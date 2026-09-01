# -*- coding: utf-8 -*-
"""
myamka_http.py — καθαρή HTTP/BFF ανάκτηση ΑΜΚΑ από το MyAMKA (www.amka.gr/app).

Πιστή μεταφορά σε Python του δοκιμασμένου Node config `amka-retrieve.js`
(runner/configs/amka-retrieve.js) + των helpers `myAmkaLogin` / `myAmkaApi` /
`gsisSubmitAndApprove` (runner/lib/hyper-http.js).

Ροή:
    GET  /app/oauth/login            -> GSIS OAuth2 (login.jsp)
    POST oauth2server/j_spring_security_check  (j_username/j_password)
    POST oauth2server/oauth/authorize          (user_oauth_approval=true)  [αν ζητηθεί]
    GET  /app/oauth/token            -> bearer token (καθαρό κείμενο)
    POST /app/api/AmkaCitizenAppService/CTZ_GetPersonAmka  (Authorization: ctaf2 <token>)
         -> { amkaList: [...], ... }

Χρησιμοποιείται ΩΣ FALLBACK όταν αποτύχει η κύρια (Playwright/AADE) διαδικασία.
Επιστρέφει το ΑΜΚΑ του ΚΑΤΟΧΟΥ των κωδικών TAXISnet (myAMKA), γι' αυτό το
αποτέλεσμα σημειώνεται ρητά ως source="myamka".

Καμία εξάρτηση πέρα από `requests` (ήδη διαθέσιμο στην εφαρμογή).
"""
from __future__ import annotations

import re
import json
import logging
from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

log = logging.getLogger(__name__)

APP = "https://www.amka.gr/app"
GSIS = "https://oauth2.gsis.gr/oauth2server"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
_ACCEPT_LANG = "el-GR,el;q=0.9,en;q=0.8"

# JSF partial-response redirect: <partial-response><redirect url="..."></redirect>
_PARTIAL_REDIRECT = re.compile(r'redirect url="([^"]*)"')


def _collect_amka(node: Any, acc: set) -> None:
    """Μάζεψε 11ψήφιες τιμές που μοιάζουν με ΑΜΚΑ (DD<=31, MM<=12) από json."""
    if node is None:
        return
    if isinstance(node, (str, int)):
        s = str(node)
        if re.fullmatch(r"\d{11}", s):
            dd, mm = int(s[0:2]), int(s[2:4])
            if 1 <= dd <= 31 and 1 <= mm <= 12:
                acc.add(s)
        return
    if isinstance(node, list):
        for x in node:
            _collect_amka(x, acc)
        return
    if isinstance(node, dict):
        for v in node.values():
            _collect_amka(v, acc)


