# Έλεγχος ΚΕΑΟ — Πιστώσεις Οφειλέτη

Αναλυτικές οδηγίες για τον έλεγχο ΚΕΑΟ που εντάχθηκε στο E3 Brain (αρχικά
2026-06-12). Καλύπτει τη λογική του scraper, τη ροή του brain, τη σύνδεση με
τους κωδικούς **Ε3_585_007** / **Ε3_588**, και τα σημεία στη φόρμα όπου
ενεργοποιείται.

> **TL;DR** — Ένα login στο TAXISNET → KEAO ανά μέλος, διαδοχικά όλοι οι μη-
> εργοδοτικοί φορείς, ένα PDF ανά φορέα, κύρια εισφορά → 585.007, πρόσθετα
> τέλη + προσαυξήσεις → 588 (αντιπαραβολή με myDATA / Ισοζύγιο). Ο ΕΦΚΑ
> Μη Μισθωτών εξαιρείται από τα totals γιατί ήδη μετριέται στη βεβαίωση
> ΕΦΚΑ.

---

## 1) Σε ποιους εφαρμόζεται

Ο έλεγχος αφορά **μόνο φυσικά πρόσωπα που χρωστούν στο ΚΕΑΟ** ως
ασφαλισμένοι:

- **Ατομικές επιχειρήσεις** — ο ίδιος ο επιχειρηματίας έχει AMO στο ΚΕΑΟ.
- **Μέλη εταιριών** (ΟΕ, ΕΕ, ΙΚΕ μέλη, διαχειριστές κλπ.) — κάθε μέλος που
  εμφανίζεται στο `active_members` τρέχει χωριστά τον scraper με τους
  δικούς του TAXISNET κωδικούς + ΑΦΜ.

Νομικά πρόσωπα δεν έχουν προσωπικά ασφαλιστικά οφειλόμενα, οπότε ο
έλεγχος δεν τρέχει σε αυτά — η ίδια `targets[]` λίστα που χρησιμοποιεί ο
ΕΦΚΑ/ΤΕΚΑ scraper επαναχρησιμοποιείται, οπότε το routing είναι αυτόματο.

---

## 2) Φορείς που περιλαμβάνονται / εξαιρούνται

Στον registry-picker του ΚΕΑΟ ο χρήστης βλέπει συνήθως δύο ή περισσότερες
γραμμές (Ι.Κ.Α. ΕΡΓΟΔΟΤΗΣ, Ε.Φ.Κ.Α. ΜΗ ΜΙΣΘΩΤΩΝ ΑΣΦΑΛΙΣΜΕΝΟΣ, Ο.Α.Ε.Ε.
ΑΣΦΑΛΙΣΜΕΝΟΣ, ΕΤΑΑ, κλπ).

| Κανόνας | Πεδίο | Συμπεριφορά |
|---|---|---|
| Filter πριν την επεξεργασία | `forea` περιέχει `ΕΡΓΟΔΟΤ` | ✗ Skip — δεν τρέχει καθόλου ο φορέας |
| Special flag | `forea` περιέχει `ΜΗ ΜΙΣΘΩΤ` | ✓ Τρέχει — αλλά εξαιρείται από τα aggregates |
| Default | Ό,τι άλλο | ✓ Τρέχει + μετράει στα aggregates |

Η εξαίρεση του ΕΦΚΑ Μη Μισθωτών γίνεται ώστε να **μη μετριέται δύο φορές**
— οι ίδιες πληρωμές εμφανίζονται ήδη στην ετήσια βεβαίωση ΕΦΚΑ που τραβάει
ο υπάρχων `efka-extractor.py`. Παρ' όλα αυτά ο scraper εξακολουθεί να
τραβάει το PDF του φορέα ώστε ο λογιστής να μπορεί να το ελέγξει χωριστά.

Ο Ι.Κ.Α. ΕΡΓΟΔΟΤΗΣ φιλτράρεται ολοκληρωτικά γιατί αφορά εργοδοτικές
οφειλές του φυσικού προσώπου ως εργοδότη — διαφορετική φύση από τις
ασφαλιστικές εισφορές αυτοαπασχολούμενου που πάνε στο 585.007.

---

## 3) Standalone script — `e3/checks/keao-mistoton.py`

### Είσοδοι

