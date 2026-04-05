# ScanmyData — Reflex Frontend (Phase 1)

This directory contains the new Python-native frontend built with [Reflex](https://reflex.dev),
replacing the previous HTML/JS/Jinja2 templates.

## Architecture

```
Flask backend (port 5000)  ←→  Reflex frontend (port 3000)
        ↑
   REST JSON API
```

The Reflex frontend calls Flask REST API endpoints for all data operations.
Firebase authentication is still handled by Flask; the Reflex app uses the
existing session cookies from the Flask login flow.

## Setup

```bash
# 1. Install dependencies
pip install reflex>=0.8.28

# 2. Navigate to the Reflex app
cd reflex_app

# 3. Initialize Reflex (first time only)
reflex init

# 4. Run in development mode
reflex run

# Or export for production
reflex export
```

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

## Configuration

Edit `reflex_app/rxconfig.py` to change:
- `api_url`: Flask backend URL (default: `http://localhost:5000`)
- `frontend_port`: Reflex dev server port (default: `3000`)
