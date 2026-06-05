# E3 Brain Wiring Notes (2026-06-05)

This note documents the misth (μισθωτήρια) / Ε9-ΕΝΦΙΑ wiring changes shipped on
2026-06-05, plus the UI rules around the analytic table, the αποθηκευμένα PDF
panel, and the bulk vs ατομικό PDF retention split.

Test creds used while diagnosing:
- misth (ΒΑΨΙΜΟ, ΑΦΜ 802576637, ΠΑΡΑΔΕΙΣΟΥ 16 ΒΑΡΗ): `802576637 / Tv802576!`
- Ε9/ΕΝΦΙΑ (ΛΟΥΓΑΡΗ ΣΠΥΡΟ, ΑΦΜ 036209456, Ν ΜΑΝΔΗΛΑΡΑ 27 ΗΛΙΟΥΠΟΛΗ):
  `WW726502U828 / LOUG11`

---

## 1) Root cause for "misth doesn't bring amount/PDF"

The standalone `e3/checks/misth.py` script always found the lease and saved the
receipt PDF when invoked with the user-typed address. The brain, however,
silently dropped the match because its address matcher disagreed with the
script's matcher.

| Matcher                                      | Strategy                | Behavior |
|----------------------------------------------|-------------------------|----------|
| `misth.py.detail_matches_address`            | Substring of >=3-char tokens in body text | "ΒΑΡΗ" in "βαρης" → ✓ |
| `e3_brain.py._pick_latest_lease` (old)       | Set inclusion of 5-char stems            | stem("βαρη")=`βαρη` ≠ stem("βαρης")=`βαρης` → ✗ |

So `_pick_latest_lease` returned `None` for ΒΑΨΙΜΟ even though `misth.py` had
already scraped the matching lease — and the PDF download in `misth.py` was
gated on a third call to `detail_matches_address`, which by itself was fine
but never saw the request because the brain hadn't passed `--target-address`
when the misth PDF checkbox was off (see §3 below).

### Fix
`e3/checks/e3_brain.py` now has `_address_loose_match(target, body_text)` which
mirrors `misth.py.detail_matches_address` (substring of `>=3`-char normalized
tokens). `_pick_latest_lease` calls this instead of stem-set comparison.

Verified (post-fix):
```text
ΠΑΡΑΔΕΙΣΟΥ 16 ΒΑΡΗ        → MATCHED transId=94653948
ΠΑΡΑΔΕΙΣΟΥ 16 ΒΑΡΗ 16672  → MATCHED transId=94653948
```

`e9.py.find_ataks_by_address` keeps its own stem-based matcher unchanged —
it operates on grid rows where the tokens are short and unambiguous, and the
user explicitly asked for the e9 path to be left alone.

---

## 2) E3 πίνακας visibility rules

| Surface                                 | Container visible? |
|-----------------------------------------|---------------------|
| Ατομικός (γρήγορος έλεγχος)              | ✓ when myDATA fetch returned rows |
| Ατομικός (αναλυτικός έλεγχος)            | ✓ when myDATA fetch returned rows |
| Μαζικός                                 | ✗ — bulk has its own accordion |
| Αποθηκευμένα                            | ✗ |
| Active client dropdown changed          | ✗ — hidden until next αντλήση    |

Implementation:
- `templates/e3_check.html` defines `_e3HasRealData(data)` as a single gate.
  Every `#e3DataContainer.style.display = 'block'` path (`loadE3Data`,
  `loadBrainE3Data`, `uploadBrainExcelFile`, `_applyBrainChecksToE3Table`)
  now goes through that gate.
- `showBrainTab(tabId)` hides the container whenever the user switches AWAY
  from `brainTabSingle`.
- `onBrainActiveClientSelectChange` hides the container and clears
  `E3_BRAIN_CHECKS` + `E3_LAST_DATA` so a stale row never bleeds across
  client switches.

---

## 3) PDF retention checkboxes (ατομικό-only)

Per user request the «Λήψη & διατήρηση PDF» panel lives **only** in the
Ατομικός tab. The bulk tab no longer mirrors them; bulk runs always send
`download_*_pdfs = false` to `/api/e3/brain`.

| Checkbox (ατομικό)                  | Payload key              | Backend effect |
|-------------------------------------|--------------------------|----------------|
| Λήψη PDF βεβαιώσεων ΕΦΚΑ            | `download_efka_pdfs`     | `_resolve_pdfs_dir("efka", afm)` + `--pdf-dir` to extractor |
| Λήψη PDF βεβαιώσεων ΤΕΚΑ            | `download_teka_pdfs`     | same, kind="teka" |
| Λήψη PDF μισθωτηρίων (έδρα + υποκαταστήματα) | `download_misth_pdfs` | `_resolve_pdfs_dir("misth", afm)` + `--pdf-dir --target-address` to `misth.py` |
| Λήψη PDF εκκαθαριστικού Ε9 / ΕΝΦΙΑ  | `download_e9_pdfs`       | `--keep-pdf` to `e9.py` for both HQ and branch loops; PDF copied to per-AFM folder |