| Flag | Περιγραφή |
|---|---|
| `--username` | TAXISNET username του μέλους/ατομικής |
| `--password` | TAXISNET password |
| `--afm` | ΑΦΜ που μπαίνει στη δεύτερη φόρμα του ΚΕΑΟ. Default το `--username` (όταν ο TAXISNET user είναι ο ίδιος ο ΑΦΜ). |
| `--date-from` | `dd/mm/yyyy` — γεμίζει το πεδίο «Από:» στις Πιστώσεις. Default `01/01/2025`. |
| `--year` | Έτος για τα `e3_585_007_year` / `e3_588_year` aggregates. Default `2025`. |
| `--output-dir` | Φάκελος που θα γραφτούν shots + per-φορέα PDFs. |
| `--output-json` | Αρχείο που θα γραφτεί το αναλυτικό αποτέλεσμα. |
| `--headed` / `--headless` | Visibility του browser. Default headless. |

### Ροή

1. **Login μία φορά** — πάει στο
   `https://www.e-efka.gov.gr/el/elektronikes-yperesies/ilektronikes-ypiresies-keao`,
   κάνει click «Είσοδος στην υπηρεσία» (popup), περνάει από TAXISNET, συμπληρώνει
   ΑΦΜ στην επόμενη φόρμα.
2. **Άνοιγμα registry picker** — κάνει click «Επιλογή Μητρώου», δέχεται και
   popup και same-tab navigation (έχει time-out fallback).
3. **Καταγραφή όλων των γραμμών** — διαβάζει `#dataTable_data tr` και
   φιλτράρει όσες περιέχουν `ΕΡΓΟΔΟΤ` στο forea.
4. **Για κάθε φορέα που πέρασε το filter**:
   - Click «Επιλογή» στη συγκεκριμένη γραμμή (matched by Α.Μ.Ο.).
   - Click «Κινήσεις Οφειλέτη» → tab «Πιστώσεις Οφειλών».
   - Συμπληρώνει `#dateFrom_input` με `--date-from`, click «Εμφάνιση».
   - **Pagination loop**: διαβάζει το `(Σελίδα X από N)` από τον bottom
     paginator. Για κάθε σελίδα:
     - Screenshot **μόνο** του «Ηλεκτρονική Καρτέλα Οφειλέτη» card (όχι
       sidebar / govgr header) — βλ. §3.1.
     - Parse rows με 8 κελιά (Κωδ. Υπ/τος, Ημ-νία, Παραστατικό, Κωδ.
       Κίνησης, Συνολικό, Κύρια Εισφορά, Πρόσθετα Τέλη, Προσαυξήσεις).
     - Click «Επόμενη σελίδα», περιμένει να αλλάξει το label.
   - Stitch όλες τις screenshots σε ένα PDF
     `keao_pistwseis_<ΦΟΡΕΑΣ>.pdf` με Pillow.
   - **Επιστροφή στον picker**: click στο sidebar
     `<a href="/eDebtor/secure/amo.xhtml">Επιλογή Μητρώου</a>` —
     η ίδια συνεδρία TAXISNET συνεχίζεται, ώστε να μη χτυπάμε
     **TAXISNET OAM-6** (όριο ταυτόχρονων συνεδριών).
5. **Aggregation** — sums per φορέα + δύο global aggregates που
   εξαιρούν τα ΜΗ ΜΙΣΘΩΤΩΝ:
   - `e3_585_007_year` = Σ(main_contrib) για όλους τους «άλλους» φορείς, μόνο rows του `--year`.
   - `e3_588_year` = Σ(extra_fees + surcharges) για τους ίδιους φορείς + έτος.

### 3.1) Καθαρό screenshot του card

Πρακτικά το `#content` του ΚΕΑΟ περιλαμβάνει sidebar + header, άρα δεν
κάνει για direct clip. Ο script χρησιμοποιεί JavaScript εκτέλεση μέσα
στη σελίδα για να βρει το **μικρότερο** wrapper που περιέχει ταυτόχρονα:

- το string `Ηλεκτρονική Καρτέλα Οφειλέτη`, και
- το `#tabView` του primefaces.

