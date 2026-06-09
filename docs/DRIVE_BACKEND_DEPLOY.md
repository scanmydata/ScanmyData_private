# Google Drive Storage Backend — Deploy Guide

## Overview

Group data (encrypted user files + activity logs) is stored in Google Drive
under the folder `Scanmydata_data/<group_folder>/...`. The Firebase Realtime
Database backend remains available as a fallback — controlled by a runtime
toggle.

| Layer | Location |
| --- | --- |
| Auth | Firebase Authentication (unchanged) |
| File storage | `Scanmydata_data/<group_folder>/...` on Google Drive |
| Activity logs | `Scanmydata_data/<group_folder>/activity.log` on Drive |
| User/group DB | SQLite `firebed.db` (unchanged) |

The active backend is controlled by `Setting.storage_backend` (`drive` or
`firebase`), togglable from `/admin/settings`. Default is `drive`.

## Secrets

The Drive backend uses OAuth user credentials so it consumes the owner's
Drive quota (not a service-account quota of 0). Three secrets must be present
in env (loaded from Infisical at startup):

| Secret | Where | Description |
| --- | --- | --- |
| `google_client_id` | Infisical prod root | OAuth 2.0 client ID (Web app type) ending in `.apps.googleusercontent.com` |
| `google_client_secret` | Infisical prod root | OAuth 2.0 client secret (`GOCSPX-...`) |
| `google_drive_refresh_token` | Infisical prod root | Long-lived refresh token tied to your Google account |

The OAuth Web client must have **`http://localhost:8765/`** in its
Authorized redirect URIs (used only during the one-time bootstrap flow).

## First-time / refresh-token rotation

Whenever you need a fresh refresh token (first deploy, after revocation,
new account), run:

```bash
python scripts/bootstrap_drive_oauth.py
```

This:
1. Opens a browser → you grant Drive access to your Google account.
2. Writes `drive_token.json` locally (gitignored — for local dev).
3. Prints the refresh token to stdout. **Copy it into Infisical as
   `google_drive_refresh_token` in the prod environment.**

Once the secret is in Infisical, the server reads it via
`infisical_bootstrap` on startup and `firebase.drive_storage.init_drive()`
uses it to mint access tokens automatically.

### Token leak / revocation

If a refresh token is exposed (logged, pasted in chat, committed by accident):

1. Go to [https://myaccount.google.com/permissions](https://myaccount.google.com/permissions).
2. Find the OAuth client (named after the app) and click **Revoke access**.
3. Delete local `drive_token.json` if present.
4. Run `python scripts/bootstrap_drive_oauth.py` again.
5. Replace `google_drive_refresh_token` in Infisical with the new value.

## Switching the backend at runtime

Admin → Settings → **Storage Backend** card → pick `drive` or `firebase`.

Switching takes effect immediately for all subsequent push/pull/log
operations. Confirm both backends contain the desired data before flipping,
or users may see empty data after login.

Programmatic toggle (e.g. from a Python shell):

```python
from app import app
with app.app_context():
    from models import Setting
    Setting.set('storage_backend', 'drive')   # or 'firebase'
```

## Initial data migration (already completed)

A one-time RTDB → Drive migration was performed at backend cutover. If you
need to repeat it (e.g. after a Drive folder wipe), the migration logic
lives in two places:

- Read side: `firebase_pull_group_to_local` reads from RTDB when
  `STORAGE_BACKEND=firebase`.
- Write side: `drive_push_group_files` writes to Drive directly.

The orchestration script that loops over all `/groups/*` in RTDB and runs
pull-then-push was deleted after the one-time use. Reconstruct it if needed
or restore from chat history.

## How the dispatcher works

`firebase/firebase_config.py` exposes the same public functions as before
(`firebase_push_group_files`, `firebase_pull_group_to_local`,
`firebase_log_activity`, `ensure_group_data_local`). Each starts with a
check of `_drive_backend_active()` and delegates to
`firebase/drive_storage.py` when true. **Zero call-site changes were
required across the codebase (~70 sites).**

## Drive folder layout

```
Scanmydata_data/
  <group_folder>/
    <file_name>                         # encrypted bytes (Fernet)
    excel/<name>.xlsx
    epsilon/<afm>_epsilon_invoices.json
    imports/<name>.xlsx
    misth_pdfs/uid_<id>/<afm>/<file>.pdf
    activity.log                        # NDJSON, plaintext, append-only
```

Each file's `appProperties.local_mtime` holds the original local mtime as a
float string — used by smart-sync to skip unchanged files on subsequent
pushes.

## Operational notes

- Drive's `httplib2` transport is not safe to share across many calls — the
  module builds a fresh service per call (cached credentials only).
- Smart-sync is enabled by default (`FIREBASE_SMART_SYNC=1`). Pass
  `force=True` to push/pull to bypass.
- `epsilon/` and `excel/` subdirectories are always synced (no skipping)
  to match the legacy RTDB behavior.
- Smoke-test the server end-to-end after deploying — login a real user,
  trigger a sync, log an action, log out, and confirm the file appears
  under `Scanmydata_data/<their_group>/` in Drive.
