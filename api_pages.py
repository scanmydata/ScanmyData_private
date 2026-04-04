"""JSON API endpoints for the ScanmyData Reflex frontend.

This blueprint exposes:
  GET  /api/auth/status          – auth + session state for Reflex
  GET  /api/page/fetch           – fetch-page initial data
  GET  /api/page/credentials     – credentials list
  GET  /api/page/search          – search page initial data
  GET  /api/page/admin/stats     – admin overview stats
  GET  /api/page/admin/users     – admin users list
  GET  /api/page/admin/groups    – admin groups list
  GET  /api/page/admin/activity  – admin activity logs
  GET  /api/page/admin/settings  – admin settings

All existing /api/... routes in app.py are left untouched.
"""

import os
from datetime import datetime

from flask import Blueprint, jsonify, session, request, redirect

api_pages_bp = Blueprint("api_pages", __name__)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _get_current_user():
    try:
        from flask_login import current_user
        return current_user
    except Exception:
        return None


def _is_admin(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    try:
        import admin_panel
        return admin_panel.is_admin(user)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# GET /api/auth/status
# ---------------------------------------------------------------------------

@api_pages_bp.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    """Return current authentication state for the Reflex frontend."""
    user = _get_current_user()
    authenticated = bool(user and getattr(user, "is_authenticated", False))

    username = ""
    email = ""
    is_admin = False
    if authenticated:
        username = getattr(user, "username", "") or getattr(user, "display_name", "") or ""
        email = getattr(user, "email", "") or ""
        is_admin = _is_admin(user)

    current_year = datetime.now().year
    available_years = [str(y) for y in range(current_year, 2018, -1)]

    active_year = session.get("active_year", "")
    if not active_year:
        try:
            from app import get_active_fiscal_year
            fy = get_active_fiscal_year()
            active_year = str(fy) if fy else str(current_year)
        except Exception:
            active_year = str(current_year)

    active_cred = session.get("active_credential", "")
    active_cred_vat = ""
    if active_cred:
        try:
            from app import get_cred_by_name
            cred = get_cred_by_name(active_cred) or {}
            active_cred_vat = str(cred.get("vat") or "")
        except Exception:
            pass

    return jsonify({
        "authenticated": authenticated,
        "username": username,
        "email": email,
        "is_admin": is_admin,
        "active_credential": active_cred,
        "active_credential_vat": active_cred_vat,
        "active_group": session.get("active_group", ""),
        "active_year": active_year,
        "available_years": available_years,
    })


# ---------------------------------------------------------------------------
# GET /api/page/fetch
# ---------------------------------------------------------------------------

@api_pages_bp.route("/api/page/fetch", methods=["GET"])
def api_page_fetch():
    """Return fetch-page initial data as JSON."""
    user = _get_current_user()
    if not user or not getattr(user, "is_authenticated", False):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from app import load_credentials, get_active_credential_from_session
        from app import get_last_fetch_date, _get_fetch_tracking_key, _format_last_fetch_date_for_display
    except ImportError:
        return jsonify({"error": "Internal error"}), 500

    creds = load_credentials() or []
    active_cred = get_active_credential_from_session()
    active_name = (active_cred or {}).get("name", "")
    initial_vat = str((active_cred or {}).get("vat") or "").strip()

    last_fetch_display = None
    try:
        key = _get_fetch_tracking_key(active_name, initial_vat) if active_name else None
        if key:
            raw = get_last_fetch_date(key, only_meta=True) or get_last_fetch_date(key, only_meta=False)
            last_fetch_display = _format_last_fetch_date_for_display(raw)
    except Exception:
        pass

    today = datetime.now()
    first_of_month = today.replace(day=1).strftime("%d/%m/%Y")
    today_str = today.strftime("%d/%m/%Y")

    credentials_list = [
        {"name": c.get("name", ""), "vat": str(c.get("vat") or ""), "active": c.get("name") == active_name}
        for c in creds
    ]

    return jsonify({
        "credentials": credentials_list,
        "active_credential": active_name,
        "vat_number": initial_vat,
        "default_from": first_of_month,
        "default_to": today_str,
        "last_fetch_date_display": last_fetch_display or "",
    })


# ---------------------------------------------------------------------------
# GET /api/page/credentials
# ---------------------------------------------------------------------------

@api_pages_bp.route("/api/page/credentials", methods=["GET"])
def api_page_credentials():
    """Return credentials list as JSON."""
    user = _get_current_user()
    if not user or not getattr(user, "is_authenticated", False):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        from app import load_credentials, get_active_credential_from_session
    except ImportError:
        return jsonify({"error": "Internal error"}), 500

    creds = load_credentials() or []
    active_cred = get_active_credential_from_session()
    active_name = (active_cred or {}).get("name", "")

    credentials_list = [
        {
            "name": c.get("name", ""),
            "vat": str(c.get("vat") or ""),
            "username": str(c.get("user") or c.get("username") or ""),
            "active": c.get("name") == active_name,
        }
        for c in creds
    ]

    return jsonify({"credentials": credentials_list, "active_credential": active_name})


# ---------------------------------------------------------------------------
# GET /api/page/search
# ---------------------------------------------------------------------------

@api_pages_bp.route("/api/page/search", methods=["GET"])
def api_page_search():
    """Return search-page initial state as JSON."""
    user = _get_current_user()
    if not user or not getattr(user, "is_authenticated", False):
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify({"results": [], "columns": [], "total": 0})


# ---------------------------------------------------------------------------
# Admin page data endpoints
# ---------------------------------------------------------------------------

@api_pages_bp.route("/api/page/admin/stats", methods=["GET"])
def api_page_admin_stats():
    user = _get_current_user()
    if not user or not getattr(user, "is_authenticated", False):
        return jsonify({"error": "Unauthorized"}), 401
    if not _is_admin(user):
        return jsonify({"error": "Admin access required"}), 403

    try:
        import admin_panel
        raw = admin_panel.admin_get_system_stats() or {}
    except Exception:
        raw = {}

    stats = {
        "users": str(raw.get("users", "—")),
        "groups": str(raw.get("groups", "—")),
        "activity_24h": str(raw.get("activity_24h", "—")),
        "system_status": str(raw.get("system_status", "OK")),
    }

    recent = []
    for entry in (raw.get("recent_activity") or [])[:20]:
        recent.append({
            "time": str(entry.get("time") or entry.get("timestamp") or ""),
            "user": str(entry.get("user") or entry.get("username") or ""),
            "action": str(entry.get("action") or entry.get("event") or ""),
            "ip": str(entry.get("ip") or ""),
        })

    return jsonify({"stats": stats, "recent_activity": recent})


@api_pages_bp.route("/api/page/admin/users", methods=["GET"])
def api_page_admin_users():
    user = _get_current_user()
    if not user or not getattr(user, "is_authenticated", False):
        return jsonify({"error": "Unauthorized"}), 401
    if not _is_admin(user):
        return jsonify({"error": "Admin access required"}), 403

    try:
        import admin_panel
        raw_users = admin_panel.admin_list_all_users() or []
    except Exception:
        raw_users = []

    users = []
    for u in raw_users:
        users.append({
            "uid": str(u.get("uid") or u.get("id") or ""),
            "email": str(u.get("email") or ""),
            "display_name": str(u.get("display_name") or u.get("username") or "—"),
            "disabled": bool(u.get("disabled", False)),
            "creation_time": str(u.get("creation_time") or u.get("created_at") or ""),
        })

    return jsonify({"users": users})


@api_pages_bp.route("/api/page/admin/groups", methods=["GET"])
def api_page_admin_groups():
    user = _get_current_user()
    if not user or not getattr(user, "is_authenticated", False):
        return jsonify({"error": "Unauthorized"}), 401
    if not _is_admin(user):
        return jsonify({"error": "Admin access required"}), 403

    try:
        import admin_panel
        raw_groups = admin_panel.admin_list_all_groups() or []
    except Exception:
        raw_groups = []

    groups = []
    for g in raw_groups:
        groups.append({
            "id": str(g.get("id") or ""),
            "name": str(g.get("name") or ""),
            "member_count": int(g.get("member_count") or 0),
            "created_at": str(g.get("created_at") or ""),
        })

    return jsonify({"groups": groups})


@api_pages_bp.route("/api/page/admin/activity", methods=["GET"])
def api_page_admin_activity():
    user = _get_current_user()
    if not user or not getattr(user, "is_authenticated", False):
        return jsonify({"error": "Unauthorized"}), 401
    if not _is_admin(user):
        return jsonify({"error": "Admin access required"}), 403

    limit = request.args.get("limit", 100, type=int)
    group_name = request.args.get("group")

    try:
        import admin_panel
        raw_logs = admin_panel.admin_get_activity_logs(group_name, limit) or []
    except Exception:
        raw_logs = []

    logs = []
    for entry in raw_logs:
        logs.append({
            "time": str(entry.get("time") or entry.get("timestamp") or ""),
            "user": str(entry.get("user") or entry.get("username") or ""),
            "action": str(entry.get("action") or entry.get("event") or ""),
            "ip": str(entry.get("ip") or ""),
        })

    return jsonify({"logs": logs})


@api_pages_bp.route("/api/page/admin/settings", methods=["GET"])
def api_page_admin_settings():
    user = _get_current_user()
    if not user or not getattr(user, "is_authenticated", False):
        return jsonify({"error": "Unauthorized"}), 401
    if not _is_admin(user):
        return jsonify({"error": "Admin access required"}), 403

    try:
        from app import load_settings
        settings = load_settings() or {}
    except Exception:
        settings = {}

    return jsonify(settings)
