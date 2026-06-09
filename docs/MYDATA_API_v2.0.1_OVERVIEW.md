# myDATA REST API — Επισκόπηση v2.0.1

> Έκδοση εγγράφου ΑΑΔΕ: **2.0.1 — Μάρτιος 2026**
> Πηγή: `myDATA API Documentation v2.0.1_official_erp.pdf` (121 σελίδες)
> Σύνδεσμος επίσημης τεχνικής τεκμηρίωσης: <https://www.aade.gr/epiheiriseis/mydata-ilektronika-biblia-aade/tehnikes-prodiagrafes-ekdoseis>

Πρόκειται για την τεχνική περιγραφή των REST API διεπαφών για διαβίβαση & λήψη δεδομένων από/προς myDATA (Ηλεκτρονικά Βιβλία ΑΑΔΕ). Αυτό το έγγραφο είναι ο γρήγορος οδηγός χρήσης μέσα στο ScanmyData — πλήρεις λεπτομέρειες στο PDF.

---

## 1. Authentication & Base URLs

**Production:** `https://mydatapi.aade.gr/myDATA/<method>`
**Development/Test:** `https://mydataapidev.aade.gr/<method>` (χωρίς `/myDATA/`)

Κάθε κλήση πρέπει να στέλνει **HTTPS** με τα παρακάτω headers:

| Header | Τύπος | Περιγραφή |
| --- | --- | --- |
| `aade-user-id` | string | Όνομα χρήστη του REST API account |
| `ocp-apim-subscription-key` | string | Subscription key (Κωδικός API) |

Εγγραφή χρήστη γίνεται από <https://www1.aade.gr/saadeapps2/bookkeeper-web> (TaxisNet credentials). Από εκεί παίρνεις το subscription key.

> Στο codebase μας χρησιμοποιείται το όνομα `Ocp-Apim-Subscription-Key` (camel-case με dashes). Δες [fetch_e3.py:113-116](../e3/checks/fetch_e3.py).

---

## 2. Σύνοψη όλων των μεθόδων

| Μέθοδος | HTTP | Σκοπός |
| --- | --- | --- |
| **SendInvoices** | POST | Υποβολή ενός ή περισσότερων παραστατικών (περιλαμβανομένων διορθωτικών/τροποποιητικών) |
| **SendIncomeClassification** | POST | Υποβολή χαρακτηρισμών εσόδων σε ήδη υποβεβλημένα παραστατικά |
| **SendExpensesClassification** | POST | Υποβολή χαρακτηρισμών εξόδων σε ήδη υποβεβλημένα παραστατικά |
| **SendPaymentsMethod** | POST | Υποβολή τρόπων πληρωμής για ήδη υποβεβλημένα παραστατικά |
| **CancelInvoice** | POST | Ακύρωση παραστατικού χωρίς νέα υποβολή |
| **RequestDocs** | GET | Λήψη παραστατικών/χαρακτηρισμών/ακυρώσεων που υπέβαλαν **άλλοι** χρήστες για εμάς |
| **RequestTransmittedDocs** | GET | Λήψη παραστατικών/χαρακτηρισμών/ακυρώσεων που υποβάλαμε **εμείς** |
| **RequestMyIncome** | GET | Πληροφορίες εσόδων του χρήστη για περίοδο |
| **RequestMyExpenses** | GET | Πληροφορίες εξόδων του χρήστη για περίοδο |
| **RequestVatInfo** | GET | Λεπτομέρειες εισροών/εκροών ΦΠΑ ανά τιμολόγιο ή ανά ημέρα |
| **RequestE3Info** | GET | Λεπτομέρειες Ε3 ανά τιμολόγιο ή ανά ημέρα — *κύρια χρήση μας* |
| **RegisterTransfer** | POST | Έναρξη/μεταφόρτωση διακίνησης από μεταφορέα (Ψηφιακό Δελτίο Αποστολής) |
| **ConfirmDeliveryOutcome** | POST | Δήλωση αποτελέσματος παράδοσης από μεταφορέα ή λήπτη |
| **RejectDeliveryNote** | POST | Ολική απόρριψη διακίνησης από λήπτη |
| **GetDeliveryNoteStatus** | GET | Κατάσταση/ιστορικό Δελτίου Αποστολής |
| **GenerateGroupQRCode** | POST | Δημιουργία ομαδικού QR Code για πολλαπλά Δελτία Αποστολής |
| **RequestGroupQRDetails** | GET | Ανάκτηση επιμέρους QR Codes ενός Group QR |