Αυτός είναι ο ζητούμενος container — title + στοιχεία οφειλέτη + tabs +
grid + σύνολα. Αν για κάποιον λόγο ο entrant DOM αλλάξει, ο script
πέφτει πίσω σε `#content` ή σε full viewport screenshot, ώστε ποτέ να μη
σπάει η ροή.

### Έξοδος JSON

```jsonc
{
  "status": "ok",
  "year": 2025,
  "date_from": "01/01/2025",
  "registries": [
    {
      "forea": "Ο.Α.Ε.Ε. ΑΣΦΑΛΙΣΜΕΝΟΣ",
      "amo": "2346014",
      "epwnymia": "ΒΑΛΛΗΝΔΡΑΣ ΚΩΝ ΝΟΣ ΙΩΑΝΝΗΣ",
      "is_efka_mh_misthwton": false,
      "status": "ok",
      "pages_captured": 2,
      "pdf": "…/keao_pistwseis_Ο.Α.Ε.Ε._ΑΣΦΑΛΙΣΜΕΝΟΣ.pdf",
      "rows": [ {"kod_yp":"444","date":"24/01/2025","doc":"…","total":135.0,…}, … ],
      "totals_year": {"total":1605.0,"main_contrib":1454.43,"extra_fees":150.57,"surcharges":0.0},
      "totals_all":  {"total":2355.0,"main_contrib":2124.37,"extra_fees":230.63,"surcharges":0.0}
    },
    { "forea":"Ε.Φ.Κ.Α. ΜΗ ΜΙΣΘΩΤΩΝ ΑΣΦΑΛΙΣΜΕΝΟΣ", "is_efka_mh_misthwton": true, … }
  ],
  "totals_year": { "total": …, … },
  "totals_all":  { "total": …, … },
  "e3_585_007_year": 1454.43,
  "e3_588_year":     150.57
}
```

### Per-registry status κωδικοί

| Status | Πότε εμφανίζεται |
|---|---|
| `ok` | Παρθηκε grid + σελίδες + PDF |
| `no_amo` | Banner «Δεν βρέθηκε Αριθμός Μητρώου Οφειλέτη» στον συγκεκριμένο φορέα |
| `no_credits` | Tab άνοιξε αλλά grid άδειο |
| `select_button_missing` | Δεν βρέθηκε button «Επιλογή» στη γραμμή — fallback flag |
| `error: …` | Ο,τιδήποτε άλλο raised — ο φορέας skip-άρεται, ο υπόλοιπος έλεγχος συνεχίζεται |

---

## 4) Brain integration — `e3/checks/e3_brain.py`

### Νέα payload fields

```jsonc
{
  "run_keao":           true,   // master switch ΚΕΑΟ check
  "download_keao_pdfs": true    // αποθήκευση των PDFs στον per-AFM φάκελο
}
```

Συνεπιδρούν με το γενικό `run_extractors` master όπως ακριβώς και τα
`run_efka_teka`/`run_misth`/`run_e9` — αν κάποιος από αυτούς είναι ορατός
ως per-flag το master γίνεται το OR τους.

### Ροή στο `process_client`

```
ΕΦΚΑ/ΤΕΚΑ scrape (per target)
        ↓
ΚΕΑΟ scrape (per target)
   • run keao-mistoton.py με --output-dir = data/<group>/keao_pdfs/<owner>/<afm>
   • parse keao_out.json
   • keao_585_007_total += e3_585_007_year
   • keao_588_total     += e3_588_year
        ↓
efka_teka_total += keao_585_007_total       ← κύρια εισφορά μπαίνει στο 585.007
        ↓
myDATA E3 fetch + mydata_585_007/014/588 sums
        ↓
Comparison messages:
   • 585.007: EFKA+TEKA+KEAO  vs  myDATA  /  Excel
   • 588:     ΚΕΑΟ extras     vs  myDATA  /  Excel
```

### Response shape — νέα κλειδιά στο `checks`

| Πεδίο | Νόημα |
|---|---|
| `keao_ran` | true όταν τρέξαμε ΚΕΑΟ τουλάχιστον μία φορά (έστω και αν τα ποσά είναι μηδέν) |
| `keao_585_007_total` | Σ(main_contrib) όλων των μη-Μη-Μισθωτών φορέων, year-filtered |
| `keao_588_total` | Σ(extra_fees + surcharges) μη-Μη-Μισθωτών φορέων |
| `mydata_588` | Σ(amount) όλων των ταξινομημένων myDATA entries με κωδικό `588` |
| `excel_588` | Στήλη `E3_588` από το Ισοζύγιο, αν εισήχθη |
| `keao_per_member` | Λίστα ανά AFM: `e3_585_007_year`, `e3_588_year`, registries[…] |

