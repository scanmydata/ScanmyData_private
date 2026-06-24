---
name: dev-login-and-preview-testing
description: How to log in and drive the local app in the preview browser for live testing
metadata:
  type: project
---

Local test login (per `copilot-instructions/copilot-instructions.md`): app at the Flask dev server (launch.json `flask-app`, autoPort). Login `douradonis@hotmail.com` / `12345678` at `/firebase-auth/login`. Then select group **tony** (POST `/groups/select` `{group:'tony'}`) and active customer **ΛΟΥΓΑΡΗΣ ΣΠΥΡΟΣ ΠΑΝΑΓΙΩΤΗΣ** AFM 036209456 (POST `/set_active` form `active_name=...`, needs `X-Requested-With: XMLHttpRequest` for JSON).

Two gotchas when testing in the Claude Preview browser:
1. **Single-session lock**: a user can have only one live session (DB `user.current_session_id` + file locks in `data/sessions/`). If the real browser is logged in, the preview can't log in as the same user — ask the user to log out first.
2. **`static/session_manager.js` logout-on-unload**: it fires a `navigator.sendBeacon('/auth/api/logout')` on every `beforeunload`, so every full-page `window.location` nav in the preview logs you out. Real users avoid this because the app navigates via partial/AJAX. For preview testing, override `navigator.sendBeacon = ()=>true` on each page right after it loads, before navigating.

Storage backend setting is `drive` (DB `setting` table) — every `firebase_log_activity` is a slow Google Drive API write. See [[slow-login-cause]].