> Οι μέθοδοι διακίνησης (`RegisterTransfer`, `ConfirmDeliveryOutcome`, `RejectDeliveryNote`, `GetDeliveryNoteStatus`, `GenerateGroupQRCode`, `RequestGroupQRDetails`) τεκμηριώνονται **ξεχωριστά** στο "Τεχνική περιγραφή διεπαφών REST API για το Ψηφιακό Δελτίο Αποστολής".

---

## 3. Αναλυτικές μέθοδοι (αυτές που έχουν σημασία για εμάς)

### 3.1 SendInvoices — POST `/SendInvoices`
Στέλνει XML `InvoicesDoc` με ένα ή περισσότερα παραστατικά (`AadeBookInvoiceType`).
Δομή παραστατικού περιγράφεται στο κεφάλαιο 5 του PDF (Στοιχεία οντότητας → Επικεφαλίδα → Γραμμές → Σύνολα → Περίληψη).

Σε επαναϋποβολή με ίδια αναγνωριστικά (`uid`/`mark`), η νεότερη γίνεται έγκυρη και η προηγούμενη ακυρώνεται αυτόματα.

### 3.2 SendIncomeClassification — POST `/SendIncomeClassification`
Body: ένα ή περισσότερα `InvoiceIncomeClassificationType`.

| Πεδίο | Τύπος | Υποχρ. | Σημείωση |
| --- | --- | --- | --- |
| `invoiceMark` | long | Ναι | Μ.ΑΡ.Κ του παραστατικού |
| `classificationMark` | long | Όχι | Συμπληρώνεται από την υπηρεσία |
| `entityVatNumber` | string | Όχι | Μόνο αν καλείται από τρίτο (λογιστής κλπ) |
| `transactionMode` | int | Ναι (choice) | `1`=Reject, `2`=Deviation |
| `lineNumber` | int | Ναι (choice) | Γραμμή του παραστατικού |
| `incomeClassificationDetailData` | IncomeClassificationType | Ναι (choice) | Στοιχεία χαρακτηρισμού |

### 3.3 SendExpensesClassification — POST `/SendExpensesClassification`
Παρόμοιο με το income. Επιπλέον πεδίο:
- `postPerInvoice` (bool, optional): `true` σημαίνει υποβολή χαρακτηρισμών εξόδων σε επίπεδο **παραστατικού** (όχι ανά γραμμή). Οδηγίες: <https://www.aade.gr/sites/default/files/2023-07/SendExpensesClassificationPostPerInvoiceGuidelines.pdf>

### 3.4 SendPaymentsMethod — POST `/SendPaymentsMethod`
Στέλνει `PaymentMethodType`. Απαιτείται τουλάχιστον ένας τρόπος τύπου POS. Άθροισμα `amount` πρέπει να ισούται με `totalGrossValue` του παραστατικού.

### 3.5 CancelInvoice — POST `/CancelInvoice?mark={mark}[&entityVatNumber]`
Δεν απαιτεί XML body. Σε επιτυχία επιστρέφει νέο mark (της ακύρωσης).

### 3.6 RequestDocs — GET `/RequestDocs?mark={mark}[&dateFrom][&dateTo][&entityVatNumber][&counterVatNumber][&invType][&maxMark][&nextPartitionKey][&nextRowKey]`
Παραστατικά/χαρακτηρισμοί/ακυρώσεις που υπέβαλαν **άλλοι** για εμάς, με Μ.ΑΡ.Κ > `mark` παράμετρο.

