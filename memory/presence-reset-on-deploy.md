---
name: presence-reset-on-deploy
description: Why all users showed "active" after every redeploy and the auto presence-reset fix
metadata:
  type: project
---

**Symptom:** after loading a new update/redeploy, the admin panel showed *every* user as active, and users were blocked from re-logging in ("ήδη ενεργός σε άλλη συσκευή").

**Root cause:** presence is computed purely from the local SQLite `User.last_active_at` via `models.py` `is_online(timeout_seconds=300)` — there is **no** Firestore presence sync (the user assumed there was). A redeploy starts a fresh process with zero live connections, but `firebed.db` persists `last_active_at`/`current_session_id` from just before the restart, so for ~5 min everyone recently active wrongly reads as online. The single-session lock in `firebase/firebase_auth_routes.py` (~line 500) then also blocks their re-login. A manual `scripts/clear_all_sessions.py` existed but had to be run by hand.

**Fix (2026-06-29):** `firebase/startup_warmup.py` `_reset_all_presence(app)` — clears `current_session_id`/`session_started_at`/`last_active_at` for all stale rows (folding the final session's elapsed time into `total_active_seconds` so stats stay accurate). Called at the top of `_run_warmup_as_leader`, so it runs **once per deploy** (leader election) while logins are still gated by the warmup `before_request` gate (`app.py` ~line 1093) → no race with a fresh login. Only the fully-`disabled` warmup path (no remote backend, i.e. local dev) skips it. Related: [[drive-pull-parallel-and-scraper-imports]], [[dev-login-and-preview-testing]].