Without the box ticked, the brain skips passing the PDF-related flags and the
extractor just scrapes the tables it needs for the comparison. The receipt /
ENFIA documents are never persisted in that case.

**Important:** unchecking `download_misth_pdfs` does NOT affect the rent
amount — that comes from the scraped lease detail tables. Unchecking
`download_e9_pdfs` does NOT affect ιδιόχρηση either — `e9.py` still downloads
the ENFIA PDF into its temp dir, reads αξία ακινήτου with `pdfplumber`, then
deletes the temp file.

Per-AFM PDF folders land under:
```
data/<group.data_folder>/<kind>_pdfs/uid_<id>/<afm>/
```

---

## 4) Αποθηκευμένα PDF panel — refresh on dropdown change

`refreshBrainAllPdfs` now accepts an explicit `{ afm }` option so the
dropdown change handler can pass the new ΑΦΜ directly. Previously the
function read `#brainAfm` which could still hold the previous client's
value if `fillBrainSingleClientFromSaved` raced with the refresh.

`onBrainActiveClientSelectChange` flow:
1. Hide `#e3DataContainer`, clear `E3_BRAIN_CHECKS`, null `E3_LAST_DATA`.
2. Wipe `_BRAIN_PDFS_AGGREGATE`, blank `#brainPdfsList` and
   `#brainPdfsStatus`.
3. Sync-fill the form from the cache via `fillBrainSingleClientFromSaved`.
4. Call `refreshBrainAllPdfs({ afm: newAfm })` — both the status line
   ("Σύνολο: N αρχεία για ΑΦΜ ...") and the file list now reflect the
   newly selected client, even if `#brainAfm` is updated slightly later.

If the user picks the empty option, the panel is just blanked — no refresh.

---

## 5) Bulk summary table — amounts instead of icons

The bulk-results summary (and its print/PDF twin) now shows euro amounts in
the ΕΦΚΑ/ΤΕΚΑ, Μισθωτήρια and Ε9 columns instead of ✓/—/◐ icons.

Splitting logic in `_renderBulkResultsAccordion`:
- Σύνολο ΕΦΚΑ/ΤΕΚΑ: `checks.efka_teka_total`
- Σύνολο Μισθωτηρίων: `Σ checks.rent_breakdown.where(source == 'misth').amount`
- Σύνολο Ε9: `Σ checks.rent_breakdown.where(source == 'e9_3_percent').amount`
- Fallback (no breakdown): attribute `checks.rent_annual` to whichever
  source `checks.rent_source` reported.

Icons (◐ amber, — gray) are retained only when the extractor ran without
producing an amount (`misth_error`/`e9_error` surfaces on hover).

---

## 6) Files touched

| File | Change |
|------|--------|
| `e3/checks/e3_brain.py` | `_address_loose_match`, `_pick_latest_lease` rewrite; misth/e9 PDF dir resolution gated on the per-extractor download flags (HQ + branches) |
| `templates/e3_check.html` | `_e3HasRealData` gate; `onBrainActiveClientSelectChange` hides table + clears PDFs + refreshes; `showBrainTab` hides table outside ατομικό; bulk PDF checkboxes removed; bulk run forces `download_*_pdfs=false`; bulk summary table renders amounts; `refreshBrainAllPdfs({ afm })` |
| `docs/E3_BRAIN_WIRING_NOTES_2026-06-05.md` | this note |

---

## 7) Operational guidance

1. Pick a client from the active-client dropdown (or load one from
   Αποθηκευμένα). The E3 table from the previous run disappears
   immediately.
2. Tick the extractors you want — ΕΦΚΑ/ΤΕΚΑ, Μισθωτήρια, Ε9.
3. Tick the PDF retention boxes you want kept on disk; untick them if you
   only want the table comparison and no document artefacts.
4. Run Εκτέλεση ελέγχου/ελέγχων. The E3 table reappears after myDATA
   completes, with brain ✓/⚠/ℹ indicators on 585.007 (ΕΦΚΑ/ΤΕΚΑ) and
   585.014 (rent / ιδιόχρηση).
5. Saved PDFs appear in the panel below; switching client re-queries the
   panel for the newly selected ΑΦΜ.

If misth still reports «η διεύθυνση έδρας δεν βρέθηκε στα μισθωτήρια» after
this update:
- Confirm the address field in the form matches the actual lease address
  closely enough (3+ char tokens of the form address must be substrings of
  the lease body). Greek case endings are tolerated; missing/extra street
  numbers will still break the match.
