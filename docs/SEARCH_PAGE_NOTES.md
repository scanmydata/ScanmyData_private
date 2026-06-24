# Search page (`templates/search.html`) — architecture & scripts

Last updated: 2026-06-24

This is a reference for the search/entry page: how MARK search, scrape, save,
delete and undo work, which scripts are involved, and the performance gotchas
that have bitten us (slow save/delete, double table reloads, slow login).

---

## 1. Page composition

`templates/search.html` (~15.9k lines) extends `base.html` and is the main
working screen. It includes the invoice/receipt table partial:

```
templates/search.html
 ├── {% include 'list_inner.html' %}   ← the results table + delete/undo + Tabulator
 └── #loadingOverlay                    ← per-operation spinner (scrape/save), NOT the global one
```

Key DOM ids:

| id | role |
|----|------|
| `markSearchForm` / `markInput` | MARK or QR-URL search box |
| `scrapeUrlInput` / `scrapeUrlField` | receipt scrape URL (visible input + hidden field) |
| `useReceiptsSwitch` | toggles receipts vs invoices mode |
| `repeatEntrySwitch` | επαναλήψιμη εισαγωγή (auto-categorize + auto-save) |
| `summaryModal` / `saveSummaryForm` / `summaryJsonInput` | the confirm/save modal |
| `summary-container` | where the results table HTML is injected |

---

## 2. Server endpoints used

| Route | File | Purpose |
|-------|------|---------|
| `POST /search` | `app.py:10186` | MARK search / QR scrape; renders or returns summary |
| `POST /save_summary` | `app.py:12626` | Persist a document → Excel + per-VAT epsilon cache |
| `POST /delete` | `app.py:16428` | Delete MARKs from Excel + epsilon cache; pushes an undo entry |
| `POST /delete/undo` | `app.py:16306` | Restore the last delete (fast — see §5) |
| `GET /list/fragment` | `app.py:15816` | Returns just the table HTML for the active VAT (used by partial reload) |

Storage backend is **Google Drive** (`setting.storage_backend='drive'`), so any
`firebase_log_activity` / `firebase_write_data` is a slow network call. This is
the single most important performance fact about this page — see §5.

---

## 3. Client scripts

### Inline (in `search.html`)
- **`partiallyReloadInvoiceTable(opts)`** — the canonical post-save table refresh.
  Schedules search-box refocus/highlight, then delegates to
  `window.FBP_REFRESH_LIST_FRAGMENT`. Wrapped with a **debounce** (`__RC_PARTIAL_RELOAD_INFLIGHT`
  + 1.2s same-mark window) so concurrent callers don't reload twice.
- **`clearSearchInputs()`** — clears markInput/scrapeUrlInput/scrapeUrlField after save.
- **`rcFastPostSaveRefresh(mark, msg)`** — clear + flash + `partiallyReloadInvoiceTable`.
- **Save handlers** — multiple `saveSummaryForm` submit listeners: receipt-with-scrapeUrl
  flow (`search.html:9079`), invoice flow (`search.html:9283`), and a final
  "safety net" delegated submit (`search.html:~15005`).
- **Global `/save_summary` fetch hook** (end of file) — wraps `window.fetch`; on any
  successful `POST /save_summary` it force-runs clear + `partiallyReloadInvoiceTable`
  + green highlight. This is a *safety net* so that whichever of the many save
  handlers fired, the post-save UX always happens. The debounce in
  `partiallyReloadInvoiceTable` / `FBP_REFRESH_LIST_FRAGMENT` prevents this from
  causing a double reload.

### `list_inner.html` (table partial)
- **`refreshListFragment()`** → exported as `window.FBP_REFRESH_LIST_FRAGMENT`
  via an **in-flight-dedup** wrapper (`refreshListFragmentDeduped`). Fetches
  `/list/fragment`, swaps `#summary-container`, re-inits Tabulator, re-applies the
  green highlight for `__RC_PENDING_LIST_HIGHLIGHT_MARK`.
- **Delete flow** — `deleteBtn` → `collectCheckedMarks()` → confirm modal →
  `POST /delete` → `FBP_REFRESH_LIST_FRAGMENT()`.
- **Undo flow** — `FBP_DELETE_UNDO` + `runUndo()` → `POST /delete/undo` →
  `FBP_REFRESH_LIST_FRAGMENT()`.
- **`FBP_INIT_TABULATOR`** — (re)builds the Tabulator grid from the server-rendered
  `.summary-table`.

### Static helpers (`static/`)
Loaded near the top of `search.html`:

| file | purpose |
|------|---------|
| `loop_hard_guard.js` | prevents infinite auto-submit/refresh loops |
| `repeat_flow_guard.js` | stops auto-submit loops; auto-applies repeat for receipts without the modal |
| `receipts_repeat_direct.js` | bypasses the summary modal for receipts when repeat is ON (legacy; disabled when canonical autoconfirm is on) |
| `receipts_autosubmit_strict.js` | deterministic receipts-only/URL-only auto-submit (legacy) |
| `receipts_fast_flow.js` | **the fast pattern**: scrape + save fully via AJAX, single `FBP_REFRESH_LIST_FRAGMENT`, no page reload. Active when receipts + repeat are both ON |
| `receipts_mode_persist.js` / `receipts_mode_repeat_bridge.js` | persist receipts/repeat toggles in localStorage |
| `char_profiles.js`, `summary_modal.js` | characterization profiles + modal helpers |

`window.__RC_USE_CANONICAL_RECEIPT_AUTOCONFIRM = true` (set at the top of the
scripts block) disables the older `receipts_repeat_direct` / `receipts_autosubmit_strict`
paths in favor of the canonical flow.

---

## 4. The three write flows

```
SAVE (invoice/receipt):
  submit saveSummaryForm → POST /save_summary
    → (native handler) rcFastPostSaveRefresh → partiallyReloadInvoiceTable
    → (global fetch hook) partiallyReloadInvoiceTable   ← deduped to 1 reload
    → clearSearchInputs + green highlight of saved MARK

DELETE:
  deleteBtn → confirm → POST /delete → FBP_REFRESH_LIST_FRAGMENT (1 reload)

UNDO:
  undo menu → POST /delete/undo → FBP_REFRESH_LIST_FRAGMENT (1 reload)
```

All three reload via the same `/list/fragment` → Tabulator rebuild path.

---

## 5. Performance notes (important)

**Why save/delete were slow but undo was instant (fixed 2026-06-24):**
`delete_invoices` and `save_summary` ended with a synchronous
`log_user_activity(...)` call, which writes an audit log to **Google Drive**
(several seconds). `delete_undo` does *not* log activity, which is exactly why it
felt instant. Fix: `utils.log_user_activity` now does the
`firebase_log_activity` write in a **daemon thread with an app context**
(fire-and-forget). This sped up every write path (delete, save, login, fetch,
export). See [[slow-login-cause]] in agent memory for the login variant.

**Double table reload on save (fixed 2026-06-24):**
Both the native save handler and the global `/save_summary` fetch hook call
`partiallyReloadInvoiceTable`, and `receipts_fast_flow` calls
`FBP_REFRESH_LIST_FRAGMENT` directly. Each reload = a `/list/fragment` fetch +
full Tabulator rebuild. Dedup added at two layers:
- `partiallyReloadInvoiceTable` — in-flight promise reuse + 1.2s same-mark window.
- `FBP_REFRESH_LIST_FRAGMENT` (`refreshListFragmentDeduped`) — in-flight promise
  reuse only (so sequential delete/undo refreshes still run).

**Global wait overlay (removed 2026-06-23):**
`base.html` used to patch `window.fetch`/XHR/submit to show `#globalWait` on every
request. It made the app feel slow and could cover the table. It's disabled;
`window.WaitOverlay.{show,hide,suspend,forceHide}` are now no-ops. The per-op
`#loadingOverlay` in `search.html` (scrape/save spinner) is separate and kept.

**`session_manager.js` logout-on-unload (gotcha for testing):**
It fires `navigator.sendBeacon('/auth/api/logout')` on every `beforeunload`.
Real users avoid logout because the app navigates via partial/AJAX, but any full
`window.location` navigation in an automated/preview browser logs you out.
When driving the page in a test browser, override `navigator.sendBeacon = ()=>true`
after each page load before navigating. See [[dev-login-and-preview-testing]].

---

## 6. Gotchas / corrupted-merge history

- A bad merge once injected stray `rcLiveDebug(...)` / `rcScheduleDirectSearchFocus(..., 22000)`
  statements into the receipt save handler in `search.html` (removed 2026-06-23).
  They scheduled 22s of search-box refocuses after each save. If post-save focus
  behaves oddly, grep for misplaced `rcLiveDebug` / `rcScheduleDirectSearchFocus`.
- The green row highlight is applied by `tryHighlightRow` in both `search.html`
  (`partiallyReloadInvoiceTable`) and `list_inner.html` (`refreshListFragment`),
  keyed on `window.__RC_PENDING_LIST_HIGHLIGHT_MARK`. It fades after 5s.
