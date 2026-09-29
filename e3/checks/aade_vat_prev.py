"""Φ2 (περιοδική δήλωση ΦΠΑ) της ΠΡΟΗΓΟΥΜΕΝΗΣ περιόδου από την ΑΑΔΕ — PURE HTTP,
ίδιο GSIS OAM login με το aade_profile.py.

Πιστή μεταφορά του runner/lib/aade-vat-http.js (live-verified σε χρεωστικές/
πιστωτικές/μηδενικές + τροποποιητικές), περιορισμένη στην ΜΙΑ περίοδο που
μας ενδιαφέρει (την αμέσως προηγούμενη της εξεταζόμενης):

  1) taxisnet/vat/protected/displayLiabilitiesForYear.htm?declarationType=vatF2&year=Y
     -> περίοδοι (μήνας/τρίμηνο) + κατάσταση υποβολής
  2) doDisplayDeclarationsList(...) -> displayDeclarationsList.htm?...
     -> δηλώσεις της περιόδου· ενεργή = η τελευταία «Οριστική»
        (η τροποποιητική υπερισχύει της αρχικής)
  3) displayDeclarationState.htm?declarationDatabaseId=<id>
     -> αποτέλεσμα (Χρεωστική/Πιστωτική/Μηδενική), Ποσό για Έκπτωση/Επιστροφή,
        εφάπαξ/1η δόση
  4) displayDebtCode.htm?declarationDatabaseId=<id> (μόνο χρεωστικές)
     -> συνολικό ποσό οφειλής, δόση, ΔΟΥ, πληρωτέο έως

Δεν αποθηκεύει τίποτα: τρέχει σε κάθε υπολογισμό, αφού μπορεί να έχει
υποβληθεί νεότερη τροποποιητική. Μόνο ανάγνωση — ποτέ υποβολή.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, urljoin

from e3.checks.aade_profile import AADE, aade_login

log = logging.getLogger(__name__)

VATBASE = AADE + "/taxisnet/vat/protected/"


def _decode(s: str) -> str:
    return html.unescape(s or "").replace("\xa0", " ")


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", _decode(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _to_num(s: str) -> Optional[float]:
    m = re.search(r"\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?", s or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _rows(page: str) -> List[Dict[str, Any]]:
    out = []
    for r in re.findall(r"<tr\b[^>]*class=\"tblRow[12]\"[^>]*>([\s\S]*?)</tr>", page, re.I):
        out.append({"html": r, "cells": re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", r, re.I)})
    return out


def _label_map(page: str) -> Dict[str, str]:
    """label -> value from 2-cell table rows (first occurrence wins)."""
    m: Dict[str, str] = {}
    for tr in re.findall(r"<tr\b[^>]*>([\s\S]*?)</tr>", page, re.I):
        cells = [c for c in (_strip(x) for x in re.findall(r"<t[dh]\b[^>]*>([\s\S]*?)</t[dh]>", tr, re.I)) if c]
        if len(cells) == 2:
            k = re.sub(r"[:：]\s*$", "", cells[0]).strip()
            if k and k not in m:
                m[k] = cells[1]
    return m


def _find(m: Dict[str, str], pattern: str) -> str:
    rx = re.compile(pattern)
    for k, v in m.items():
        if rx.search(k):
            return v
    return ""


def _parse_dmy(s: str) -> Optional[date]:
    mm = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    if not mm:
        return None
    try:
        return date(int(mm.group(3)), int(mm.group(2)), int(mm.group(1)))
    except ValueError:
        return None


def _decl_list_args(cell_html: str) -> Optional[Dict[str, str]]:
    onclick = _decode(cell_html or "")
    i = onclick.find("doDisplayDeclarationsList(document.displayDeclarationsListForm,")
    if i < 0:
        return None
    j = onclick.find(")", i)
    inner = onclick[i + len("doDisplayDeclarationsList(document.displayDeclarationsListForm,"):j]
    a = [x.replace('"', "").strip() for x in inner.split(",")]
    if len(a) < 7:
        return None
    keys = ["declarationType", "year", "periodType", "periodStart", "periodEnd", "effectivePeriodStart", "effectivePeriodEnd"]
    return dict(zip(keys, a[:7]))


def _period_label(args: Dict[str, str]) -> str:
    d = _parse_dmy(args.get("effectivePeriodStart") or args.get("periodStart") or "")
    if not d:
        return args.get("periodStart") or ""
    if args.get("periodType") == "oneMonth":
        return f"{d.month}ος Μήνας {d.year}"
    if args.get("periodType") == "threeMonths":
        return f"{(d.month - 1) // 3 + 1}ο Τρίμηνο {d.year}"
    return args.get("periodStart") or ""


def _follow(http, method: str, url: str, form: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """_HyperHttp.follow + JSF <partial-response><redirect> handling (as the JS)."""
    r = http.follow(method, url, form)
    guard = 0
    while "<partial-response><redirect url=" in (r.get("text") or "") and guard < 12:
        mm = re.search(r'redirect url="([^"]*)"', r["text"])
        if not mm:
            break
        r = http.follow("GET", urljoin(r["url"], mm.group(1).replace("&amp;", "&")))
        guard += 1
    return r


def _accountant_self(http, page: str) -> bool:
    if not re.search(r"searchTaxpayer|Επιλογή Λογιστικού Γραφείου", page or ""):
        return False
    t = _follow(http, "POST", VATBASE + "displayDeclarationTypes.htm",
                {"actorRole": "SELF_SERVICE", "_eventId_chooseIndividual": "για τον εαυτό μου"})
    return "Καλωσήρθατε στην Υπηρεσία ηλεκτρονικής υποβολής δηλώσεων" in (t.get("text") or "")


def _liabilities(http, year: int) -> Optional[str]:
    url = VATBASE + f"displayLiabilitiesForYear.htm?declarationType=vatF2&year={year}"
    page = _follow(http, "GET", url)["text"]
    if _accountant_self(http, page):
        page = _follow(http, "GET", url)["text"]
    if "Δεν έχετε υποχρεώσεις υποβολής για το συγκεκριμένο έντυπο" in page:
        return None
    return page


def _declaration_amounts(http, db_id: str, ret_view: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"registration_no": db_id, "result": None}
    rv = quote(ret_view, safe="")
    st = _follow(http, "GET", VATBASE + f"displayDeclarationState.htm?declarationType=vatF2&declarationDatabaseId={db_id}&returnView={rv}")
    m = _label_map(st.get("text") or "")
    res = _find(m, r"Αποτέλεσμα")
    out["result"] = ("Χρεωστική" if "Χρεωστικ" in res else "Πιστωτική" if "Πιστωτικ" in res
                     else "Μηδενική" if "Μηδενικ" in res else (res or None))
    out["current_state"] = _find(m, r"Τρέχουσα Κατάσταση") or None
    pay = _find(m, r"Δυνατότητες Πληρωμής")
    lump = re.search(r"Εφάπαξ ποσό:\s*([\d.,]+)", pay)
    first = re.search(r"Ποσό 1ης Δόσης:\s*([\d.,]+)", pay)
    out["lump_sum"] = _to_num(lump.group(1)) if lump else None
    out["first_installment"] = _to_num(first.group(1)) if first else None
    out["amount_for_deduction"] = _to_num(_find(m, r"Ποσό για Έκπτωση"))
    out["amount_for_refund"] = _to_num(_find(m, r"Ποσό για Επιστροφή"))
    if out["result"] == "Χρεωστική":
        dc = _follow(http, "GET", VATBASE + f"displayDebtCode.htm?declarationType=vatF2&declarationDatabaseId={db_id}&returnView={rv}")
        if re.search(r"Ταυτότητα Οφειλής|Συνολικό ποσό οφειλής", dc.get("text") or ""):
            dm = _label_map(dc["text"])
            out["total_due"] = _to_num(_find(dm, r"Συνολικό ποσό οφειλής"))
            dose_key = next((k for k in dm if re.search(r"Ποσό δόσης δήλωσης", k)), None)
            if dose_key:
                out["installment"] = _to_num(dm[dose_key])
                pb = re.search(r"(\d{2}/\d{2}/\d{4})", dose_key)
                out["pay_by"] = pb.group(1) if pb else None
    return out


def declared_balance(p: Dict[str, Any]) -> Optional[float]:
    """Signed outcome of an effective declaration: + amount payable (Χρεωστική),
    − credit (Πιστωτική: για έκπτωση + για επιστροφή), 0 (Μηδενική)."""
    if not p or not p.get("result"):
        return None
    if p["result"] == "Μηδενική":
        return 0.0
    if p["result"] == "Χρεωστική":
        due = p.get("total_due")
        if due is None:
            due = p.get("lump_sum") if p.get("lump_sum") else p.get("first_installment")
        return round(float(due), 2) if due is not None else None
    if p["result"] == "Πιστωτική":
        credit = float(p.get("amount_for_deduction") or 0.0) + float(p.get("amount_for_refund") or 0.0)
        return round(-credit, 2)
    return None


def fetch_vat_periods(username: str, password: str, current_period_from: date) -> Dict[str, Any]:
    """ONE TAXISnet login, two Φ2 periods (month or quarter — whatever the
    taxpayer's liabilities list shows):
      previous — the period right before `current_period_from` (its credit is
                 carried into the current period / its debit is reported);
      current  — the period starting at `current_period_from`, if a
                 declaration was already filed for it (cross-checked against
                 myDATA by the caller — a mismatch means an amending
                 declaration may be needed).
    -> {ok, previous: {...}|None, current: {...}|None} or {ok: False, error}."""
    L = aade_login(username, password)
    if not L.get("ok"):
        return {"ok": False, "error": f"Αποτυχία σύνδεσης TAXISnet ({L.get('reason')})"}
    http = L["http"]
    http.deadline = time.monotonic() + 75

    candidates: List[Dict[str, Any]] = []
    for y in (current_period_from.year, current_period_from.year - 1):
        page = _liabilities(http, y)
        if page is None:
            continue
        for row in _rows(page):
            texts = [_strip(c) for c in row["cells"]]
            args = _decl_list_args(row["cells"][3] if len(row["cells"]) > 3 else "")
            if not args:
                continue
            p_from = _parse_dmy(args.get("effectivePeriodStart") or args.get("periodStart"))
            p_to = _parse_dmy(args.get("effectivePeriodEnd") or args.get("periodEnd"))
            if not p_from or not p_to:
                continue
            status_text = next((t for t in texts if re.search(r"Υποβληθεί|εκκρεμότητα|Δεν έχ", t)), texts[2] if len(texts) > 2 else "")
            candidates.append({"args": args, "from": p_from, "to": p_to, "status_text": status_text})
        if any(current_period_from - timedelta(days=1) <= c["to"] < current_period_from for c in candidates):
            break
    if not candidates:
        return {"ok": False, "error": "Δεν βρέθηκαν περίοδοι Φ2 στο TAXISnet (ή η εταιρία δεν έχει υποχρέωση Φ2)"}
    before = [c for c in candidates if c["to"] < current_period_from]
    same = [c for c in candidates if c["from"] == current_period_from]
    previous = _period_outcome(http, max(before, key=lambda c: c["to"])) if before else None
    current = _period_outcome(http, same[0]) if same else None
    return {"ok": True, "previous": previous, "current": current}


def fetch_previous_vat_period(username: str, password: str, current_period_from: date) -> Dict[str, Any]:
    """Backwards-compatible: just the previous period (see fetch_vat_periods)."""
    r = fetch_vat_periods(username, password, current_period_from)
    if not r.get("ok"):
        return r
    if not r.get("previous"):
        return {"ok": False, "error": "Δεν βρέθηκε προηγούμενη περίοδος Φ2"}
    return r["previous"]


def _period_outcome(http, cand: Dict[str, Any]) -> Dict[str, Any]:
    """One period's status + its EFFECTIVE declaration's outcome."""
    args = cand["args"]
    out: Dict[str, Any] = {
        "ok": True,
        "period": _period_label(args),
        "period_from": cand["from"].strftime("%d/%m/%Y"),
        "period_to": cand["to"].strftime("%d/%m/%Y"),
        "status_text": cand["status_text"],
        "result": None,
    }
    if re.search(r"Δεν έχει υποβληθεί|Δεν έχουν υποβληθεί", cand["status_text"] or ""):
        out["status"] = "NOT_SUBMITTED"
        return out
    out["status"] = "PENDING" if "εκκρεμότητα" in (cand["status_text"] or "") else "SUBMITTED"

    query = "?" + urlencode(args)
    ret_view = "displayDeclarationsList.htm" + query
    lst = _follow(http, "GET", VATBASE + "displayDeclarationsList.htm" + query)["text"]
    decls = []
    for r in _rows(lst):
        c = [_strip(x) for x in r["cells"]]
        mm = re.search(r'doDisplayDeclarationState\(document\.displayDeclarationStateForm,"vatF2","([^"]+)"', _decode(r["html"]))
        db_id = (mm.group(1) if mm else (c[2] if len(c) > 2 else "")).strip()
        decls.append({"db_id": db_id, "submitted_at": c[3] if len(c) > 3 else None,
                      "type": c[4] if len(c) > 4 else None, "finalized": c[5] if len(c) > 5 else ""})
    finals = [d for d in decls if "Οριστικ" in (d.get("finalized") or "")]
    eff = finals[-1] if finals else None
    if not eff or not eff["db_id"]:
        out["status"] = "PENDING" if decls else out["status"]
        return out
    out.update({"submitted_at": eff["submitted_at"], "type": eff["type"], "declarations": len(decls)})
    out.update(_declaration_amounts(http, eff["db_id"], ret_view))
    return out