**Pagination:** όταν τα αποτελέσματα ξεπερνούν το όριο, η απάντηση περιέχει `nextPartitionKey` + `nextRowKey` που τα στέλνεις πίσω στην επόμενη κλήση.

### 3.7 RequestTransmittedDocs — GET `/RequestTransmittedDocs?...`
Ίδιες παράμετροι με RequestDocs αλλά επιστρέφει ό,τι έχουμε υποβάλει **εμείς**.

### 3.8 RequestMyIncome — GET `/RequestMyIncome?dateFrom&dateTo[...&counterVatNumber][...&entityVatNumber][...&invType][&nextPartitionKey][&nextRowKey]`
Παράμετροι ημερομηνιών **υποχρεωτικές** σε μορφή `dd/MM/yyyy`.

### 3.9 RequestMyExpenses — GET `/RequestMyExpenses?...`
Ίδιο interface με RequestMyIncome, για έξοδα.

### 3.10 RequestVatInfo — GET `/RequestVatInfo?entityVatNumber&dateFrom&dateTo[&GroupedPerDay][&nextPartitionKey][&nextRowKey]`
Λεπτομέρειες ΦΠΑ ανά τιμολόγιο ή ανά ημέρα.

- `GroupedPerDay`: `true` ομαδοποιεί ανά ημέρα, `false` (default) επιστρέφει ανά τιμολόγιο
- Όταν `GroupedPerDay=false`, τα `nextPartitionKey/nextRowKey` **αγνοούνται** (δεν χρειάζονται για ομαδοποίηση)

### 3.11 RequestE3Info — GET `/RequestE3Info?entityVatNumber&dateFrom&dateTo[&GroupedPerDay][&nextPartitionKey][&nextRowKey]`
> ⚠️ **Κύρια μέθοδος για το `/api/e3/fetch` και τον Έλεγχο Ε3.** Δες ξεχωριστή ενότητα 4 πιο κάτω.

Επιστρέφει στοιχεία Ε3 για περίοδο. dateFrom/dateTo υποχρεωτικά σε `dd/MM/yyyy`. Pagination όπως στις άλλες request methods.

**Response shape (XML, με βάση ζωντανή κλήση):**
```xml
<E3Info>
  <V_Afm>036209456</V_Afm>
  <V_Mark>400008179126509</V_Mark>
  <IssueDate>2025-01-02T00:00:00</IssueDate>
  <V_Class_Category>category2_1</V_Class_Category>
  <V_Class_Type>E3_102_001</V_Class_Type>
  <V_Class_Value>49.15</V_Class_Value>
</E3Info>
```

> ❗ Το `V_Class_Category` επιστρέφεται με **codes** (`category1_*`, `category2_*`) **όχι με ελληνικά strings**. Εξαίρεση: όταν το παραστατικό είναι μη χαρακτηρισμένο, η κατηγορία γυρνά ως `ΜΗ ΧΑΡΑΚΤΗΡΙΣΜΕΝΑ ΕΞΟΔΑ` (πραγματικό ελληνικό string). Αυτή η ασυνέπεια πρέπει να λαμβάνεται υπόψη όταν φιλτράρουμε με `cat.startswith("ΜΗ")`.

### 3.12 Μέθοδοι Διακίνησης (RegisterTransfer, ConfirmDeliveryOutcome, RejectDeliveryNote, GetDeliveryNoteStatus, GenerateGroupQRCode, RequestGroupQRDetails)
Ξεχωριστό PDF (δες σύνδεσμο στην αρχή). Δεν τα χρησιμοποιούμε σήμερα στο ScanmyData.

---

## 4. Πρακτικά για RequestE3Info — οδηγίες χρήσης

Αυτή είναι η μέθοδος που τροφοδοτεί το **Πίνακα Έλεγχου Ε3** της εφαρμογής (`/e3_check`, `/api/e3/fetch`).

