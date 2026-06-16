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

## Persist the data folder across redeploys (Coolify) — READ THIS

**The biggest speed win.** The app stores all group data under
`/app/data` (it uses `BASE_DIR/data` and `os.getcwd()/data`, both `/app/data`
when `WORKDIR=/app`). If that folder is *not* on a persistent volume, every
redeploy starts from an empty `data/` and the warmup has to pull **everything**
from the remote — which is slow.

> ⚠️ The Dockerfile sets `ENV DATA_DIR=/data` / `UPLOADS_DIR=/uploads`, but the
> Python code does **not** read those env vars — it always uses `/app/data` and
> `/app/uploads`. So a Coolify mount whose **Destination Path** is `/data` does
> nothing. It must point at the real path.

**Coolify → Persistent Storage → Directories**, set the **Destination Path** to
the path the app actually uses:

| Source Path (host, keep as-is) | Destination Path (container) |
| --- | --- |
| `/data/coolify/applications/<id>` | **`/app/data`** |
| `/data/coolify/applications/<id>` | **`/app/uploads`** |

(i.e. change the existing `/data` mount to `/app/data` and `/uploads` to
`/app/uploads`.) After this, `data/` survives redeploys, so the warmup only
reconciles the few files that changed while the server was down → near-instant.

## Startup data warmup + maintenance gate

On every server start / redeploy, `firebase/startup_warmup.py` **reconciles**
each group's `data/<group_folder>/` with the active remote backend (Drive *or*
Firebase) **before** the app accepts logins. While that runs:

- Public visitors and the login page get a self-contained **maintenance page**
  (`templates/maintenance.html`, HTTP 503) that auto-polls and reloads when
  ready. It shows a percentage + `done / total` count (no group names, backend-
  neutral wording).
- All routes are gated except static assets and the readiness probe
  `GET /api/system/readiness` (alias `/healthz/ready`).

Behavior:

- **Reconcile, not blind pull** — per group it calls
  `compare_group_payload_freshness` and acts on the result: `push` when the
  **server holds newer data** (it wins and is backed up to the remote), `pull`
  when the remote is newer / local is empty, skip when equal. A server with
  fresher data is never clobbered.
- **Backend-agnostic** — works for both `drive` and `firebase` via the unified
  dispatcher.
- **Smart sync** — only changed files transfer, so with a persistent `data/`
  folder restarts are fast.
- **Multi-worker safe** — a JSON marker (`data/.drive_warmup_state.json`) plus a
  leader-election lock (`data/.drive_warmup.lock`) ensure one worker reconciles
  while the others follow the shared state. Works for `--workers 1` (Dockerfile)
  and `--workers 4` (start.sh).
- **Fail-open** — if the warmup stalls or errors (rate-limit, network, leader
  crash), the gate auto-unblocks after `DRIVE_WARMUP_MAX_SECONDS` (default
  **300s**) and logs a warning, so a remote outage can never brick the server.
  Remaining groups then reconcile lazily on first login.

Tune with `DRIVE_WARMUP_MAX_SECONDS` (env). The gate is a no-op when no remote
backend is usable.

## Admin panel — Remote Database Sync card

Admin → Settings → **🔄 Συγχρονισμός Απομακρυσμένης Βάσης** card (works for both
the Drive and Firebase backends):

- **Readiness badge** — startup warmup status (Έτοιμος / Φόρτωση / Fail-open).
- **Έλεγχος up-to-date** — `POST /admin/api/drive-sync/check` runs a per-group
  reconcile-direction check (`compare_group_payload_freshness`) and reports how
  many groups are pending push / pull, without transferring files.
- **Manual Push/Pull** — `POST /admin/api/drive-sync/run` runs a background
  push or pull for all groups or a single group (with optional `force`), routed
  to whichever backend is active. Status polled via
  `GET /admin/api/drive-sync/status`.

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