> Σημαντικό: το `efka_teka_total` που εμφανίζεται στα reports
> **περιλαμβάνει ήδη το KEAO 585.007** add-on. Η αντιπαραβολή στο
> on-screen table και στο PDF δείχνει αυτή την αθροισμένη τιμή στο πεδίο
> «Brain».

### PDF αποθήκευση

`_resolve_pdfs_dir("keao", afm)` επιστρέφει
`data/<group_data_folder>/keao_pdfs/uid_<id>/<afm>/`. Ο scraper γράφει
τα `keao_pistwseis_<ΦΟΡΕΑΣ>.pdf` απευθείας εκεί όταν είναι ενεργό το
download flag.

---

## 5) Excel import — προαιρετική στήλη `E3_588`

`e3/checks/e3_brain.py._STRICT_HEADER_ALIASES` δέχεται:

```
e3_588   ↔  {"e3_588", "588"}
```

Αν στο Ισοζύγιο σου υπάρχει στήλη με τίτλο `588` (ή `E3_588`), η τιμή
περνά στο `client["excel_values"]["E3_588"]` και η μηχανή την συγκρίνει
με το `keao_588_total`. Αν δεν υπάρχει, η σύγκριση γίνεται μόνο
έναντι myDATA.

---

## 6) Routes & UI

### REST endpoints (app.py)

| Method | Path | Σκοπός |
|---|---|---|
| GET    | `/api/e3/brain/keao_pdfs?afm=…` | Λίστα PDFs για συγκεκριμένο ΑΦΜ |
| GET    | `/api/e3/brain/keao_pdfs/file?afm=…&name=…` | Download μεμονωμένου PDF |
| DELETE | `/api/e3/brain/keao_pdfs/file?afm=…&name=…` | Διαγραφή PDF |
| POST   | `/api/e3/brain/keao_pdfs/bulk_delete` | Mass delete |

Ο `_e3_pdfs_root("keao")` mapping έγινε στο `app.py` ώστε όλα τα
generic helpers να γνωρίζουν το νέο kind.

### Φόρμα — `templates/e3_check.html`

| Στοιχείο | ID | Που εμφανίζεται |
|---|---|---|
| Checkbox ατομικού | `brainRunKeao` | Στο `Ατομικός` tab, στη γραμμή ΕΦΚΑ/ΤΕΚΑ/ΚΕΑΟ/Μισθωτήρια/Ε9 |
| Checkbox μαζικού | `brainRunKeaoBulk` | Στο `Μαζικός` tab |
| Toggle αποθήκευσης PDF | `brainDownloadKeaoPdfs` | Στο PDFs section του ατομικού (στο bulk καλύπτεται από το master switch `brainBulkSavePdfs`) |
| Chip στο PDF browser | `data-kind="keao"` | Στο unified PDFs panel — βάζει filter |

Στο comparison πίνακα ανά AFM, αν `checks.keao_ran === true`, προστίθεται
ακόμα μία γραμμή κάτω από το 585.014:

```
588 · Πρόσθετα τέλη + προσαυξήσεις (ΚΕΑΟ)  ·  Brain · myDATA · Ισοζύγιο · Διαφορές
```

Το ίδιο αποτυπώνεται στο PDF preview γιατί η εξαγωγή του χρησιμοποιεί
τον ίδιο HTML render path.

Επιπλέον στα **διαγνωστικά εκτέλεσης** (bulk view + per-AFM card)
γράφεται ξεχωριστή γραμμή:

```
ΚΕΑΟ Πιστώσεις: ✓ έτρεξε — προς 585.007: 1.454,43 € · προς 588: 150,57 €
```

και ο μετρητής `pdfs_saved` επεκτείνεται με `keao` count.

---

## 7) Επικύρωση που έχει γίνει

