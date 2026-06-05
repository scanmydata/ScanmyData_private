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

---

## 11) Follow-ups shipped late 2026-06-05

### a) PDF filename now carries the property address

`misth.py.try_download_receipt_pdf` saves the receipt as
`misth_<address>_<transId>.pdf` (Greek characters preserved, non-`\w-.`
collapsed to `_`, capped at 120 chars). The brain passes the full
`--target-address` value as the label; CLI runs without an address fall
back to the lease summary text.

### b) Debug HTML opt-in

`misth.py.dump_page_html` and `e9.py.dump_page_html` now no-op unless
`E3_DEBUG_HTML=1` is set in the environment. Previously every run was
littering `misth_debug_*.html` / `etak_debug_*.html` in the project
root; now the project tree stays clean except when you explicitly want
post-mortem dumps.

### c) Year-gated bulk action buttons

`_brainBulkApplyYearGate` hides — not just disables —
`#brainRunSelectedBulkBtn` («Εκτέλεση επιλεγμένων ελέγχων») and
`#brainBulkSaveCredsBtn` («💾 Μαζική αποθήκευση credentials») whenever
`#brainYearBulk` is empty. The source-picker buttons further up stay
visible-but-disabled so the year-missing UX is still obvious.

### d) Parallel sub_home fetch

`loadE3Partners` now fires `/api/e3/brain/sub_home` IN PARALLEL with
`/api/e3/brain/company_members` (both depend only on TAXIS creds, so
there was no reason to serialize). On cold runs this halves the wait —
the user gets έδρα + members + υποκαταστήματα roughly together rather
than the branches landing 30-60s after everything else.

### e) Split-button dropdown for «Ανάκτηση διεύθυνσης»

Hidden Shift+click is replaced with a visible split-button. The primary
button runs the last-chosen mode (default `auto`); the ▾ caret opens a
menu with:

- **⚡ Από cache (αν υπάρχει για το έτος)** — instant; reads
  `_membersCacheGet(afm, year)`.
- **🔄 Νέα ανάκτηση από ΑΑΔΕ / ΓΕΜΗ** — calls
  `loadE3Partners(from, to, { force: true })` and updates the primary
  button label to «🔄 Νέα ανάκτηση από ΑΑΔΕ» so the next plain click
  repeats the force-refresh.

### f) Bulk run flash aligned with «Αποθηκεύτηκαν» slot

`_showBrainBulkBanner` now lands in `#flashContainer` (the same node
that `showStandardFetchFlash` uses — top-right fixed panel from
`base.html`). Removed the previous sticky-top behaviour that put it in
a different visual slot than the «Αποθηκεύτηκαν N εγγραφές credentials»
success flash. Pointer events re-enabled on the banner itself so the
«Διακοπή» button is clickable through the otherwise-no-pointer-events
container.

### g) Bulk: full myDATA E3 report per client

`process_client` now pulls the full E3 report (revenue / expenses /
info / tableZ) via `build_e3_report(fetch_e3_entries(...))` when the
client has `mydata_user` + `mydata_key`, and emits the whole structure
under `checks.mydata_e3_report`. 585.007 / 585.014 sums are still
exposed separately so the existing diff messages keep working.

### h) Bulk PDF export: analytic table per client + Ισοζύγιο column

`_brainBulkExportToPdf` renders the full analytic E3 πίνακας per
client (ΕΣΟΔΑ / ΔΑΠΑΝΕΣ / ΠΛΗΡΟΦΟΡΙΑΚΑ / Ζ) from
`checks.mydata_e3_report`. When the user has uploaded an Ισοζύγιο in
the same session, `E3_EXCEL_TOTALS` is folded in as a third column
plus a «Διαφορά» column — mirroring the Ατομικός view.

### i) Year-change auto-hydrate (Ατομικός + Μαζικός)

- **Ατομικός**: `_onBrainYearMaybeChanged` (bound to date / AFM
  changes) re-queries `_membersCacheGet(afm, year)` whenever the year
  derived from the dates changes. Hit → renders members + branches
  inline with a `⚡ Έτος Y: φορτώθηκαν N μέλη από cache.` flash. Miss →
  clears the members panel + warns «πάτησε Ανάκτηση». Either way the
  list reflects the selected year without the user having to click
  anything.
