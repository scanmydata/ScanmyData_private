"""
rx_integration.py
=================
Integrates the Reflex frontend (scanmydata_rx/) with the Flask backend.

- Runs ``reflex init`` automatically if the .web directory is missing.
- Starts Reflex as a subprocess (frontend port 3000, backend port 8001).
- Detects readiness by polling the Reflex HTTP port directly — more
  reliable than grepping log lines.
- Provides a Flask Blueprint (rx_proxy) that proxies:
    HTTP:     /_next/*, /_reflex/*, and all page routes → Reflex frontend (3000)
    WebSocket: /_event/*  → Reflex state backend (8001)

Register with Flask:
    from rx_integration import rx_proxy, init_reflex, init_websocket_proxy
    app.register_blueprint(rx_proxy)
    init_websocket_proxy(app)
    init_reflex()   # starts Reflex subprocess once
"""

import os
import sys
import time
import threading
import subprocess
import logging

import requests
from flask import Blueprint, request, Response, stream_with_context

logger = logging.getLogger(__name__)

# ── Ports ─────────────────────────────────────────────────────────────────────
FLASK_PORT           = int(os.getenv("PORT", "5001"))
REFLEX_FRONTEND_PORT = int(os.getenv("REFLEX_FRONTEND_PORT", "3000"))
REFLEX_BACKEND_PORT  = int(os.getenv("REFLEX_BACKEND_PORT", "8001"))

REFLEX_FRONTEND_BASE = f"http://localhost:{REFLEX_FRONTEND_PORT}"
REFLEX_BACKEND_BASE  = f"http://localhost:{REFLEX_BACKEND_PORT}"

REFLEX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scanmydata_rx")

# ── Subprocess management ──────────────────────────────────────────────────────
_reflex_proc  = None
_reflex_ready = threading.Event()
_start_lock   = threading.Lock()
_started      = False


def _ensure_reflex_init(env: dict) -> None:
    """Run ``reflex init`` if the Next.js .web directory is missing.

    This is required on a fresh checkout or after deleting the build cache.
    The command is synchronous so the subsequent ``reflex run`` can succeed.
    """
    web_dir = os.path.join(REFLEX_DIR, ".web")
    if os.path.isdir(web_dir):
        return

    logger.info("Reflex .web directory not found — running 'reflex init' …")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "reflex", "init"],
            cwd=REFLEX_DIR,
            env=env,
            timeout=300,  # allow up to 5 min for npm install on slow machines
        )
        if result.returncode != 0:
            logger.error("'reflex init' exited with code %d", result.returncode)
        else:
            logger.info("'reflex init' completed successfully.")
    except subprocess.TimeoutExpired:
        logger.error("'reflex init' timed out after 5 minutes.")
    except Exception as exc:
        logger.exception("'reflex init' failed: %s", exc)


def _poll_ready() -> None:
    """Poll the Reflex frontend HTTP port until it accepts connections.

    Sets ``_reflex_ready`` as soon as the port responds — this is more
    robust than grepping log lines which vary between Reflex releases.
    """
    url = f"{REFLEX_FRONTEND_BASE}/"
    while not _reflex_ready.is_set():
        # If the subprocess has died, stop polling.
        if _reflex_proc is not None and _reflex_proc.poll() is not None:
            logger.error(
                "Reflex subprocess exited (code %d) before becoming ready.",
                _reflex_proc.returncode,
            )
            break
        try:
            resp = requests.get(url, timeout=3, allow_redirects=False)
            # Any HTTP response (including 404) means the server is up.
            if resp.status_code < 600:
                logger.info(
                    "Reflex frontend is ready (HTTP %d on :%d).",
                    resp.status_code, REFLEX_FRONTEND_PORT,
                )
                _reflex_ready.set()
                return
        except requests.ConnectionError:
            pass  # still starting — wait and retry
        except Exception as exc:
            logger.debug("Reflex poll error (will retry): %s", exc)
        time.sleep(2)


def _run_reflex() -> None:
    global _reflex_proc
    env = os.environ.copy()
    env.update({
        "REFLEX_FRONTEND_PORT": str(REFLEX_FRONTEND_PORT),
        "REFLEX_BACKEND_PORT":  str(REFLEX_BACKEND_PORT),
        # Tell Reflex where the browser should connect for WebSocket events.
        "REFLEX_API_URL": os.getenv("REFLEX_API_URL",
                                     f"http://localhost:{FLASK_PORT}"),
        # Suppress Reflex telemetry prompts in non-interactive environments.
        "TELEMETRY_ENABLED": "false",
    })

    # Ensure the Reflex project is initialised before trying to run it.
    _ensure_reflex_init(env)

    try:
        _reflex_proc = subprocess.Popen(
            [sys.executable, "-m", "reflex", "run", "--env", "dev",
             "--frontend-port", str(REFLEX_FRONTEND_PORT),
             "--backend-port",  str(REFLEX_BACKEND_PORT)],
            cwd=REFLEX_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )

        # Start the HTTP-based readiness poller in parallel.
        poll_thread = threading.Thread(
            target=_poll_ready, name="reflex-poll", daemon=True
        )
        poll_thread.start()

        # Stream subprocess output to the logger (for debugging).
        for raw in _reflex_proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            logger.info("[reflex] %s", line)

        _reflex_proc.wait()
        if _reflex_proc.returncode != 0:
            logger.error(
                "Reflex subprocess exited unexpectedly (code %d).",
                _reflex_proc.returncode,
            )
    except Exception as exc:
        logger.exception("Reflex subprocess error: %s", exc)