### Test creds A — ΓΑΛΑΝΟΠΟΥΛΟΥ ΓΕΩΡΓΙΑ, ΑΦΜ 050058120
- TAXISNET: `050058120 / Galan0500581!`
- Registries που εντοπίστηκαν: μόνο Ε.Φ.Κ.Α. Μη Μισθωτών (1 φορέας).
- Αποτέλεσμα: `is_efka_mh_misthwton: true`, PDF γράφτηκε για audit, αλλά
  `e3_585_007_year = 0` / `e3_588_year = 0` — σωστά, γιατί ήδη
  καλύπτεται από τη βεβαίωση ΕΦΚΑ.

### Test creds B — ΒΑΛΛΗΝΔΡΑΣ ΚΩΝ/ΝΟΣ ΙΩΑΝΝΗΣ, ΑΦΜ 043813926
- TAXISNET: `WW551078U749 / BALL11`
- Registries που εντοπίστηκαν:
  - Ο.Α.Ε.Ε. ΑΣΦΑΛΙΣΜΕΝΟΣ (AMO 2346014) — counted
  - Ε.Φ.Κ.Α. Μη Μισθωτών (AMO 3278081) — flagged, PDF μόνο
  - Ι.Κ.Α. ΕΡΓΟΔΟΤΗΣ — φιλτράρεται **πριν** την επεξεργασία
- 2025-only aggregates:
  - `e3_585_007_year = 1.454,43` (Ο.Α.Ε.Ε. κύρια εισφορά)
  - `e3_588_year = 150,57` (Ο.Α.Ε.Ε. πρόσθετα τέλη)

---

## 8) Πιθανά σημεία αποτυχίας & troubleshooting

| Σύμπτωμα | Πιθανή αιτία | Διόρθωση |
|---|---|---|
| `OAM-6` στο TAXISNET | Παράλληλα running brain instances ή stale tab με ίδιο user | Κλείσε open sessions ή περίμενε ~10 λεπτά. Ο νέος ΚΕΑΟ scraper κάνει **ένα login** και κρατάει την ίδια συνεδρία για όλους τους φορείς, άρα ο ίδιος ο έλεγχος **δεν** προκαλεί OAM-6. |
| `status: no_credits` σε όλους τους φορείς | Έλειπε ή λάθος ΑΦΜ στη φόρμα δεύτερου σταδίου | Βεβαιώσου ότι το `--afm` ταιριάζει με τον TAXISNET user. Για ατομικές συνήθως είναι το ίδιο. |
| PDF περιέχει sidebar | Άλλαξε το DOM του ΚΕΑΟ — δεν βρέθηκε wrapper που να περιέχει `Ηλεκτρονική Καρτέλα Οφειλέτη` + `#tabView` | Άνοιξε debug screenshot στο `<output-dir>/shots/` και άλλαξε το `_CARD_JS` selector στο `keao-mistoton.py`. Ο fallback σε `#content` διατηρεί τη ροή. |
| Διαφορά στο 588 με myDATA | Είτε ο λογιστής δεν χαρακτήρισε ως 588 στη myDATA όλους τους ΚΕΑΟ-πρόστιμα, είτε υπάρχουν 588 entries άλλης φύσης (πχ διοικητικά πρόστιμα) | Ο πίνακας του brain δείχνει και τα δύο νούμερα — η ανθρώπινη απόφαση πέφτει στον λογιστή. |
| Διπλή καταμέτρηση ΕΦΚΑ | Σπάνιο — μόνο αν αλλάξει το όνομα του φορέα στο ΚΕΑΟ και δεν περιέχει πια το `ΜΗ ΜΙΣΘΩΤ` | Ενημέρωσε το `EFKA_MH_MISTHWTWN_TOKENS` στο `keao-mistoton.py`. |

---

## 9) Σχετικά αρχεία

| Αρχείο | Ρόλος |
|---|---|
| `e3/checks/keao-mistoton.py` | Standalone Playwright scraper + PDF stitcher |
| `e3/checks/e3_brain.py` | Brain orchestration — `_flag_for("keao")`, per-target loop, aggregation, 588 reconciliation |
| `app.py` | REST routes `/api/e3/brain/keao_pdfs*`, `_e3_pdfs_root("keao")` mapping |
| `templates/e3_check.html` | UI checkboxes, comparison table 588 row, διαγνωστικά, PDF browser chip |
| `docs/E3_KEAO_CHECK_NOTES.md` | Αυτό το αρχείο |