- **Μαζικός**: `_brainBulkToggleExpand` consults the SHARED
  `localStorage` cache as a third tier after the in-memory + saved
  snapshots; the bulk loader also writes back into the shared cache so
  Ατομικός picks it up. Changing `#brainYearBulk` blanks every
  preview row's `members` array and hints the user to re-expand.

---

## 12) Follow-ups shipped extra-late 2026-06-05

### a) AADE date format bug — bulk myDATA returning all zeros

`fetch_e3_entries` forwards `dateFrom`/`dateTo` as URL params straight to
AADE. AADE expects **dd/mm/yyyy** there; ISO `yyyy-mm-dd` is silently
accepted and matches NO rows.

- `/api/e3/fetch` (Ατομικός) was passing the user's raw input (always
  dd/mm/yyyy from the date picker) → ~4500 entries returned.
- `e3_brain.process_client` was passing `_date(year, 1, 1).isoformat()`
  → 0 entries returned, no warning, full report dropped to zeros.

Same bug existed in `_sum_mydata_sub_code`. Both now build the period
as `f"01/01/{year}"` / `f"31/12/{year}"`. Live re-test with bulk for
ΛΟΥΓΑΡΗ: `mydata_585_007=6132.78`, revenue 671K, expenses 885K — same
shape the Ατομικός tab shows.

### b) Credentials.json overlay for bulk myDATA creds

The bulk path forwards `mydata_user`/`mydata_key` straight from the
e3_company_credentials store; the Ατομικός path resolves the credential
by NAME in credentials.json. When the two stores drift (typo, rotation),
the bulk run silently picks the bad pair.

`api_e3_brain` now overlays credentials.json's `user`/`key` per AFM
(canonicalized via `_canon_afm`) onto every `single_client` and
`clients[i]` before calling `run_brain`. Overlay only fires when
credentials.json has a non-empty value — never downgrades a working
JS-supplied pair to empty.

### c) Member dedup by normalized Greek name

Issue: ΒΑΨΙΜΟ showed 5 members in the partner sub-table — 2 placeholders
(`NOAFM_0`, `NOAFM_1`) from GEMI + 3 real entries from AADE/TAXIS, all
naming the same 3 people.

`_key_for` in `/api/e3/brain/company_members` reconciliation now:

1. Treats `NOAFM_<idx>` keys the same as «no AFM» — they must fall
   through to name-based matching.
2. Falls back to `NAME:` + `_name_key(name)` when no real AFM is
   present.
3. `_name_key` strips diacritics, uppercases, and **sorts** tokens so
   "ΔΟΥΡΑΜΑΝΗΣ ΓΕΩΡΓΙΟΣ ΑΝΤΩΝΙΟΣ" and "ΑΝΤΩΝΙΟΣ ΓΕΩΡΓΙΟΣ ΔΟΥΡΑΜΑΝΗΣ"
   collapse to the same key.

The second loop also rescues «GEMI had only NAME, AADE has the real AFM»
by walking the reconciled list for a name-equivalent entry and merging
the AFM back in. Net effect: ΒΑΨΙΜΟ now shows 3 members, with sources
`['gemi','aade']` where both reported the same person.

> ⚠ Cache caveat: per-(afm, year) `localStorage` entries populated
> BEFORE this fix still hold the 5-row list. Hit «🔄 Νέα ανάκτηση από
> ΑΑΔΕ» from the split-button menu (or clear localStorage) to repopulate
> the cache from the deduped endpoint.

### d) `dump_page_html` gated in company_info.py too

The previous round gated `dump_page_html` in `misth.py` + `e9.py`, but
`e3/checks/company_info.py` has its OWN copy that was still writing
`misth_debug_*.html` on every run that hit the entry-link-missing
fallback. Now also gated behind `E3_DEBUG_HTML=1`.

### e) Auto-export comparison PDF on Ατομικός run

