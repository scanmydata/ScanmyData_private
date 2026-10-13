# Παραστατικά σε SQLite (`invoice_store.py`)

Τα παραστατικά κάθε πελάτη ζουν πλέον σε **μία βάση SQLite ανά ομάδα**: `data/<ομάδα>/invoices.db`.
Αντικαθιστά τη χρήση των JSON ως «βάσης» (ανάγνωση ολόκληρου αρχείου για κάθε ερώτηση).

| αρχείο (export) | kind | περιεχόμενο |
|---|---|---|
| `<ομάδα>/<ΑΦΜ>_invoices.json` | `invoices` | πλήρη παραστατικά της λήψης myDATA |
| `<ομάδα>/<ΑΦΜ>_summary.json` | `summary` | summaries λήψης / αποδείξεων |
| `<ομάδα>/epsilon/<ΑΦΜ>_epsilon_invoices.json` | `epsilon` | καταχωρημένα στη λίστα (γραμμές, κατηγορίες) |

Δεν διαχειρίζονται (μένουν αρχεία): το legacy `epsilon_invoices.json` της ομάδας (σκιά), αρχεία στη ρίζα `data/`
ή στο `data/epsilon/` (χωρίς ομάδα), φάκελοι `_…`/`.…`.

## Μοντέλο

* **Η βάση είναι η πηγή αλήθειας** για αναγνώσεις/εγγραφές της εφαρμογής (`json_read`, `json_write`,
  `_write_json_atomic`, `_save_json`, `_load_json`, `load_epsilon_cache_for_vat` περνούν από το store).
* **Το JSON αρχείο γράφεται ΣΥΓΧΡΟΝΑ από τη βάση** (atomic, μέσα στην ίδια συναλλαγή) ως *export*: το Drive, τα
  αντίγραφα ασφαλείας, οι γέφυρες epsilon και τα scripts συνεχίζουν να βλέπουν αρχεία χωρίς καμία αλλαγή.
* Αν το αρχείο **αλλάξει εξωτερικά** (Drive pull, επαναφορά backup, script) εντοπίζεται από την υπογραφή του
  (mtime, μέγεθος) και εισάγεται στη βάση πριν από την επόμενη ανάγνωση.
* Εγγραφή = **diff**: μόνο οι γραμμές που άλλαξαν γράφονται (πρόσθεση παραστατικού = ένα INSERT).
* Αποτυχία εγγραφής αρχείου → ROLLBACK της βάσης + εξαίρεση (όπως πριν): ποτέ βάση και αρχείο σε διαφορετική κατάσταση.
* Φθαρμένη βάση → μπαίνει στην άκρη (`invoices.db.corrupt-<ώρα>`) και ξαναχτίζεται από τα JSON.
* Στο **Drive** ταξιδεύει η βάση (βλ. «Drive» παρακάτω)· στο backup zip μπαίνουν τα JSON export.
* Απενεργοποίηση: `INVOICE_STORE=0` → όλα όπως πριν (μόνο αρχεία)· `INVOICE_DRIVE_DB=0` → η βάση μένει τοπική και το
  Drive συγχρονίζει JSON όπως πριν.

## Σχήμα

```
stores(kind, vat, shape, raw, n, version, updated_at, file_mtime_ns, file_size, export_dirty)  PK(kind, vat)
docs(id, kind, vat, pos, mark, issue_date, issue_iso, issuer_vat, issuer_name, counterpart_vat, aa, doc_type,
     classification, class_l, total, is_receipt, h, data)       UNIQUE(kind, vat, pos)
ευρετήρια: mark · (issuer_vat, issue_iso) · (kind, vat, class_l)
```

`data` = ολόκληρη η αρχική εγγραφή (JSON, ίδια κλειδιά/σειρά). Οι στήλες είναι εξαγόμενες για ερωτήματα.

## Ερωτήματα (νέα δυνατότητα)

```python
import invoice_store as S
S.find_by_issuer(group_dir, "094187996", kind="summary", vat=client_vat, date_from="2026-01-01")
S.find_by_mark(group_dir, "400012145709824")
S.suppliers(group_dir, vat=client_vat, kind="summary")         # προμηθευτές με πλήθος/σύνολο
S.docs_where(group_dir, "summary", vat, classification="αχαρακτηριστο")
S.marks_of(group_dir, "epsilon", vat)
S.issuer_for(group_dir, mark="…", aa="…")                       # ΑΦΜ εκδότη από MARK/ΑΑ
```

HTTP: `GET /api/store/suppliers?kind=summary` και
`GET /api/store/supplier_docs?supplier=<ΑΦΜ>&kind=summary|invoices|epsilon&from=YYYY-MM-DD&to=YYYY-MM-DD&limit=N`
(για τον ενεργό πελάτη της συνεδρίας).

Ήδη σε ερωτήματα (αντί για ανάγνωση ολόκληρων αρχείων): `_unchar_collect` (πλήθος/λίστα αχαρακτήριστων) και
`_find_afm_in_epsilon`.

## Μετάβαση

