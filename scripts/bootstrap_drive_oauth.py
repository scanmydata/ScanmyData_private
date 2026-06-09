"""One-time OAuth bootstrap for the Google Drive storage backend.

Run this whenever you need to (re)generate the refresh token — typically:
  - First-time setup on a new machine
  - After revoking the previous token at https://myaccount.google.com/permissions

Usage:
    python scripts/bootstrap_drive_oauth.py

What it does:
  1. Reads google_client_id / google_client_secret from env (loaded from
     Infisical via the standard bootstrap chain).
  2. Opens a browser for OAuth consent (Google account login + grant Drive
     access).
  3. Writes refresh + access token to drive_token.json (for local dev use).
  4. Prints the refresh token to stdout — copy this into Infisical as
     google_drive_refresh_token (prod environment, root path).

Prereq: the OAuth client must have http://localhost:8765/ in its
Authorized redirect URIs.
"""
import os
import sys
import webbrowser
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer

# Make project root importable when run from scripts/ subdir.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)  # so drive_token.json lands in project root

from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

load_dotenv()
try:
    from infisical_bootstrap import bootstrap_infisical_secrets
    bootstrap_infisical_secrets()
except Exception as e:
    print(f"[warn] Infisical bootstrap failed: {e}")

from google_auth_oauthlib.flow import Flow  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/drive"]
REDIRECT_URI = "http://localhost:8765/"
LOCAL_PORT = 8765
TOKEN_FILE = "drive_token.json"


def _client_config():
    client_id = (os.getenv("google_client_id") or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("google_client_secret") or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        sys.exit("[error] Missing google_client_id / google_client_secret in env (Infisical).")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }


def _wait_for_callback() -> str:
    captured: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                captured["code"] = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<h2>Authorization received.</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                )
            elif "error" in qs:
                captured["error"] = qs["error"][0]
                self.send_response(400); self.end_headers()
                self.wfile.write(b"Auth error.")
            else:
                self.send_response(404); self.end_headers()

        def log_message(self, fmt, *args):
            pass

    httpd = HTTPServer(("localhost", LOCAL_PORT), Handler)
    while not captured:
        httpd.handle_request()
    httpd.server_close()
    if "error" in captured:
        sys.exit(f"[error] OAuth error: {captured['error']}")
    return captured["code"]


def main():
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    print("[auth] Opening browser:")
    print(f"       {auth_url}")
    print(f"[auth] Waiting for callback on {REDIRECT_URI} ...")
    try:
        webbrowser.open(auth_url, new=2)
    except Exception:
        pass

    code = _wait_for_callback()
    flow.fetch_token(code=code)
    creds = flow.credentials

    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    print(f"[ok] drive_token.json written for local dev use.")

    if not creds.refresh_token:
        sys.exit("[error] Google returned no refresh_token. Revoke prior grant and retry.")

    print("")
    print("=" * 70)
    print("ADD THIS SECRET TO INFISICAL (prod env, root path):")
    print(f"  google_drive_refresh_token = {creds.refresh_token}")
    print("=" * 70)
    print("Once added, the server will use it automatically on startup.")


if __name__ == "__main__":
    main()