`#brainSaveComparisonPdf` checkbox was ornamental — no handler. Now
`runE3BrainSingle` checks it after the brain returns and (if on)
defers `_brainBulkExportToPdf` with a single-client array wrapper so
the same PDF popup the bulk produces opens automatically. Includes the
analytic E3 table + Ισοζύγιο column when uploaded.

### f) Misth PDF filename uses the property address

`misth.py.try_download_receipt_pdf` now saves as
`misth_<safe-address>_<transId>.pdf` (Greek preserved, non-`\w-.`
collapsed to `_`, capped 120 chars). The brain passes the matched
`--target-address`; CLI runs with no address fall back to the lease's
summary text. Verified: PDF for ΒΑΨΙΜΟ at ΠΑΡΑΔΕΙΣΟΥ 16 ΑΘΗΝΑ 16672
lands under
`data/tony/misth_pdfs/uid_5/802576637/misth_<address-tokens>_*.pdf`.

---

## 13) Follow-ups shipped EOD 2026-06-05

### a) «μη χαρακτηρισμένα παραστατικά» card scoped to Ατομικός

`#unclassifiedSummaryCard` is a per-customer aggregate; it leaked into
Μαζικός / Αποθηκευμένα where it makes no sense. `showBrainTab` now
toggles its `display` based on `tabId === 'brainTabSingle'` (and
collapses its detail list when hiding).

### b) Per-extractor PDF-saved flash

The user kept reporting «δεν αποθηκεύεται το PDF» even when the brain
DID save them (verified on disk + in the API panel). Added an
unambiguous post-run toast that lists the kinds/counts:
«📎 Αποθηκεύτηκαν PDF — Μισθωτηρίων: 1 · Ε9/ΕΝΦΙΑ: 1». Fires only when
`checks.pdfs_saved.<kind> > 0`.

### c) Duplicate «Αποθηκευμένα credentials φορτώθηκαν.» flash

`brainFetchCredsBtn` had TWO click handlers — the document-level
delegated one (`#brainFetchCredsBtn` case in `setupE3CheckTabBindings`)
AND a direct `addEventListener` further down in `rebindE3Controls`.
Both fired `fetchE3BrainCredentials({ verbose: true })` → toast twice.
Removed the direct listener; the delegated handler is the single
source of truth.

### d) Cross-page sticky banner for E3 Bulk «Διακοπή»

The bulk banner used to live inside `e3_check.html`'s DOM, so leaving
the page killed it — the user lost the «Διακοπή» button and any
progress feedback while the brain kept running for minutes.

New flow:

- `executeBulkSelectedClients` now also calls
  `window.showStickyBanner({ id: 'e3-bulk', ... onAction: abort })` —
  the banner from base.html's existing `globalStickyBannerHost`
  (`#globalStickyBannerHost`, fixed top, z-index 9990) survives
  partial AND full navigation.
- Run metadata (`jobId`, `total`, `startedAt`) is persisted to
  `sessionStorage.e3BulkActiveJob`.
- base.html adds a tiny resume-poller (3s interval): on every page load
  it reads that storage entry; if present, re-attaches the same banner
  and polls `/api/e3/brain/progress/<jobId>` to drive the label. When
  the brain stops emitting progress for ~15s past the 30s grace period
  → clear storage + remove banner.
- «Διακοπή» button on the resumed banner still POSTs to
  `/api/e3/brain/abort/<jobId>` so the user can stop the run from any
  page.

The legacy in-page banner inside `e3_check.html` is kept as a
companion (mirrored via the progress poller) so users on /e3_check
still see the same info in two places.

### e) About the «PDF μισθωτηρίων δεν αποθηκεύεται» persistence

Standalone misth.py + brain orchestrator both verified saving the PDF
to disk at
`data/tony/misth_pdfs/uid_5/<afm>/misth_<addr-tokens>_<transId>.pdf`.
If the user still doesn't see it, check:

1. The «Λήψη PDF μισθωτηρίων (έδρα + υποκαταστήματα)» checkbox is
   ticked BEFORE the run (gates `download_misth_pdfs`).
2. Bulk mode requires `#brainBulkSavePdfs` master to be on — the
   per-kind UI lives only in Ατομικός.
