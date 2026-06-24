---
name: slow-login-cause
description: Why login AND save/delete were slow (Drive activity logging) and how it was fixed
metadata:
  type: project
---

Root cause for slow **login, save, and delete**: storage backend is **Google Drive** (DB `setting.storage_backend='drive'`), so every `firebase_config.firebase_log_activity` is a slow Drive API round-trip, and these were done **synchronously** in the request. `delete_undo` skips activity logging entirely — that's why undo always felt instant while delete/save were slow.

**Fixes (2026-06-24):**
- `utils.log_user_activity` now does its `firebase_log_activity` write in a **daemon thread with an app context** (fire-and-forget). This is the source-level fix and speeds up every caller: delete (`/delete`), save (`/save_summary`), fetch, export, etc. `delete_invoices` POST dropped to ~1s, matching undo.
- Login also: `firebase/firebase_auth_routes.py` `_log_login_activity_async(app, ...)` + `firebase/firebase_auth_handlers.py` `login_user` spawns a thread for its internal log. Login ~17s → ~2s. Remaining sync calls are `signInWithPassword` REST + `is_email_verified`→`get_user_by_email` (~1-2s each).
- Double table reload on save fixed with dedup wrappers: `partiallyReloadInvoiceTable` (search.html) and `refreshListFragmentDeduped` → `FBP_REFRESH_LIST_FRAGMENT` (list_inner.html).

Full page/script reference: `docs/SEARCH_PAGE_NOTES.md`. Related: [[dev-login-and-preview-testing]].
