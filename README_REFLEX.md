# ScanmyData — Reflex Frontend (Phase 1)

This directory contains the new Python-native frontend built with [Reflex](https://reflex.dev),
replacing the previous HTML/JS/Jinja2 templates.

## Architecture

```
Flask backend (port 5000)   ←─httpx─  Reflex state files
Reflex backend (port 8000)  ←─WS──    Reflex frontend (port 3000)
```

- **Reflex backend (port 8000)** handles state WebSocket updates (Reflex's own protocol).
- **Flask backend (port 5000)** serves the REST JSON API for data (credentials, invoices, etc.).
- State files use `httpx` to call Flask; `rxconfig.py` only configures the Reflex backend.

The Reflex app uses the existing Flask session cookies for Firebase authentication.

## Setup

```bash
# 1. Install dependencies (Python 3.10+)
pip install "reflex>=0.8.28"

# 2. Also install httpx (used by state files to call Flask API)
pip install httpx

# 3. Navigate to the Reflex app directory (IMPORTANT: must cd here first)
cd reflex_app

# 4. Initialize Reflex (first time only — downloads Node.js dependencies)
reflex init

# 5. Start Flask backend in another terminal (required for data)
cd ..
python app.py   # runs on http://localhost:5000

# 6. Run Reflex in development mode
cd reflex_app
reflex run      # runs on http://localhost:3000
```

## Configuration

| File | Purpose |
|------|---------|
| `reflex_app/rxconfig.py` | Reflex ports (frontend: 3000, backend: 8000) |
| `reflex_app/scanmydata/config.py` | Flask API URL (`FLASK_API_BASE`) |

To point the Reflex frontend at a different Flask instance, set the environment variable:

```bash
export SCANMYDATA_API_URL="http://your-flask-host:5000"
reflex run
```

> **Important**: `api_url` in `rxconfig.py` is Reflex's *own* WebSocket backend URL —
> do **not** set it to Flask's port. Flask's URL belongs only in `config.py`.

## Pages Implemented (Phase 1)

| Route | Page | Status |
|-------|------|--------|
| `/` | Home (dashboard) | ✅ Done |
| `/credentials` | Credentials management | ✅ Done |
| `/fetch` | MYDATA fetch | ✅ Done |
| `/list` | Invoice list | ✅ Done |
| `/search` | MARK search | ✅ Done |
| `/login` | Login redirect | ✅ Done |

## Phases Roadmap

- **Phase 1** (this PR): Setup + shared components + core pages
- **Phase 2**: Modals, Epsilon Preview, Custom Categories
- **Phase 3**: Admin Panel (users, groups, backups, logs)
- **Phase 4**: Auth pages, session management, mobile polish