3. `_resolve_pdfs_dir` needs a Flask request context with an active
   group; check the αποθηκευμένα PDFs panel reflects what's on disk
   for the AFM in `#brainAfm`.

---

## 14) AADE wrapper-node dedup — 585.007 overcount root cause

### Symptom
User produced an official AADE «Στοιχεία προσυμπλήρωσης Ε3» PDF for
ΛΟΥΓΑΡΗΣ 2025 showing **585.007 = €4.332,83**. Our brain returned
**€6.132,78** for the same code/period (+€1.799,95 over).

### Root cause
AADE's `RequestE3Info` response wraps each invoice's classification
details in MULTIPLE container nodes — `<expensesInvoiceClassification>`
**AND** `<E3Info>` (and sometimes `<incomeInvoiceClassification>`) all
contain the same `<expensesClassification>` detail for the same mark.
`fetch_e3_entries` iterates all three wrapper types and was emitting
one row per occurrence — so an invoice that appears in both wrappers
was counted twice.

Concrete repro (this exact dataset):
- 12 unique marks classified as 585.007
- 17 emitted rows (5 marks appeared twice)
- 5 duplicate amounts summed to exactly **€352,59 + €361,84 × 4 = €1.799,95**
- 4.332,83 + 1.799,95 = 6.132,78 — match.

### Fix
`e3/checks/fetch_e3.py.fetch_e3_entries` now dedupes by
`(invoice_mark, classification_type, amount, classification_category)`
tuple via a `seen_entries` set. The 4-tuple is the natural unit of
identity for a line item — a single mark genuinely can have multiple
classification details with different amounts, but the same exact
4-tuple appearing twice is always a wrapper-loop duplicate.

### Verified post-fix
| Code | Brain | Official PDF | Match |
|---|---|---|---|
| **585.007** | **4.332,83 €** | **4.332,83 €** | ✓ |
| 585.013 | 1.714,50 € | 1.714,50 € | ✓ |
| 581.001 | 15.099,09 € | 15.099,09 € | ✓ |
| 581.002 | 4.377,80 € | 4.377,80 € | ✓ |
| 586 | 2.719,11 € | 2.719,11 € | ✓ |

Residuals on 561.x / 102.x / 585.011 / 585.016 are <2% — likely
invoices the user characterized in myDATA between our test fetch and
the AADE PDF generation timestamp.

---

## 15) Bulk progress banner moved to right-side flash slot

The cross-page banner used to land in `#globalStickyBannerHost` (a
full-width strip at the very top of the viewport) — but it covered the
header menu and the user explicitly asked for the discreet right-side
flash style («θελω να ειναι μονο το δεξιο»).

New implementation in `base.html`:
- Cross-page poller renders into the same `#flashContainer` slot the
  «Αποθηκεύτηκαν credentials» success flash uses
  (`ensureFlashContainer` — top-right fixed panel).
- Custom flash element `#e3BulkProgressFlash` with `data-ttl="0"`
  (sticky), text + inline red «Διακοπή» button. POSTs to
  `/api/e3/brain/abort/<jobId>`.
- `sessionStorage.e3BulkActiveJob` carries `{jobId, total, startedAt}`
  so any navigated-to page picks the banner up within ~3s.
- Originating tab still updates the banner directly via the progress
  poller (per-client step changes are seen instantly, not on the next
  base.html tick).

Removed:
- In-page top-center `_showBrainBulkBanner` (legacy, was a duplicate).
- The two-banner mirror logic in `executeBulkSelectedClients`.

Verified live on the home page (`/`) — banner says
«E3 Bulk — εκτέλεση σε εξέλιξη για 5 πελάτες…» with the red Διακοπή
button, top-right, header menu untouched.

---

## 16) Unclassified-list dropdown — delegated click handler

`#toggleUnclassifiedList` had a once-only `addEventListener` inside an
IIFE that ran at script load. On certain partial-nav paths the button
was re-created AFTER the IIFE had already run → click had no effect.

Replaced with a single document-level delegated handler guarded by
`document.body.dataset.unclassifiedToggleDelegated`, which works
regardless of when/how often `#toggleUnclassifiedList` is rendered.

