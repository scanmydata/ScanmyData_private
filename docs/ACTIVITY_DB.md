# Activity log σε SQLite (activity.db)

**Γιατί:** το `data/<ομάδα>/activity.log` (NDJSON) έφτανε εκατοντάδες MB και κάθε «τελευταία λήψη», admin dashboard ή
λίστα δραστηριότητας το διάβαζε ολόκληρο (λεπτά, κόλλαγε τους workers).

**Τι αλλάζει**
- Νέες εγγραφές (`drive_log_activity`, `_append_group_log`) γράφονται στο `data/<ομάδα>/activity.db` (WAL, ευρετήρια σε χρόνο / ενέργεια / ΑΦΜ).
  Αν η εγγραφή στη βάση αποτύχει, πέφτει σε append στο `activity.log` (και εισάγεται αργότερα από το `ensure_synced`).
- Αναγνώστες που χρησιμοποιούν τη βάση: `get_last_fetch_date`, `admin_get_activity_logs` (ομάδα και όλες οι ομάδες),
  καθαρισμός logs (admin). Χρησιμοποιούν τη βάση ΜΟΝΟ όταν η μετάβαση του ιστορικού έχει ολοκληρωθεί (`is_ready`)·
  μέχρι τότε δουλεύει η παλιά ροή.
- **Μετάβαση:** αυτόματα στο background στην εκκίνηση (`_start_activity_db_migration`, ένας worker, κλείδωμα φακέλου,
  απενεργοποίηση με `ACTIVITY_DB_MIGRATE=0`) ή χειροκίνητα: `python scripts/migrate_activity_logs_to_db.py [--group X] [--status]`.
  Μη καταστροφική και idempotent: το `activity.log` ΔΕΝ σβήνεται· διπλές γραμμές (ίδιο περιεχόμενο) εισάγονται μία φορά.
- **Backup:** η βάση είναι τοπική και ΔΕΝ ανεβαίνει ως αρχείο στο Drive· το off-site αντίγραφο παραμένει το κυλιόμενο
  απομακρυσμένο `activity.log` που συνεχίζει να γράφει το `drive_log_activity`. Δεν περιλαμβάνεται στα zip backup ομάδας.
- Μετά την επιβεβαίωση ότι η βάση έχει όλο το ιστορικό (`--status`), το μεγάλο `activity.log` μπορεί να αρχειοθετηθεί/σβηστεί χειροκίνητα.

Δοκιμή σε αντίγραφο του πραγματικού log 244MB: 7.624 γραμμές (πολλές διπλές) → 2.605 εγγραφές, βάση 22MB, ~1s.

## Συμπλήρωμα 2026-10-13 — ΟΛΑ τα logs σε SQL

| Τι | Πριν | Τώρα |
|---|---|---|
| Audit ομάδας (login, fetch, save_invoice…) | `data/<ομάδα>/activity.log` | `data/<ομάδα>/activity.db` (`activity_store.py`) |
| Log ανάκτησης Ε3 | `data/<ομάδα>/e3_brain_retrieval_log.jsonl` | ίδια βάση, `action=e3_brain_retrieval` |
| Logging εφαρμογής (INFO+, WARNING+) | `data/activity.log`, `data/error.log` (RotatingFileHandler) | `data/app_log.db` (`app_log_store.py`, ουρά + background writer) |

- **Logging εφαρμογής:** `SQLiteLogHandler` (μη μπλοκάρον). Το Console (stdout/docker) μένει πάντα. `APP_LOG_FILES=1` κρατά και τα
  αρχεία παράλληλα. Retention `APP_LOG_RETENTION_DAYS` (προεπιλογή 90), καθαρισμός στην εκκίνηση και ανά 24ωρο.
  Το admin γράφημα «firebase.read bytes» διαβάζει από τη βάση (ευρετήριο) αντί για ολόκληρο αρχείο.
- **Μετάβαση παλιών:** `python scripts/migrate_logs_to_sql.py [--backup-pattern "data/_drive_backup_YYYYMMDD_{group}"]`
  (μη καταστροφικό, idempotent· παραλείπει δυαδικά «error.log» και διπλότυπα αντίγραφα του Drive).
  Κατάσταση: `--status`.
- **Λήψη backup από Drive:** `scratchpad/drive_pull_all.py` (διαβάζει μόνο από Drive, δεν γράφει πίσω) → `data/_drive_backup_<ημερομηνία>_<ομάδα>`.
- **Σημ.:** το `error.log` της ομάδας `test` στο Drive (42MB) είναι δυαδικό (journal SQLite), όχι log — δεν εισάγεται.

## Λήψη παραστατικών — γιατί «κόλλαγε» όλη η εφαρμογή
Κάθε παραστατικό/summary της λήψης διάβαζε ΟΛΟ το `{vat}_invoices.json`/`{vat}_summary.json`, έψαχνε και το ξαναέγραφε
(indent + fsync): για 1000+ παραστατικά χιλιάδες parse/dump αρχείων MB μέσα στο GIL → όλοι οι workers στέκονταν.
`customer_file_batch()` (app.py) φορτώνει κάθε αρχείο **μία** φορά, εφαρμόζει τους ίδιους κανόνες merge/dedupe στη μνήμη και
γράφει **μία** φορά στο τέλος (και σε σφάλμα). Δοκιμή 1.500 παραστατικών: 26s/1.714 εγγραφές → 0,6s/2 εγγραφές, ίδιο αποτέλεσμα.