* Αυτόματη στην εκκίνηση (background thread, κλείδωμα `data/.invoice_migrate.lock`), και «τεμπέλικη» στην πρώτη
  ανάγνωση κάθε αρχείου. `INVOICE_STORE_MIGRATE=0` απενεργοποιεί μόνο τη μαζική εισαγωγή στην εκκίνηση.
* Χειροκίνητα: `python scripts/migrate_invoices_to_sql.py [--group G] [--status|--verify|--rebuild]`.
* Μη καταστροφική: τα JSON δεν αλλάζουν ούτε σβήνονται. Idempotent.
* Μετά από Drive pull και επαναφορά backup γίνεται αυτόματα `sync_group`.

## Διαγραφή δεδομένων

* Η εφαρμογή (`delete_customer_data_files`) σβήνει τα αρχεία και καλεί `invoice_store.forget(path)`: γράφεται
  **ταφόπλακα** (`tombstones`) που ταξιδεύει με το snapshot ώστε η συγχώνευση με το Drive να μην αναστήσει τα δεδομένα.
  Ταφόπλακα ισχύει μόνο αν δεν υπάρξει νεότερη εγγραφή του store (τότε το store «ξαναγεννιέται»). Λήγουν σε 180 ημέρες.
* Αρχείο που λείπει **χωρίς** `forget()` (χειροκίνητο σβήσιμο, καθαρισμός φακέλου) ΔΕΝ θεωρείται διαγραφή:
  ξαναγράφεται από τη βάση. Έτσι ένα «σβήσε τα JSON για να ξανακατεβούν από το Drive» δεν σβήνει δεδομένα από το Drive.
  Για πραγματική διαγραφή από script: `os.remove(path)` + `invoice_store.forget(path)`.

## Drive: κρυπτογραφημένη `invoices.db`

* **Push** (ανά ομάδα): αφού εισαχθούν τα τοπικά JSON που άλλαξαν εξωτερικά και συγχωνευθεί η απομακρυσμένη βάση,
  ανεβαίνει **ένα snapshot** `invoices.db`: χωρίς ευρετήρια, VACUUM, zlib (tony: 26 MB JSON → 4,7 MB), Fernet.
  Ανεβαίνει μόνο όταν το δακτυλικό αποτύπωμα της βάσης άλλαξε από το τελευταίο push (έλεγχος μεγέθους στο upload).
  Τα `{ΑΦΜ}_invoices/_summary/epsilon/…` JSON **δεν** ανεβαίνουν πια και τα παλιά αντίγραφά τους στο Drive
  πηγαίνουν στα **απορρίμματα** (ανακτώνται ~30 ημέρες).
* **Pull**: κατεβαίνει η βάση και συγχωνεύεται **ανά store**: κερδίζει το νεότερο `updated_at` (ώρα του
  περιεχομένου — στις εισαγωγές το mtime του αρχείου)· ποτέ αντικατάσταση της τοπικής βάσης και ποτέ σβήσιμο
  τοπικού store. Τα store που αλλάζουν ξαναγράφονται και ως JSON export (τα εργαλεία βλέπουν αρχεία).
  Ίδια σημασιολογία με το προηγούμενο «τελευταίο αρχείο κερδίζει», αλλά ανά πελάτη/είδος.
* **Fallbacks**: Drive χωρίς `invoices.db` (παλιά κατάσταση) → κατεβαίνουν/εισάγονται τα JSON όπως πριν·
  αποτυχία upload βάσης → ανεβαίνουν τα JSON όπως πριν· φάκελοι αντιγράφων (`_…`) και scripts χωρίς `configure()`
  δουλεύουν (pull παράγει JSON export).
* Όλοι οι servers/μηχανές πρέπει να έχουν τη νέα έκδοση: ένας παλιός κώδικας που ανεβάζει JSON δεν
  συγχρονίζεται πια με τους νέους.
* Έλεγχοι: `python tools/test_invoice_drive.py` (προσομοίωση Drive με δύο μηχανές).

### Μία φορά, ελεγχόμενα (προαιρετικό)

Ο κανονικός συγχρονισμός κάνει τη μετάβαση αυτόματα στο πρώτο push. Για ελεγχόμενη εκτέλεση με επαλήθευση:

```
python scripts/drive_migrate_invoices.py                 # dry-run
python scripts/drive_migrate_invoices.py --apply [--group G]
```

Κατεβάζει πρώτα τα νεότερα απομακρυσμένα JSON, εισάγει, συγχωνεύει, ανεβάζει, **επαληθεύει** (λήψη/αποκρυπτογράφηση/
πλήθος ανά είδος) και μόνο τότε στέλνει τα παλιά JSON στα απορρίμματα.

**Σειρά deploy**: πρώτα ο server (έχει τα πιο πρόσφατα δεδομένα) — το πρώτο του push ανεβάζει τη βάση. Αν τρέξει
πρώτα το script από άλλη μηχανή, κατεβάζει πρώτα τα νεότερα JSON, άρα δεν χάνεται δουλειά.