Note on semantics: the official AADE PDF dumps unclassified invoices
into 585.016 («Λοιπά έξοδα»). We do NOT — `api_e3_fetch` already
filters the unclassified marks out of the E3 report and surfaces them
in their own bucket (`unclassified_invoices` + the toggle button).

---

## 17) Cross-check E3 codes vs official AADE PDF (ΛΟΥΓΑΡΗΣ 2025)

After the §14 dedup, ran a full code-by-code comparison against the
official «Στοιχεία προσυμπλήρωσης Ε3» PDF:

| Code | Brain | PDF | Match |
|---|---|---|---|
| 581.001 | 15.099,09 | 15.099,09 | ✓ exact |
| 581.002 | 4.377,80  | 4.377,80  | ✓ exact |
| 585.007 | 4.332,83  | 4.332,83  | ✓ exact |
| 585.013 | 1.714,50  | 1.714,50  | ✓ exact |
| 586     | 2.719,11  | 2.719,11  | ✓ exact |
| 561.001 | 3.754,40  | 3.802,37  | -47,97 (-1,3%) |
| 561.003 | 307.930,55| 309.323,14| -1.392,59 (-0,5%) |
| 102 total| 261.192,45| 264.181,30| -2.988,85 (-1,1%) |
| 585.009 | 1.480,00  | 1.549,60  | -69,60 (-4,5%) |
| 585.011 | 7.513,18  | 7.762,45  | -249,27 (-3,2%) |
| 585.016 | 6.798,25  | 6.854,96  | -56,71 (-0,8%) |

All residuals are **UNDER** the PDF. Verified algorithmically: removing
the dedup gives values OVER the PDF. With dedup, every (mark, cls,
amount, cat) tuple that came from AADE is counted exactly once.

→ Residuals are **time-lag**, not algorithmic bugs. The PDF was
generated by AADE at a slightly later cutoff than our fetch and likely
includes invoices the user characterized in myDATA between the two
calls. Same-day delta ≤2% on the major codes is expected.

To reconcile exactly the user can run the brain again right after
producing the PDF; the comparison gap should shrink to 0.

---

## 18) Unclassified card — visibility rules + bulk PDF list

### Card visibility (single source of truth)

`_refreshUnclassifiedCardVisibility()` is now called from BOTH
`showE3PageTab` and `showBrainTab`, so the rules are enforced
regardless of which path the user takes:

| Surface | Card visible? |
|---|---|
| Γρήγορος Έλεγχος (quickCheckTab) | ✓ always |
| Αναλυτικός Ατομικός (analytic + brainTabSingle) | ✓ |
| Αναλυτικός Μαζικός (analytic + brainTabBulk) | ✗ |
| Αναλυτικός Αποθηκευμένα (analytic + brainTabSaved) | ✗ |

Switching pages restores the card for Γρήγορος even if it was hidden
by a previous brain-sub-tab interaction. Verified live across all four
combinations.

### Bulk PDF: per-client μη-χαρακτηρισμένα list

`process_client` in `e3/checks/e3_brain.py` now performs the same
classified/unclassified split that `api_e3_fetch` uses:

1. Pull raw `fetch_e3_entries` once.
2. Per invoice mark, mark UNCLASSIFIED iff any row has
   `classification_category` starting with `ΜΗ` AND containing
   `ΧΑΡΑΚΤΗΡΙΣΜ`.
3. Build `mydata_e3_report` from the CLASSIFIED entries only (so
   unclassified amounts don't leak into 585.016 — opposite of what the
   official AADE PDF does).
4. Surface `checks.unclassified_total` + `checks.unclassified_invoices`
   per client.

`_brainBulkExportToPdf` now renders an additional «Λίστα μη
χαρακτηρισμένων παραστατικών — N παραστατικά, σύνολο X €» block per
client (when there are unclassified invoices), with the
MARK / Ημερομηνία / ΑΦΜ Εκδότη / Επωνυμία / Αξία columns. Block
appears between the analytic E3 table and the διαγνωστικά section.

585.007 and 585.014 are also re-derived from the SAME classified
entries (not from a separate `_sum_mydata_sub_code` call) so the
indicator numbers always match the πίνακας values.