class _HyperSession:
    """Λεπτό wrapper γύρω από requests.Session: follow() που ακολουθεί ΚΑΙ τα
    HTTP 3xx ΚΑΙ τα JSF <partial-response> redirects (όπως το lib/hyper-http.js)."""

    def __init__(self, timeout: float = 30.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": _UA, "Accept-Language": _ACCEPT_LANG})
        self.timeout = timeout

    def follow(self, method: str, url: str, form: Optional[Dict[str, str]] = None):
        headers = {"Accept": _ACCEPT_HTML}
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        # requests ακολουθεί μόνο του τα HTTP 3xx
        res = self.s.request(method, url, data=form, headers=headers,
                             allow_redirects=True, timeout=self.timeout)
        text = res.text or ""
        cur = res.url
        guard = 0
        # ακολούθησε τα JSF partial-response redirects (βρίσκονται στο body)
        while "<partial-response><redirect url=" in text and guard < 12:
            m = _PARTIAL_REDIRECT.search(text)
            if not m:
                break
            nxt = requests.compat.urljoin(cur, m.group(1).replace("&amp;", "&"))
            res = self.s.get(nxt, headers={"Accept": _ACCEPT_HTML},
                             allow_redirects=True, timeout=self.timeout)
            text = res.text or ""
            cur = res.url
            guard += 1
        return {"url": cur, "status": res.status_code, "text": text}

    def api(self, method: str, url: str, body_obj: Any = None,
            headers: Optional[Dict[str, str]] = None):
        h = {"Accept": "application/json, text/plain, */*",
             "Accept-Language": _ACCEPT_LANG}
        if headers:
            h.update(headers)
        data = None
        if body_obj is not None:
            h.setdefault("Content-Type", "application/json")
            data = body_obj if isinstance(body_obj, str) else json.dumps(body_obj)
        res = self.s.request(method, url, data=data, headers=h,
                             allow_redirects=False, timeout=self.timeout)
        return {"status": res.status_code, "text": res.text or "",
                "ct": (res.headers.get("content-type") or "").lower()}


def _gsis_submit_and_approve(http: _HyperSession, user: str, password: str) -> Dict[str, Any]:
    """GSIS OAuth2: υποβολή διαπιστευτηρίων + έγκριση (confirmationForm)."""
    rl = http.follow("POST", f"{GSIS}/j_spring_security_check",
                     {"j_username": user, "j_password": password})
    low = rl["text"] or ""
    if str(rl["url"]).endswith("authentication_error=true") or 'name="j_password"' in low:
        return {"ok": False, "reason": "InvalidCredentials"}
    if re.search(r'id="confirmationForm"', low, re.I) or re.search(r"user_oauth_approval", low, re.I):
        http.follow("POST", f"{GSIS}/oauth/authorize",
                    {"user_oauth_approval": "true", "scope.read": "true"})
    return {"ok": True}


def retrieve_amka(user: str, password: str, timeout: float = 30.0) -> Dict[str, Any]:
    """Ανάκτηση ΑΜΚΑ του κατόχου των κωδικών TAXISnet μέσω MyAMKA BFF.

    Επιστρέφει: {ok, amka, amkaList, source:"myamka"[, reason, raw]}.
    Ασφαλές fallback — δεν πετάει exception προς τα έξω· επιστρέφει ok=False.
    """
    if requests is None:
        return {"ok": False, "reason": "requests-unavailable"}
    if not user or not password:
        return {"ok": False, "reason": "MissingCredentials"}

    try:
        http = _HyperSession(timeout=timeout)
        # 1) login MyAMKA (GSIS OAuth2 BFF)
        http.follow("GET", f"{APP}/oauth/login")
        g = _gsis_submit_and_approve(http, user, password)
        if not g.get("ok"):
            return {"ok": False, "reason": g.get("reason") or "LoginFailed", "source": "myamka"}

        # 2) bearer token
        t = http.api("GET", f"{APP}/oauth/token", None, {})
        token = (t.get("text") or "").strip()
        if t.get("status") != 200 or not token or (len(token) > 4000 and "<html" in token.lower()):
            return {"ok": False, "reason": "NoToken", "source": "myamka"}

        # 3) CTZ_GetPersonAmka (Authorization: ctaf2 <token>)
        r = http.api("POST", f"{APP}/api/AmkaCitizenAppService/CTZ_GetPersonAmka",
                     {}, {"Authorization": "ctaf2 " + token})
        if r.get("status") != 200:
            return {"ok": False, "reason": "API_" + str(r.get("status")),
                    "source": "myamka", "raw": (r.get("text") or "")[:500]}
        try:
            data = json.loads(r.get("text") or "")
        except Exception:
            return {"ok": False, "reason": "BadJson", "source": "myamka",
                    "raw": (r.get("text") or "")[:500]}

        found: set = set()
        if isinstance(data, dict) and data.get("amkaList"):
            _collect_amka(data.get("amkaList"), found)
        if not found:
            _collect_amka(data, found)
        amkas = sorted(found)
        if not amkas:
            return {"ok": False, "reason": "NotFound", "source": "myamka"}
        return {"ok": True, "amka": amkas[0], "amkaList": amkas, "source": "myamka"}
    except Exception as e:
        log.exception("MyAMKA fallback retrieve_amka failed")
        return {"ok": False, "reason": f"Exception: {e}", "source": "myamka"}


if __name__ == "__main__":  # χειροκίνητη δοκιμή: python -m e3.checks.myamka_http <user> <pass>
    import sys
    if len(sys.argv) >= 3:
        print(json.dumps(retrieve_amka(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=2))
    else:
        print("usage: python -m e3.checks.myamka_http <taxis_user> <taxis_pass>")
