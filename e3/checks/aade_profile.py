"""Στοιχεία ταυτότητας/έδρας πελάτη από το Μητρώο ΑΑΔΕ (myAADE / TAXISnet) —
PURE HTTP fallback για την ανάκτηση διεύθυνσης όταν ο κύριος μηχανισμός
(BusinessPortalFetcher, βλ. app.py::api_e3_brain_company_members) αποτυγχάνει.

Πιστή μετάφραση του configs/aade-profile.js (repo scanmydata/ScanMyData-Tax-Center,
αποκωδικοποιημένο) — login μέσω GSIS OAM (login.gsis.gr/oam/server/auth_cred_submit,
ΔΕΝ είναι το ίδιο με το Keycloak-brokered login του e-EFKA στο efka_teka_certificate.py/
keao-mistoton.py), μετά GET στα webresources/infomytaxisnet του saadeapps3/comregistry:
  getuserdata/username            -> ΑΦΜ + ονοματεπώνυμο λογαριασμού
  getMhtrwoFusikou/{afm}          -> μητρώο φυσικού προσώπου
  getMhtrwoEpixeirhshs/{afm}      -> μητρώο επιχείρησης (ΔΟΥ, έναρξη, κατάσταση)

ΣΗΜΕΙΩΣΗ για τη διεύθυνση: το reference JS ΔΕΝ εξάγει ρητά πεδίο διεύθυνσης
(δεν το χρειαζόταν για τη δική του φόρμα) — δεν ξέρουμε τα ακριβή XML tag
names εκ των προτέρων. Αντί να μαντέψουμε συγκεκριμένα ονόματα, το
_parse_all_tags() εξάγει ΚΑΘΕ απλό <tag>value</tag> ζεύγος που υπάρχει στο
XML, ώστε οποιοδήποτε πεδίο διεύθυνσης να είναι διαθέσιμο ό,τι όνομα κι αν
έχει· το _guess_address() κάνει μια best-effort εικασία ψάχνοντας για
ονόματα tag που μοιάζουν με διεύθυνση, αλλά ΠΑΝΤΑ επιστρέφει και το πλήρες
dict + το raw XML ώστε να διορθωθεί χειροκίνητα αν η εικασία είναι λάθος
(ίδιο ύφος με το amount.candidates του efka_teka_certificate.py).

CLI:
    python aade_profile.py --username <taxisnet_user> --password <taxisnet_pass> \\
        [--afm <afm-στόχος, κενό = ο ΑΦΜ του λογαριασμού>] --summary-out <path>
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlencode, urlsplit

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
AADE = "https://www1.aade.gr"

# Candidate substrings for a "this tag looks like an address field" guess —
# checked against LOWERCASED tag names. Deliberately broad; _guess_address()
# is advisory only, the full tag dict is always returned alongside it.
_ADDRESS_TAG_HINTS = (
    "dieuthins", "dieythins", "odos", "arithmos", "poli", "polh", "tk",
    "postal", "address", "perioxi", "dimos",
)


class _HyperHttp:
    """Same minimal cookie jar + redirect-following engine used across the
    other e3/checks pure-HTTP scripts this session (efka_teka_certificate.py,
    keao-mistoton.py) — see those for the fuller rationale."""

    def __init__(self, timeout: float = 30.0):
        self.jar: Dict[str, Dict[str, str]] = {}
        self.timeout = timeout

    @staticmethod
    def _host(url: str) -> str:
        return (urlsplit(url).hostname or "").lower()

    def set_cookie(self, host: str, name: str, value: str) -> None:
        self.jar.setdefault(host, {})[name] = value

    def _store(self, url: str, resp: requests.Response) -> None:
        host = self._host(url)
        self.jar.setdefault(host, {})
        try:
            raws = resp.raw.headers.getlist("Set-Cookie")
        except Exception:
            sc = resp.headers.get("Set-Cookie")
            raws = [sc] if sc else []
        for raw in raws:
            kv = raw.split(";", 1)[0]
            if "=" in kv:
                k, v = kv.split("=", 1)
                self.jar[host][k.strip()] = v.strip()

    def _cookie(self, url: str) -> str:
        host = self._host(url)
        parts: List[str] = []
        for h, kv in self.jar.items():
            if host == h or host.endswith(h) or h.endswith("aade.gr") or h.endswith("gsis.gr"):
                for k, v in kv.items():
                    parts.append(f"{k}={v}")
        return "; ".join(parts)

    def _once(self, method: str, url: str, form: Optional[Dict[str, str]] = None) -> requests.Response:
        headers = {"User-Agent": UA, "Accept-Language": "el-GR,el;q=0.9,en;q=0.8"}
        ck = self._cookie(url)
        if ck:
            headers["Cookie"] = ck
        data = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            data = urlencode(form).encode("utf-8")
        resp = requests.request(method, url, headers=headers, data=data,
                                allow_redirects=False, timeout=self.timeout)
        self._store(url, resp)
        return resp

    def follow(self, method: str, url: str, form: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        res = self._once(method, url, form)
        loc = res.headers.get("Location")
        cur = url
        hops = 0
        while loc and 300 <= res.status_code < 400 and hops < 25:
            cur = urljoin(cur, loc)
            res = self._once("GET", cur)
            loc = res.headers.get("Location")
            hops += 1
        return {"url": cur, "status": res.status_code, "text": res.text}


def _tag(xml: str, name: str) -> str:
    m = re.search(r"<" + re.escape(name) + r">([^<]*)</" + re.escape(name) + r">", xml, re.I)
    return m.group(1).strip() if m else ""


def _parse_all_tags(xml: str) -> Dict[str, str]:
    """Every simple <tag>value</tag> pair in the XML, lowercased-key ->
    value — see module docstring for why (we don't know the exact address
    field names ahead of time, so we don't filter, we expose everything)."""
    out: Dict[str, str] = {}
    for m in re.finditer(r"<([A-Za-z_][\w.-]*)>([^<]*)</\1>", xml):
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if val and key not in out:
            out[key] = val
    return out


def _guess_address(tags: Dict[str, str]) -> str:
    hits = []
    for key in tags:
        if any(hint in key for hint in _ADDRESS_TAG_HINTS):
            hits.append(key)
    if not hits:
        return ""
    # Stable order (street-ish first, then number, city, postal) is a nice
    # to have but not knowable without real data — just join in tag order,
    # deduplicated, and let the caller/human eyeball it via `address_tags`.
    parts = [tags[k] for k in hits if tags[k]]
    return " ".join(dict.fromkeys(parts))  # de-dup while preserving order


def aade_login(username: str, password: str) -> Dict[str, Any]:
    """Login μέσω GSIS OAM (myAADE) — ΔΙΑΦΟΡΕΤΙΚΟ login από το Keycloak-brokered
    e-EFKA (efka_teka_certificate.py). Επιστρέφει {ok, http, page} ή {ok:False, reason}."""
    http = _HyperHttp()
    logging.info("[aade-login] GET protected home -> OAM login")
    home = http.follow("GET", AADE + "/taxisnet/info/protected/home.htm")
    m = re.search(r'name="request_id"[^>]*value="([^"]*)"', home["text"], re.I)
    if not m:
        m = re.search(r'value="([^"]*)"[^>]*name="request_id"', home["text"], re.I)
    req_id = m.group(1) if m else ""
    if not req_id:
        return {"ok": False, "reason": "NoRequestId"}
    logging.info("[aade-login] POST OAM auth_cred_submit")
    auth = http.follow("POST", "https://login.gsis.gr/oam/server/auth_cred_submit", {
        "username": username, "password": password, "request_id": req_id, "btn_login": "",
    })
    if (re.search(r"An incorrect Username or Password|Καθορίστηκε λανθασμένο όνομα χρήστη ή κωδικός"
                  r"|κλειδωμένος ή απενεργοποιημένος|auth_fail_exception", auth["text"], re.I)
            or (re.search(r'name="username"', auth["text"], re.I) and re.search(r'name="password"', auth["text"], re.I))):
        return {"ok": False, "reason": "InvalidCredentials"}
    # Manual cookies mirroring the proven JS reference (== C# WebRequestHelper).
    http.set_cookie("www1.aade.gr", "gr.taxisnet.infrastructure.common.web.ActorRoleCookieResolver.ACTOR_ROLE", "SELF_SERVICE")
    http.set_cookie("www1.aade.gr", "OAMAuthnHintCookie", "1")
    logging.info("[aade-login] finish webtax/incomefp -> login.done")
    http.follow("GET", AADE + "/webtax/incomefp/")
    http.follow("GET", AADE + "/webtax/incomefp/login.done")
    check = http.follow("GET", AADE + "/taxisnet/info/protected/home.htm")
    if re.search(r'name="request_id"', check["text"], re.I) and re.search(r'name="password"', check["text"], re.I):
        return {"ok": False, "reason": "NotLoggedIn"}
    logging.info("[aade-login] OK")
    return {"ok": True, "http": http, "page": check}


def fetch_company_profile(username: str, password: str, afm: Optional[str] = None) -> Dict[str, Any]:
    """Login + fetch the ΑΑΔΕ Μητρώο (φυσικού + επιχείρησης) for ``afm``
    (blank = the account's own ΑΦΜ). Returns a best-effort profile dict —
    ``address`` is a best-effort guess (see _guess_address); ``address_tags``
    exposes every candidate tag found so a wrong guess can be corrected
    without another live run."""
    L = aade_login(username, password)
    if not L.get("ok"):
        return {"ok": False, "reason": L.get("reason")}
    http: _HyperHttp = L["http"]
    reg = urljoin(AADE, "/saadeapps3/comregistry")
    w = reg + "/webresources/infomytaxisnet"

    http.follow("GET", reg + "/")
    userdata = http.follow("GET", w + "/getuserdata/username")["text"]
    login_afm = _tag(userdata, "afm")
    target_afm = (afm or "").strip() or login_afm
    if not target_afm:
        return {"ok": False, "reason": "NoAfm"}

    fysiko = http.follow("GET", w + "/getMhtrwoFusikou/" + target_afm)["text"]
    has_fysiko = f"<afm>{target_afm}</afm>" in fysiko

    epix = http.follow("GET", w + "/getMhtrwoEpixeirhshs/" + target_afm)["text"]
    has_epix = "<hmenarxhs>" in epix

    if has_fysiko:
        kind = "ΑΤΟΜΙΚΗ ΕΠΙΧΕΙΡΗΣΗ" if has_epix else "ΙΔΙΩΤΗΣ"
    elif has_epix:
        kind = "ΝΟΜΙΚΟ ΠΡΟΣΩΠΟ"
    else:
        return {"ok": False, "reason": "NoRegistry", "afm": target_afm}

    same_account = target_afm == login_afm
    name = ""
    if kind == "ΝΟΜΙΚΟ ΠΡΟΣΩΠΟ":
        name = _tag(userdata, "longepwnymia") or _tag(userdata, "onomatepwnymo") if same_account else ""
    elif same_account:
        name = _tag(userdata, "onomatepwnymo")
    else:
        name = _tag(fysiko, "epwnymoa")

    doy = _tag(epix, "doydescription") or _tag(fysiko, "armodiadoy")

    all_tags = {**_parse_all_tags(fysiko), **_parse_all_tags(epix)}
    guessed_address = _guess_address(all_tags)
    address_tags = {k: v for k, v in all_tags.items() if any(h in k for h in _ADDRESS_TAG_HINTS)}

    active = (
        not re.search(r"ΔΙΑΚΟΠ|ΑΝΕΝΕΡΓ", _tag(epix, "katastashepixeirhshs"), re.I)
        if has_epix else
        bool(re.search(r"ΚΑΝΟΝΙΚΗ", _tag(fysiko, "katastashforologoumenoy"), re.I))
    )

    return {
        "ok": True,
        "afm": target_afm,
        "name": name,
        "kind": kind,
        "doy": doy,
        "active": active,
        "business_start": _tag(epix, "hmenarxhs"),
        "address": guessed_address,
        "address_tags": address_tags,
        "all_tags": all_tags,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ΑΑΔΕ Μητρώο (myAADE) — fallback identity/address fetch όταν ο κύριος μηχανισμός αποτυγχάνει."
    )
    parser.add_argument("--username", default=os.getenv("AADE_USER"), help="TAXISnet username.")
    parser.add_argument("--password", default=os.getenv("AADE_PASS"), help="TAXISnet password.")
    parser.add_argument("--afm", default=os.getenv("AADE_TARGET_AFM", ""), help="ΑΦΜ-στόχος (κενό = ο ΑΦΜ του λογαριασμού).")
    parser.add_argument("--summary-out", default=os.getenv("AADE_SUMMARY_OUT"), help="Αν δοθεί, γράφει JSON summary σε αυτό το αρχείο.")
    args = parser.parse_args()

    if not args.username or not args.password:
        print("ERROR: --username και --password είναι υποχρεωτικά.", file=sys.stderr)
        return 2

    result = fetch_company_profile(args.username, args.password, args.afm.strip() or None)

    if args.summary_out:
        try:
            with open(args.summary_out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logging.info("Summary -> %s", args.summary_out)
        except Exception as exc:
            logging.warning("Could not write summary file (%s): %s", args.summary_out, exc)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