### 4.1 Format ημερομηνιών
AADE δέχεται **`dd/MM/yyyy`**. Αν περάσεις ISO `yyyy-mm-dd` *δεν* επιστρέφει σφάλμα — γυρνά **0 entries σιωπηρά**. Δες σχόλιο στο [e3_brain.py:2113](../e3/checks/e3_brain.py).

### 4.2 Pagination
Όταν τα results ξεπερνούν το page limit, η απάντηση περιέχει είτε:
- ζεύγος `nextPartitionKey` + `nextRowKey` (παλαιό σχήμα), ή
- `nextPartitionToken` (νεότερο σχήμα).

**Πρέπει** να ξαναζητείς μέχρι να μη γυρίσει κανένα cursor.

### 4.3 ⚠️ ΓΝΩΣΤΟ BUG ΤΗΣ AADE: pagination overlap σε μεγάλα ranges

> **Επιβεβαιωμένο στο dataset ΛΟΥΓΑΡΗΣ 2025 (ΑΦΜ 036209456) στις 06/2026.**

Όταν τραβάμε ολόκληρο έτος μονομιάς (`01/01/2025-31/12/2025`), το AADE επιστρέφει τα ίδια `(V_Mark, V_Class_Type, V_Class_Value, V_Class_Category)` σε **>1 σελίδες paging**. Για το ΛΟΥΓΑΡΗΣ:

- Page 4: first mark = `400011925882593`
- Page 5: first mark = `400011925882593` ← ίδιο!

Επίσης βρέθηκαν 58 cross-page duplicates στα 4469 entries, όλα **diff-page**, κανένα same-page.

#### Πώς το διορθώνουμε

- **Σωστή στρατηγική:** fetch **ανά τρίμηνο** (ή και ανά μήνα) και dedup μόνο μέσα στο range. Τα duplicates χάνονται φυσικά.
- **Λάθος στρατηγική:** yearly fetch + dedup με tuple `(mark, type, amount, cat)` — μπορεί να σκοτώσει **νόμιμα** line items του ίδιου τιμολογίου που τυχαίνει να έχουν ίδιο ποσό (το API δεν επιστρέφει line index στο E3Info).

#### Validation με το AADE PDF (ΛΟΥΓΑΡΗΣ 2025)

| Sub-code | Yearly+dedup | Quarterly+dedup | AADE PDF |
| --- | ---: | ---: | ---: |
| 561.001 | 3.754,40 | **3.802,37** | 3.802,37 |
| 561.003 | 307.930,55 | **309.323,14** | 309.323,14 |
| 102.001 | 261.192,45 | **264.181,30** | 264.181,30 |
| 581.001 | 15.099,09 | 15.099,09 | 15.099,09 |
| 585.011 | 7.513,18 | **7.762,45** | 7.762,45 |
| 585.016 | 6.614,55 | **6.854,96** | 6.854,96 |

Με quarterly fetch, **όλα** τα κωδικά ταιριάζουν στο cent. Με yearly fetch, χάνουμε ~1.440€ στο 561 και ~2.989€ στο 102 από aggressive dedup.

### 4.4 Σήμανση μη-χαρακτηρισμένων
- **Έσοδα:** αν χαρακτηρίζονται normal → `V_Class_Category` = `category1_*`. Αν είναι αχαρακτήριστο → string `ΜΗ ΧΑΡΑΚΤΗΡΙΣΜΕΝΑ ΕΣΟΔΑ`.
- **Έξοδα:** classified → `category2_*`. Αχαρακτήριστα → string `ΜΗ ΧΑΡΑΚΤΗΡΙΣΜΕΝΑ ΕΞΟΔΑ`.

Σύμφωνα με την επίσημη προσυμπλήρωση Ε3 από AADE: **όλα τα αχαρακτήριστα έξοδα μεταφέρονται στον κωδικό 585.016** ("Λοιπά έξοδα"). Στην εφαρμογή τα κρατάμε σε ξεχωριστή λίστα `unclassified_invoices` ώστε ο χρήστης να ξέρει τι έχει χαρακτηριστεί από AADE και τι όχι.

