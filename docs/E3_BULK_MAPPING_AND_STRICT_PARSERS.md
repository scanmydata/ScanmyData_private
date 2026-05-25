# E3 Bulk Mapping and Strict Parsers

Date: 2026-05-25

## Scope
This document describes:
- deterministic bulk parsing for production Excel templates,
- accepted flat-table header variants,
- strict amount extraction rules for EFKA / TEKA / misth / E9,
- sample outputs used to validate parser behavior.

## 1) Deterministic bulk parsing modes

The bulk parser in `e3/checks/e3_brain.py` now supports 3 levels:

1. Strict flat-table mode (explicit headers)
2. Production template mode (`ΤΕΣΤ_ΚΩΔΙΚΟΙ.xlsx` layout blocks)
3. Final pandas fallback for generic workbooks

Parsing priority is deterministic in this exact order.

## 2) Accepted flat headers (explicit)

The following column families are recognized (Greek + English variants):

- AFM:
  - `ΑΦΜ`, `afm`, `vat`, `vat number`
- Name:
  - `επωνυμια`, `επωνυμία`, `name`, `πελατης`, `πελάτης`
- TAXIS username:
  - `ονομα χρηστη taxis`, `όνομα χρήστη taxis`, `taxis username`, `taxisnet username`, `username`
- TAXIS password:
  - `κωδικος taxis`, `κωδικός taxis`, `taxis password`, `taxisnet password`, `password`
- myDATA username:
  - `ονομα χρηστη mydata`, `όνομα χρήστη mydata`, `mydata user`, `aade-user-id`
- myDATA subscription key:
  - `subscription key mydata`, `subscription key`, `mydata key`, `ocp-apim-subscription-key`
- Optional:
  - `ΑΜΚΑ`, `E3_585_007`, `E3_585_014`, `διευθυνση/διεύθυνση/address`

User-requested format is therefore fully supported.

## 3) Production template mapping (`ΤΕΣΤ_ΚΩΔΙΚΟΙ.xlsx`)

Supported source format (block layout):
- company row: `00074 <Company Name> <AFM>`
- TAXIS row: `Taxis Net | <username> | <password>`
- myDATA row: `Ηλεκτρονικά Βιβλία Α.Α.Δ.Ε. (myDATA) | <mydata_user> | <subscription_key>`

The parser reads XLSX XML directly (zip/xml), so it works even when workbook styles are invalid for openpyxl.

## 4) Real sample output (from production file)

Input file used:
- `ΤΕΣΤ_ΚΩΔΙΚΟΙ.xlsx`

Parsed output sample:

```json
[
  {
    "afm": "998702854",
    "name": "ΤΟΓΚΑΣ ΒΑΣΙΛΕΙΟΣ ΡΑΠΤΗΣ ΔΗΜΗΤΡΙΟΣ ΟΕ",
    "taxisnet_username": "WW1964253U564",
    "taxisnet_password": "TogasRaptisOE1=",
    "amka": "",
    "mydata_user": "TOGASRAPTISOE1",
    "mydata_key": "b0053df15fd24295bbb733a101623b9b",
    "excel_values": {"E3_585_007": 0.0, "E3_585_014": 0.0},
    "address": ""
  },
  {
    "afm": "802576637",
    "name": "ΤΟ ΒΑΨΙΜΟ Ε Ε",
    "taxisnet_username": "802576637",
    "taxisnet_password": "Tv802576!",
    "amka": "",
    "mydata_user": "TOVAPSIMO",
    "mydata_key": "02e1d4aae7052d7cf5a6dfddb9af81e9",
    "excel_values": {"E3_585_007": 0.0, "E3_585_014": 0.0},
    "address": ""
  }
]
```

## 5) Strict amount parsers for 585_007 / 585_014

### EFKA / TEKA strict parser

Source:
- JSON from `efka-extractor.py` / `teka-extractor.py` (`rows` list)

Rules:
1. Keep only rows that include target year text.
2. Prefer numeric values from amount-related headers:
   - `ποσο`, `ποσό`, `εισφορες`, `εισφορές`, `συνολο`, `σύνολο`, `οφειλη`, `οφειλή`
3. If no strict header match exists, fallback to numeric extraction from row text.
4. Use `max(candidate_values)` for each extractor result.

### misth strict parser

Source:
- JSON from `misth.py` (`detail.detailTables`)

Rules:
1. Select latest lease matching HQ address.
2. Detect monthly rent from rows containing tokens:
   - `μηνια`, `μισθωμα`, `μίσθωμα`, `ποσο μισθ`, `ποσό μισθ`
3. Detect expiry from rows containing tokens:
   - `εως`, `έως`, `ληξη`, `λήξη`
4. Fallback to global numeric/date extraction only if strict labels are missing.

### E9 strict parser

Source:
- JSON from `e9.py` (`pdfMatchedRows`)

Rules:
1. Keep only rows that include target year.
2. Prioritize rows containing value tokens:
   - `συνολικ`, `αξια/αξία`, `ακινητ`, `αντικειμεν`, `φορολογητε/φορολογητέ`
3. If no strict row found, fallback to year-filtered numeric values.
4. For 585_014 fallback via property value: `annual_rent = property_value * 0.03`.

## 6) Notes

- `client_db` remains counterparties/suppliers and is not used for active-client detection.
- Active clients for E3 brain eligibility continue to derive from credentials.