def init_reflex():
    """Start Reflex once in a background daemon thread."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    t = threading.Thread(target=_run_reflex, name="reflex-server", daemon=True)
    t.start()
    logger.info(
        "Reflex subprocess starting (frontend :%d, backend :%d) …",
        REFLEX_FRONTEND_PORT, REFLEX_BACKEND_PORT,
    )


# ── Flask Blueprint ────────────────────────────────────────────────────────────
rx_proxy = Blueprint("rx_proxy", __name__)

# Flask-handled path prefixes — these are NEVER proxied to Reflex
_FLASK_PREFIXES = (
    "/api/", "/auth/", "/firebase-auth/",
    "/credentials/add", "/credentials/delete/", "/credentials/set_active",
    "/credentials/save_settings", "/credentials/get_settings",
    "/upload_", "/remove_chart", "/get_chart", "/client_db_info",
    "/set_active", "/set_fiscal_year", "/get_fiscal_year",
    "/save_epsilon", "/save_accounts", "/save_summary", "/save_receipt",
    "/export/", "/delete", "/health", "/_debug_log",
    "/favicon.ico", "/icons/", "/mobile/",
)


def _should_flask_handle(path: str) -> bool:
    for prefix in _FLASK_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _proxy_http_to_reflex(path: str) -> Response:
    """Forward an HTTP request to the Reflex Next.js frontend."""
    qs = request.query_string.decode("utf-8")
    target = f"{REFLEX_FRONTEND_BASE}{path}" + (f"?{qs}" if qs else "")

    skip_req = {"host", "transfer-encoding", "connection", "te",
                "trailers", "upgrade", "keep-alive", "proxy-authorization"}
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in skip_req}
    try:
        upstream = requests.request(
            method=request.method,
            url=target,
            headers=headers,
            data=request.get_data(),
            allow_redirects=False,
            timeout=20,
            stream=True,
        )
    except requests.ConnectionError:
        return Response(
            """<!doctype html>
<html><head>
<meta http-equiv="refresh" content="5">
<title>ScanmyData — Starting…</title>
<style>
 body{font-family:sans-serif;display:flex;align-items:center;
      justify-content:center;height:100vh;margin:0;
      background:#111827;color:#f9fafb}
 .card{text-align:center;padding:40px;border-radius:16px;
       background:#1f2937;box-shadow:0 8px 32px rgba(0,0,0,.4)}
 h2{color:#38bdf8}p{color:#9ca3af}
</style>
</head><body>
<div class="card">
  <h2>⏳ Starting ScanmyData…</h2>
  <p>The application is compiling. This page will reload automatically.</p>
</div>
</body></html>""",
            status=503,
            content_type="text/html",
        )

    skip_resp = {"transfer-encoding", "connection"}
    resp_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() not in skip_resp}

    return Response(
        stream_with_context(upstream.iter_content(chunk_size=8192)),
        status=upstream.status_code,
        headers=resp_headers,
    )


# ── WebSocket proxy (/_event/*) ────────────────────────────────────────────────
try:
    from flask_sock import Sock as _Sock
    import websocket as _wsclient
    _ws_proxy_available = True
except ImportError:
    _ws_proxy_available = False
    logger.warning(
        "flask-sock / websocket-client not installed — "
        "Reflex state WebSocket proxy disabled. "
        "Run: pip install flask-sock websocket-client"
    )


def init_websocket_proxy(app):
    """Attach the /_event WebSocket proxy to the given Flask app."""
    if not _ws_proxy_available:
        return

    sock = _Sock(app)

    @sock.route("/_event/<path:event_path>")
    def reflex_ws_proxy(ws, event_path):  # noqa: F811
        qs = request.query_string.decode("utf-8")
        target_url = (
            f"ws://localhost:{REFLEX_BACKEND_PORT}/_event/{event_path}"
            + (f"?{qs}" if qs else "")
        )
        try:
            backend_ws = _wsclient.WebSocket()
            backend_ws.connect(target_url, timeout=10)
        except Exception as exc:
            logger.warning("Reflex WS backend not ready: %s", exc)
            ws.close(message=b"backend not ready")
            return

        def _fwd_client_to_backend():
            try:
                while True:
                    data = ws.receive()
                    if data is None:
                        break
                    backend_ws.send(data)
            except Exception:
                pass
            finally:
                try:
                    backend_ws.close()
                except Exception:
                    pass

        t = threading.Thread(target=_fwd_client_to_backend, daemon=True)
        t.start()

        try:
            while True:
                op, data = backend_ws.recv_data()
                if op == _wsclient.ABNF.OPCODE_TEXT:
                    ws.send(data.decode("utf-8"))
                elif op == _wsclient.ABNF.OPCODE_BINARY:
                    ws.send_bytes(data)
                elif op == _wsclient.ABNF.OPCODE_CLOSE:
                    break
        except Exception:
            pass


# ── HTTP proxy routes ──────────────────────────────────────────────────────────

@rx_proxy.route("/_next/<path:path>", methods=["GET", "HEAD"])
@rx_proxy.route("/_reflex/<path:path>", methods=["GET", "HEAD"])
def proxy_static(path):
    """Proxy Next.js static assets to the Reflex frontend."""
    return _proxy_http_to_reflex(request.path)


# Catch-all: proxy all remaining GET requests to Reflex page renderer.
# Flask's own routes (more specific) take priority over this catch-all.
@rx_proxy.route("/", defaults={"path": ""}, methods=["GET"])
@rx_proxy.route("/<path:path>",             methods=["GET"])
def proxy_page(path):
    """Proxy page requests to the Reflex frontend."""
    full = "/" + path if path else "/"
    if _should_flask_handle(full):
        # Fall through to Flask's own 404 handler; its routes won't re-match here
        from flask import abort
        abort(404)
    return _proxy_http_to_reflex(full)