### 4.5 Mapping V_Class_Type → κωδικός Ε3
Το `V_Class_Type` έχει μορφή `E3_<code>_<sub>` (π.χ. `E3_585_007`). Στο codebase το regex βρίσκεται στο [fetch_e3.py:16](../e3/checks/fetch_e3.py): `E3_(\d{3})(?:_(\d{3}))?`.

Πλήρης χάρτης κωδικών στο [e3/e3_field_map.py](../e3/e3_field_map.py).

---

## 5. Pagination patterns — πώς τα χειριζόμαστε

| API | Cursor fields | Σημείωση |
| --- | --- | --- |
| RequestDocs | `nextPartitionKey` + `nextRowKey` | πάντα ζεύγος |
| RequestTransmittedDocs | `nextPartitionKey` + `nextRowKey` | πάντα ζεύγος |
| RequestMyIncome | `nextPartitionKey` + `nextRowKey` | |
| RequestMyExpenses | `nextPartitionKey` + `nextRowKey` | |
| RequestVatInfo | `nextPartitionKey` + `nextRowKey` | αγνοούνται όταν `GroupedPerDay=false` |
| RequestE3Info | `nextPartitionKey` + `nextRowKey` **ή** `nextPartitionToken` | δεκτά και τα δύο σχήματα — δες [fetch_e3.py:228-250](../e3/checks/fetch_e3.py) |

**Pseudo-code** για ασφαλές pagination:
```python
while True:
    resp = GET(url, params)
    process(resp)
    cursors = extract_cursors(resp)
    if cursors.nextPartitionKey or cursors.nextRowKey:
        params.pop('nextPartitionToken', None)
        params['nextPartitionKey'] = cursors.nextPartitionKey
        params['nextRowKey'] = cursors.nextRowKey
        continue
    if cursors.nextPartitionToken:
        params.pop('nextPartitionKey', None)
        params.pop('nextRowKey', None)
        params['nextPartitionToken'] = cursors.nextPartitionToken
        continue
    break
```

---

## 6. Παραρτήματα του PDF (Παρ. 8)

Στο PDF υπάρχουν χρήσιμοι πίνακες κωδικοποίησης. Όταν χρειαστείς λεπτομέρειες, πήγαινε στη σχετική σελίδα:

| Παρ. | Πίνακας | Σελ. |
| --- | --- | --- |
| 8.1 | Είδη παραστατικών (invType codes) | 88 |
| 8.2 | Κατηγορίες ΦΠΑ | 93 |
| 8.3 | Αιτίες εξαίρεσης ΦΠΑ | 93 |
| 8.4 | Παρακρατούμενοι Φόροι | 95 |
| 8.5 | Λοιποί Φόροι | 95 |
| 8.6 | Συντελεστές Ψηφιακού Τέλους | 96 |
| 8.7 | Τέλη | 97 |
| 8.8 | Κατηγορίες Χαρακτηρισμού **Εσόδων** | 97 |
| 8.9 | Τύποι Χαρακτηρισμού **Εσόδων** | 99 |
| 8.10 | Κατηγορίες Χαρακτηρισμού **Εξόδων** | 100 |
| 8.11 | Τύποι Χαρακτηρισμού **Εξόδων** | 101 |
| 8.12 | Τρόποι Πληρωμής | 104 |
| 8.13 | Είδος Ποσότητας | 104 |
| 8.14 | Σκοπός Διακίνησης | 104 |
| 8.15 | Επισήμανση | 105 |
| 8.16 | Είδος Γραμμής | 105 |
| 8.17 | Κωδικοί Καυσίμων | 105 |
| 8.18 | Τύπος Απόκλισης Παραστατικού | 106 |
| 8.19 | Ειδική Κατηγορία Παραστατικού | 107 |
| 8.20 | Κατηγορία Οντότητας (EntityType) | 108 |
| 8.21 | Αιτία Έκδοσης Αντίστροφης Διακίνησης | 108 |
| 8.22 | Κατάσταση Παραστατικού Δελτίου Διακίνησης | 109 |
| 8.23 | Τύποι Συσκευασίας (PackagingType) | 109 |