- Check `data/<group>/misth_pdfs/uid_<id>/<afm>/` — if a PDF landed there
  but no amount surfaced, the matcher is fine but
  `_extract_lease_amount_and_expiry` failed to find an "μηνιαίο μίσθωμα"
  cell; flag the lease so we can extend the parser.

---

## 8) ΤΚ-anchored address matcher (follow-up 2026-06-05)

Live debug surfaced a third matcher failure mode: the user-stored address
for ΒΑΨΙΜΟ was `ΠΑΡΑΔΕΙΣΟΥ 16 ΑΘΗΝΑ 16672` but the actual lease body lists
`ΠΑΡΑΔΕΙΣΟΥ 16 ΒΑΡΗΣ 16672`. The substring matcher from §1 still rejected
because the city token «αθηνα» was nowhere in the body.

`_address_loose_match` (brain) and `detail_matches_address` (misth.py) now
both run a two-stage match:

1. **Strict** — every significant target token (>=3-char word OR any
   digit token) must hit the body. Words match as substrings (Greek case
   endings preserved); numbers must match as standalone tokens (so `"5"`
   cannot stealth-match `"16672"`).
2. **ΤΚ-anchored fallback** — if strict fails BUT the target has a 5-digit
   ΤΚ in the body, every street-number token is in the body, AND at least
   one word token still hits the body, accept anyway. The rule is "ΤΚ +
   street + number triangulates the property; city/δήμος label is allowed
   to differ".

Tests cover:

| Target                              | Body                                 | Result |
|-------------------------------------|--------------------------------------|--------|
| ΠΑΡΑΔΕΙΣΟΥ 16 ΑΘΗΝΑ 16672           | … παραδεισου 16 βαρης 16672 …        | ✓ (ΤΚ fallback) |
| ΠΑΡΑΔΕΙΣΟΥ 16 ΒΑΡΗ 16672            | … βαρης …                            | ✓ (case ending) |
| ΕΡΜΟΥ 5 ΑΘΗΝΑ 16672                 | (different street)                   | ✗ |
| ΠΑΡΑΔΕΙΣΟΥ 5 ΑΘΗΝΑ 16672            | (wrong street number)                | ✗ |

Verified end-to-end against ΑΦΜ 802576637 through `/api/e3/brain`:
`rent_annual=732, rent_source=misth, pdfs_saved.misth=1`.

---

## 9) Bulk PDF master switch (follow-up 2026-06-05)

The bulk tab now has a single `#brainBulkSavePdfs` master checkbox:

- **off** (default): bulk run sends `download_*_pdfs=false` for every
  client; nothing lands on disk.
- **on**: each enabled extractor for each client persists its document to
  `data/<group>/<kind>_pdfs/uid_<id>/<afm>/`. Per-extractor granularity
  stays in the Ατομικός tab — bulk is intentionally coarse.

The flag flows through `executeBulkSelectedClients` as:
```js
download_efka_pdfs:  _bulkSaveAllPdfs && runEfkaTeka,
download_teka_pdfs:  _bulkSaveAllPdfs && runEfkaTeka,
download_misth_pdfs: _bulkSaveAllPdfs && runMisth,
download_e9_pdfs:    _bulkSaveAllPdfs && runE9,
```

so PDF saving only happens for the extractors the bulk run actually
invoked — no orphan ENFIA PDFs from a bulk run that had Ε9 unchecked.

---

## 10) Per-(AFM, year) member cache (follow-up 2026-06-05)

Members + έδρα + νομική μορφή + υποκαταστήματα are stable for a given
χρήση. The first αντλήση hits ΑΑΔΕ + ΓΕΜΗ (30-60s); subsequent loads for
the same `(afm, year)` now hydrate from `localStorage` instantly.

Cache layout:
```
key:   E3_BRAIN_MEMBERS_CACHE:<afm>:<year>
value: { company, address, legal_type, is_individual, members, branches, ts }
TTL:   30 days
```

Helpers in `templates/e3_check.html`:
- `_membersCacheGet(afm, year)` / `_membersCacheSet(afm, year, payload)`
  / `_membersCacheClear(afm, year)` (omit year to clear every year for
  the AFM).
- `loadE3Partners(fromIso, toIso, { force })` — when `force` is true,
  bypasses the cache.

User UX:
- Cache hit → instant fill, success flash `⚡ Φορτώθηκαν από cache …`
  with member count + cache age in days.
- **Shift+κλικ** στο κουμπί «Ανάκτηση διεύθυνσης» = force refresh from
  ΑΑΔΕ/ΓΕΜΗ (when the company structure actually changed mid-year).
- Branches arrive asynchronously from `sub_home`; the cache entry is
  updated again when they land, so the next visit gets them too.

Storage caveat: localStorage is per-browser, per-origin. A user switching
between desktop and mobile will repopulate the cache once on each device.