---

## 7. Ιστορικό κρίσιμων εκδόσεων

| Έκδοση | Ημερομηνία | Σημαντικότερες αλλαγές |
| --- | --- | --- |
| **2.0.1** | 11/03/2026 | Μετονομασία `invoiveDeliveryStatus` → `invoiceDeliveryStatus`, μικροδιορθώσεις |
| 2.0.0 | 24/11/2025 | Προσθήκη όλων των μεθόδων **Διακίνησης** (RegisterTransfer, ConfirmDeliveryOutcome, RejectDeliveryNote, GetDeliveryNoteStatus, GenerateGroupQRCode, RequestGroupQRDetails) |
| 1.0.12 | 13/11/2025 | Πεδίο `downloadingInvoiceUrl`, πεδία `reverseDeliveryNote*` |
| 1.0.11 | 02/04/2025 | Χαρτόσημο → Ψηφιακό Τέλος συναλλαγής, αναβάθμιση `transmissionFailure` (μέγιστη τιμή 4), νέα codes σκοπών διακίνησης |
| 1.0.10 | 23/12/2024 | `fuelInvoice=true` επιτρέπεται και μέσω ERP (όχι μόνο παρόχους) |

Πλήρες changelog στις σελίδες 109-120 του PDF (§ 9.1-9.18).

---

## 8. Σφάλματα (κεφ. 7 του PDF)

Δύο κατηγορίες:
- **Τεχνικά σφάλματα** (§ 7.1): network, authentication, malformed XML
- **Επιχειρησιακά σφάλματα** (§ 7.2): επιχειρησιακή λογική (αναπάντητο mark, λάθος ΑΦΜ, classification mismatch κλπ). Στην 1.0.11 προστέθηκαν κωδικοί 281-287 για διάφορα domain validation cases.

Όταν αντιμετωπίσεις error code, ψάξε στο PDF στο § 7.2.

---

## 9. Που το χρησιμοποιούμε στο ScanmyData

| Λειτουργία | Κώδικας | Σημειώσεις |
| --- | --- | --- |
| Fetch invoices | [fetch.py:request_docs](../fetch.py) | Χρησιμοποιεί RequestDocs + RequestTransmittedDocs |
| E3 classification fetch | [e3/checks/fetch_e3.py:fetch_e3_entries](../e3/checks/fetch_e3.py) | RequestE3Info, **χρειάζεται quarterly chunking — δες § 4.3** |
| E3 Check UI | [templates/e3_check.html](../templates/e3_check.html), [app.py:api_e3_fetch](../app.py) | Καλεί fetch_e3_entries |
| Brain individual + bulk | [e3/checks/e3_brain.py](../e3/checks/e3_brain.py) | Επανατρέχει RequestE3Info για cross-check |

---

## 10. Quick reference — checklist για νέα κλήση

1. ☐ Headers `aade-user-id` + `Ocp-Apim-Subscription-Key` σε κάθε call
2. ☐ Production URL: `https://mydatapi.aade.gr/myDATA/<method>` (όχι dev για παραγωγικά)
3. ☐ Ημερομηνίες πάντα `dd/MM/yyyy` (όχι ISO!)
4. ☐ Pagination loop μέχρι να ΜΗΝ γυρίσει κανένα cursor
5. ☐ Για RequestE3Info μεγάλων ranges → **chunk per quarter** (αλλιώς χάνεις/πολλαπλασιάζεις entries)
6. ☐ `V_Class_Category` μπορεί να γυρνά είτε `categoryX_Y` code είτε ελληνικό string — υποστήριξε και τα δύο
7. ☐ Για credit notes (5.1/5.2/11.4) το API επιστρέφει αρνητικό `V_Class_Value` — μην το ξαναγυρίζεις σε θετικό
