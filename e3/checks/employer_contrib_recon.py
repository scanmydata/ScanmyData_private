"""Απλήρωτες εισφορές εργοδότη (ΙΚΑ / ΤΕΚΑ): Οικονομική Καρτέλα Εργοδότη ↔ ΚΕΑΟ εργοδότη.

Λογική (κανόνες του χρήστη):
* Χρεώσεις της καρτέλας = ΑΠΔ ανά περίοδο. Οι εισφορές του έτους Y = ΑΠΔ περιόδων 1/Y … 12/Y
  (μαζί με το δώρο Χριστουγέννων, που δηλώνεται στην ΑΠΔ του Ιανουαρίου Y+1).
* Πιστώσεις: πληρωμές (π.χ. «ΓΕΤΕ ΤΡΑΠΕΖΑΣ ΤΠΤΕ») και «ΠΕΕ ΛΟΓΩ ΔΙΑΦΟΡΑΣ ΔΗΛΩΘΕΝΤΩΝ». Η δεύτερη ΔΕΝ είναι
  πληρωμή: η απλήρωτη διαφορά μεταφέρεται στο ΚΕΑΟ (ΙΚΑ ή ΤΕΚΑ εργοδότη, ανάλογα με την καρτέλα).
* Μια εισφορά του Y λογίζεται εξοφλημένη αν πληρώθηκε — στην καρτέλα ή στο ΚΕΑΟ — έως το τέλος
  Φεβρουαρίου του Y+1 (χρήση + 2 μήνες, λόγω του δώρου Χριστουγέννων). Ό,τι μένει = απλήρωτο.

Αντιστοίχιση: καθολικό ανά περίοδο (κάθε πληρωμή εξοφλεί την παλαιότερη ανοιχτή περίοδο· προπληρωμή
μένει ως πιστωτικό και καλύπτει την επόμενη ΑΠΔ)· οι ΠΕΕ κλείνουν ανοιχτές περιόδους μέσα στο εύρος
τους· οι πληρωμές του ΚΕΑΟ (κύρια εισφορά) εξοφλούν τις μεταφερμένες οφειλές με σειρά μεταφοράς (FIFO).
"""
from __future__ import annotations

import calendar
import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TOL = 0.01
_AMT = r"-?\d{1,3}(?:\.\d{3})*,\d{2}"
_ROW_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(\d{1,2}/\d{4}(?:\s*-\s*\d{1,2}/\d{4})?)\s+(" + _AMT + r")\s+(" + _AMT + r")\s+(" + _AMT + r")\s*$")
_DOC_RE = re.compile(r"\s(\d{3}-\d{4}-\S+)$")


def _amt(s: Any) -> float:
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("€", "").replace(" ", "")
    if not t:
        return 0.0
    try:
        return float(t.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _date(s: str) -> Optional[_dt.date]:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    if not m:
        return None
    try:
        return _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _periods(s: str) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    ps = [(int(y), int(m)) for m, y in re.findall(r"(\d{1,2})/(\d{4})", s or "")]
    if not ps:
        return None, None
    return ps[0], ps[-1]


def _r2(x: float) -> float:
    return round(x + 0.0, 2)


def deadline_for(year: int) -> _dt.date:
    return _dt.date(year + 1, 2, calendar.monthrange(year + 1, 2)[1])


# ------------------------------------------------------------------ είσοδος
def is_transfer(kind: str) -> bool:
    k = (kind or "").upper()
    return "ΔΙΑΦΟΡΑΣ ΔΗΛΩΘΕΝΤΩΝ" in k or k.startswith("ΠΕΕ") or k.startswith("ΕΕ ")


def normalize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Γραμμές καρτέλας (HTTP: date/kind/docNo/period/charge/credit) → ενιαία μορφή."""
    out = []
    for i, r in enumerate(rows or []):
        d = _date(r.get("date") or "")
        if not d:
            continue
        p0, p1 = _periods(r.get("period") or "")
        out.append({"i": i, "date": d, "kind": str(r.get("kind") or "").strip(), "doc": str(r.get("docNo") or r.get("doc") or ""),
                    "period_from": p0, "period_to": p1, "debit": _amt(r.get("charge")), "credit": _amt(r.get("credit"))})
    return out


def parse_card_text(text: str) -> List[Dict[str, Any]]:
    """Γραμμές από το κείμενο του PDF της καρτέλας (ΕΦΚΑ ή ΤΕΚΑ):
    «Ημ/νία Τύπος Κίνησης Παραστατικό Περίοδος Χρέωση Πίστωση Υπόλοιπο»."""
    rows = []
    for line in (text or "").splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        head = m.group(2)
        dm = _DOC_RE.search(head)
        doc = dm.group(1) if dm else ""
        kind = head[:dm.start()].strip() if dm else head
        rows.append({"date": m.group(1), "kind": kind, "docNo": doc, "period": m.group(3),
                     "charge": m.group(4), "credit": m.group(5), "balance": m.group(6)})
    return rows


def parse_card_pdf(path: Path) -> List[Dict[str, Any]]:
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    return parse_card_text(text)


# ------------------------------------------------------------------ καθολικό
def ledger(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Καθολικό ανά περίοδο: ΑΠΔ (χρεώσεις) ↔ πληρωμές / μεταφορές στο ΚΕΑΟ (ΠΕΕ), χρονολογικά."""
    debts: List[Dict[str, Any]] = []      # ανοιχτές οφειλές ανά ΑΠΔ
    pool: List[Dict[str, Any]] = []       # προπληρωμές (πιστωτικό) που δεν έχουν κατανεμηθεί
    transfers: List[Dict[str, Any]] = []  # μεταφορές στο ΚΕΑΟ ανά περίοδο
    unalloc_transfers = 0.0
    events = sorted(rows, key=lambda r: (r["date"], 0 if r["debit"] > 0 else 1, r["i"]))

    def _pay(debt, amount, src):
        take = min(amount, debt["open"])
        if take > TOL / 10:
            debt["open"] = _r2(debt["open"] - take)
            debt["paid"].append({"date": src["date"], "amount": _r2(take), "doc": src["doc"], "kind": src["kind"]})
        return _r2(amount - take)

    for r in events:
        if r["debit"] > 0:
            per = r["period_to"] or r["period_from"]
            d = {"period": per, "date": r["date"], "doc": r["doc"], "kind": r["kind"], "amount": r["debit"],
                 "open": r["debit"], "paid": [], "transferred": []}
            debts.append(d)
            # η προπληρωμή καλύπτει την καινούρια ΑΠΔ
            while pool and d["open"] > TOL / 10:
                left = _pay(d, pool[0]["left"], pool[0]["src"])
                if left <= TOL / 10:
                    pool.pop(0)
                else:
                    pool[0]["left"] = left
            continue
        if r["credit"] <= 0:
            continue
        amount = r["credit"]
        if is_transfer(r["kind"]):
            lo, hi = r["period_from"], r["period_to"]
            for d in sorted(debts, key=lambda x: (x["period"] or (0, 0), x["date"])):
                if amount <= TOL / 10:
                    break
                if d["open"] <= TOL / 10:
                    continue
                if lo and hi and d["period"] and not (lo <= d["period"] <= hi):
                    continue
                take = min(amount, d["open"])
                d["open"] = _r2(d["open"] - take)
                t = {"date": r["date"], "amount": _r2(take), "doc": r["doc"], "period": d["period"],
                     "keao_paid": [], "keao_open": _r2(take)}
                d["transferred"].append(t)
                transfers.append(t)
                amount = _r2(amount - take)
            if amount > TOL:
                unalloc_transfers += amount
            continue
        for d in sorted(debts, key=lambda x: (x["period"] or (0, 0), x["date"])):
            if amount <= TOL / 10:
                break
            if d["open"] > TOL / 10:
                amount = _pay(d, amount, r)
        if amount > TOL / 10:
            pool.append({"left": amount, "src": r})
    return {"debts": debts, "transfers": transfers, "prepaid": _r2(sum(p["left"] for p in pool)),
            "unallocated_transfers": _r2(unalloc_transfers)}


def _norm_doc(x: Any) -> str:
    return re.sub(r"\s+", "", str(x or "")).upper()


def apply_keao(transfers: List[Dict[str, Any]], keao_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Πληρωμές ΚΕΑΟ (κύρια εισφορά) → μεταφερμένες οφειλές. Με «Ανάλυση» της πίστωσης: πρώτα οι μεταφορές με το
    ίδιο παραστατικό ΠΕΕ, μετά της ίδιας περιόδου· ό,τι μένει (ή χωρίς ανάλυση) με σειρά μεταφοράς (FIFO)."""
    pays = sorted(((_date(r.get("date") or ""), r) for r in keao_rows or []), key=lambda x: (x[0] or _dt.date.min))
    used = []
    leftover = 0.0
    queue = sorted(transfers, key=lambda t: (t["date"], t["period"] or (0, 0)))
    for d, r in pays:
        if not d:
            continue
        main = float(r.get("main_contrib") or 0.0)
        fees = float(r.get("extra_fees") or 0.0) + float(r.get("surcharges") or 0.0)
        an = r.get("analysis") or {}
        docs = {_norm_doc(x) for x in an.get("docs") or []}
        pers = set()
        for p in an.get("periods") or []:
            pm = re.match(r"(\d{1,2})/(\d{4})", str(p))
            if pm:
                pers.add((int(pm.group(2)), int(pm.group(1))))
        by_doc = [t for t in queue if docs and _norm_doc(t.get("doc")) in docs]
        by_per = [t for t in queue if pers and t["period"] in pers and t not in by_doc]
        order = [("analysis_doc", t) for t in by_doc] + [("analysis_period", t) for t in by_per] +                 [("fifo", t) for t in queue if t not in by_doc and t not in by_per]
        rest = main
        allocs = []
        for how, t in order:
            if rest <= TOL / 10:
                break
            if t["keao_open"] <= TOL / 10 or t["date"] > d:
                continue
            take = min(rest, t["keao_open"])
            t["keao_open"] = _r2(t["keao_open"] - take)
            t["keao_paid"].append({"date": d, "amount": _r2(take), "doc": r.get("doc"), "matched_by": how,
                                   "fees_share": _r2(fees * take / main) if main else 0.0})
            allocs.append({"period": t["period"], "amount": _r2(take), "transfer_doc": t.get("doc"), "matched_by": how})
            rest = _r2(rest - take)
        leftover += rest
        used.append({"date": d, "doc": r.get("doc"), "main": main, "fees": _r2(fees), "unassigned": _r2(rest),
                     "allocations": allocs, "has_analysis": bool(an)})
    return {"payments": used, "unassigned": _r2(leftover)}


def _pay_view(p: Dict[str, Any], year: int) -> Dict[str, Any]:
    """Πληρωμή ΚΕΑΟ για εμφάνιση: πόσα αφορούν περιόδους της χρήσης `year` και πώς αντιστοιχίστηκαν."""
    allocs = [dict(a, period=f"{a['period'][1]}/{a['period'][0]}" if a.get("period") else "") for a in p.get("allocations") or []]
    for_year = _r2(sum(a["amount"] for a in p.get("allocations") or [] if a.get("period") and a["period"][0] == year))
    return dict(p, date=p["date"].strftime("%d/%m/%Y"), allocations=allocs, for_year=for_year)


def reconcile_fund(year: int, card_rows: List[Dict[str, Any]], keao_rows: Optional[List[Dict[str, Any]]],
                   fund: str) -> Dict[str, Any]:
    deadline = deadline_for(year)
    rows = normalize_rows(card_rows)
    L = ledger(rows)
    keao = apply_keao(L["transfers"], keao_rows) if keao_rows is not None else None
    per: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for d in L["debts"]:
        if not d["period"] or d["period"][0] != year:
            continue
        p = per.setdefault(d["period"], {"period": f"{d['period'][1]}/{d['period'][0]}", "apd": 0.0, "paid_card": 0.0,
                                         "paid_card_late": 0.0, "to_keao": 0.0, "paid_keao": 0.0, "paid_keao_late": 0.0,
                                         "keao_fees": 0.0, "open_card": 0.0, "docs": []})
        p["apd"] += d["amount"]
        p["docs"].append(d["doc"])
        p["open_card"] += d["open"]
        for x in d["paid"]:
            if x["date"] <= deadline:
                p["paid_card"] += x["amount"]
            else:
                p["paid_card_late"] += x["amount"]
        for t in d["transferred"]:
            p["to_keao"] += t["amount"]
            for k in t["keao_paid"]:
                if k["date"] <= deadline:
                    p["paid_keao"] += k["amount"]
                    p["keao_fees"] += k["fees_share"]
                else:
                    p["paid_keao_late"] += k["amount"]
    periods = []
    for key in sorted(per):
        p = per[key]
        for k in ("apd", "paid_card", "paid_card_late", "to_keao", "paid_keao", "paid_keao_late", "keao_fees", "open_card"):
            p[k] = _r2(p[k])
        p["paid_by_deadline"] = _r2(p["paid_card"] + p["paid_keao"])
        p["unpaid"] = _r2(max(0.0, p["apd"] - p["paid_by_deadline"]))
        periods.append(p)
    tot = {k: _r2(sum(p[k] for p in periods)) for k in ("apd", "paid_card", "paid_card_late", "to_keao", "paid_keao",
                                                         "paid_keao_late", "keao_fees", "paid_by_deadline", "unpaid", "open_card")}
    last_row = max((r["date"] for r in rows), default=None)
    notes = []
    if last_row and last_row < _dt.date(year + 1, 1, 31):
        notes.append(f"Η καρτέλα φτάνει έως {last_row:%d/%m/%Y} — οι κινήσεις έως {deadline:%d/%m/%Y} (ΑΠΔ Δεκεμβρίου/δώρου "
                     "Χριστουγέννων και πληρωμές τους) ίσως λείπουν.")
    if keao is None and tot["to_keao"] > TOL:
        notes.append("Δεν βρέθηκαν πιστώσεις ΚΕΑΟ εργοδότη: τα ποσά που μεταφέρθηκαν στο ΚΕΑΟ λογίζονται απλήρωτα.")
    if keao and keao["unassigned"] > TOL:
        notes.append(f"Πληρωμές ΚΕΑΟ {keao['unassigned']:.2f} € δεν αντιστοιχίστηκαν σε μεταφορά της καρτέλας "
                     "(πιθανώς παλαιότερες οφειλές).")
    if L["unallocated_transfers"] > TOL:
        notes.append(f"Μεταφορές στο ΚΕΑΟ {L['unallocated_transfers']:.2f} € αφορούν περιόδους εκτός της καρτέλας.")
    status = "no_data" if not periods else ("unpaid" if tot["unpaid"] > TOL else "paid")
    return {"fund": fund, "year": year, "deadline": deadline.strftime("%d/%m/%Y"), "status": status,
            "totals": tot, "periods": periods, "keao_available": keao is not None,
            "keao_payments": [_pay_view(p, year) for p in (keao or {}).get("payments", [])],
            "keao_analysis_used": sum(1 for p in (keao or {}).get("payments", []) if p.get("has_analysis")),
            "prepaid": L["prepaid"], "notes": notes, "card_rows": len(rows),
            "movements": [{"date": r["date"].strftime("%d/%m/%Y"), "kind": r["kind"], "doc": r["doc"],
                           "period": (f"{r['period_from'][1]}/{r['period_from'][0]}" if r["period_from"] else "")
                           + (f" - {r['period_to'][1]}/{r['period_to'][0]}" if r["period_to"] and r["period_to"] != r["period_from"] else ""),
                           "debit": r["debit"], "credit": r["credit"],
                           "type": "apd" if r["debit"] > 0 else ("transfer" if is_transfer(r["kind"]) else "payment")}
                          for r in sorted(rows, key=lambda x: (x["date"], x["i"]))]}


def reconcile(year: int, cards: Dict[str, List[Dict[str, Any]]], keao: Dict[str, Optional[List[Dict[str, Any]]]],
              registries: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    funds = {}
    for fund in ("IKA", "TEKA"):
        if cards.get(fund):
            funds[fund] = reconcile_fund(year, cards[fund], keao.get(fund), fund)
            # ποιο μητρώο ΚΕΑΟ (ΑΜΟ + ονομασία ταμείου) — εμφανίζεται στην καρτέλα PDF
            funds[fund]["keao_registries"] = [{"amo": r.get("amo"), "forea": r.get("forea")}
                                              for r in (registries or {}).get(fund) or []]
    unpaid = _r2(sum(f["totals"]["unpaid"] for f in funds.values()))
    paid = _r2(sum(f["totals"]["paid_by_deadline"] for f in funds.values()))
    apd = _r2(sum(f["totals"]["apd"] for f in funds.values()))
    return {"year": year, "funds": funds, "apd_total": apd, "paid_total": paid, "unpaid_total": unpaid,
            "status": "no_data" if not funds else ("unpaid" if unpaid > TOL else "paid")}


# ------------------------------------------------------------------ καρτέλα PDF
def _fmt(x: Any) -> str:
    try:
        return f"{float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


_STATUS = {"paid": ("ΕΞΟΦΛΗΜΕΝΕΣ", "match"), "unpaid": ("ΑΠΛΗΡΩΤΕΣ", "difference"), "no_data": ("ΧΩΡΙΣ ΣΤΟΙΧΕΙΑ", "no_data")}
_FUND_LABEL = {"IKA": "e-ΕΦΚΑ (ΙΚΑ) εργοδότη", "TEKA": "ΤΕΚΑ εργοδότη"}


def build_report_html(res: Dict[str, Any], afm: str, name: str, ame: str = "") -> str:
    """Καρτέλα PDF στο ΙΔΙΟ format με την «Αντιπαραβολή Εισφορών Μη Μισθωτών» (ίδιο CSS/δομή)."""
    try:
        from e3.checks.efka_teka_reconciliation import _CSS
    except Exception:  # pragma: no cover
        from efka_teka_reconciliation import _CSS  # type: ignore
    from html import escape

    def e(x: Any) -> str:
        return escape("" if x is None else str(x))

    def eur(x: Any) -> str:
        return _fmt(x) + " €"

    y = res["year"]
    dl = deadline_for(y).strftime("%d/%m/%Y")
    now = _dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    parts = [f"<!doctype html><html lang='el'><head><meta charset='utf-8'><title>Αντιπαραβολή εισφορών εργοδότη {y}</title>"
             f"<style>{_CSS}</style></head><body>",
             "<h1>Αντιπαραβολή Εισφορών Εργοδότη (Καρτέλα Εργοδότη ↔ ΚΕΑΟ)</h1>"
             f"<div class='sub'>Φορολογικό έτος {y} · Εισφορές ΑΠΔ έναντι πληρωμών στην Οικονομική Καρτέλα Εργοδότη και στο ΚΕΑΟ "
             f"εργοδότη · προθεσμία εξόφλησης {dl}</div>",
             "<div class='meta'>"
             f"<div><b>Εργοδότης</b>{e(name or '—')}</div><div><b>ΑΦΜ</b>{e(afm)}</div>"
             f"<div><b>ΑΜΕ</b>{e(ame or '—')}</div><div><b>Ημ/νία καρτέλας</b>{now}</div></div>"]
    for fund, f in (res.get("funds") or {}).items():
        t = f["totals"]
        lab, cls = _STATUS.get(f["status"], (f["status"], "no_data"))
        fname = _FUND_LABEL.get(fund, fund)
        parts.append(f"<div class='sec'><h2>{fname} — <span class='status {cls}'>{lab}</span></h2>")
        parts.append("<div class='cards'>"
                     f"<div class='card'><div class='l'>Εισφορές ΑΠΔ περιόδων {y}</div><div class='v'>{eur(t['apd'])}</div></div>"
                     f"<div class='card {'ok' if t['unpaid'] <= TOL else ''}'><div class='l'>Εξοφλημένες έως {dl}</div><div class='v'>{eur(t['paid_by_deadline'])}</div></div>"
                     f"<div class='card {'bad' if t['unpaid'] > TOL else 'ok'}'><div class='l'>Απλήρωτες στην προθεσμία</div><div class='v'>{eur(t['unpaid'])}</div></div>"
                     f"<div class='card {'warn' if t['keao_fees'] > TOL else ''}'><div class='l'>Επιβαρύνσεις ΚΕΑΟ (τέλη + προσαυξ.)</div><div class='v'>{eur(t['keao_fees'])}</div></div>"
                     "</div>")
        expl = [
            f"Οι εισφορές του {y} είναι οι ΑΠΔ των περιόδων 1/{y}–12/{y} (μαζί με το δώρο Χριστουγέννων που δηλώνεται στην ΑΠΔ "
            f"Ιανουαρίου {y + 1}): {eur(t['apd'])}.",
            f"Πληρώθηκαν απευθείας στην καρτέλα έως {dl}: {eur(t['paid_card'])}.",
            f"Μεταφέρθηκαν στο ΚΕΑΟ με «ΠΕΕ ΛΟΓΩ ΔΙΑΦΟΡΑΣ ΔΗΛΩΘΕΝΤΩΝ» (απλήρωτη διαφορά — δεν είναι πληρωμή): {eur(t['to_keao'])}· "
            f"από αυτά εξοφλήθηκαν στο ΚΕΑΟ έως {dl}: {eur(t['paid_keao'])}.",
        ]
        late = t["paid_card_late"] + t["paid_keao_late"]
        if late > TOL:
            expl.append(f"Πληρώθηκαν μετά την προθεσμία: {eur(late)} — δεν λογίζονται στη χρήση {y}.")
        expl.append(f"Εξοφλημένες έως την προθεσμία {eur(t['paid_by_deadline'])} · απλήρωτες {eur(t['unpaid'])}"
                    + (" — δεν εκπίπτουν στη χρήση." if t["unpaid"] > TOL else "."))
        parts.append("<div class='expl'>" + "".join(f"<p>{e(x)}</p>" for x in expl) + "</div>")
        # ανάλυση ανά περίοδο
        parts.append(f"<h2>{fname} — ανάλυση ανά περίοδο ΑΠΔ {y}</h2>"
                     "<table><tr><th>Περίοδος</th><th class='n'>ΑΠΔ</th><th class='n'>Πληρωμή καρτέλας</th>"
                     "<th class='n'>Μεταφορά ΚΕΑΟ</th><th class='n'>Πληρωμή ΚΕΑΟ</th><th class='n'>Εξοφλ. έως προθεσμία</th>"
                     "<th class='n'>Μετά την προθεσμία</th><th class='n'>Απλήρωτο</th></tr>")
        for p in f["periods"]:
            lt = p["paid_card_late"] + p["paid_keao_late"]
            cls_r = "hl" if p["unpaid"] <= TOL else ""
            parts.append(f"<tr class='{cls_r}'><td>{e(p['period'])}</td><td class='n'>{_fmt(p['apd'])}</td>"
                         f"<td class='n'>{_fmt(p['paid_card']) if p['paid_card'] else ''}</td>"
                         f"<td class='n'>{_fmt(p['to_keao']) if p['to_keao'] else ''}</td>"
                         f"<td class='n'>{_fmt(p['paid_keao']) if p['paid_keao'] else ''}</td>"
                         f"<td class='n'>{_fmt(p['paid_by_deadline'])}</td><td class='n'>{_fmt(lt) if lt > TOL else ''}</td>"
                         f"<td class='n'>{('<b>' + _fmt(p['unpaid']) + '</b>') if p['unpaid'] > TOL else ''}</td></tr>")
        parts.append(f"<tr class='tot'><td>Σύνολα</td><td class='n'>{_fmt(t['apd'])}</td><td class='n'>{_fmt(t['paid_card'])}</td>"
                     f"<td class='n'>{_fmt(t['to_keao'])}</td><td class='n'>{_fmt(t['paid_keao'])}</td>"
                     f"<td class='n'>{_fmt(t['paid_by_deadline'])}</td><td class='n'>{_fmt(late)}</td>"
                     f"<td class='n'>{_fmt(t['unpaid'])}</td></tr>")
        parts.append(f"<tr class='diff'><td><b>Απλήρωτες εισφορές {y} στις {dl}</b></td><td></td><td></td><td></td><td></td><td></td><td></td>"
                     f"<td class='n'><b>{_fmt(t['unpaid'])}</b></td></tr></table>")
        # πληρωμές ΚΕΑΟ
        rel = [kp for kp in f.get("keao_payments") or [] if kp.get("for_year", 0) > TOL]
        if rel:
            how_lbl = {"analysis_doc": "ανάλυση ΚΕΑΟ (παραστατικό ΠΕΕ)", "analysis_period": "ανάλυση ΚΕΑΟ (περίοδος)",
                       "fifo": "σειρά μεταφοράς"}
            regs = "; ".join(f"ΑΜΟ {e(r.get('amo'))} — {e(re.sub(r'^Ληξιπρόθεσμο - ', '', r.get('forea') or ''))}"
                             for r in f.get("keao_registries") or [])
            parts.append(f"<h2>Καταβολές στο ΚΕΑΟ που αφορούν τη χρήση {y} — {fname}</h2>"
                         + (f"<p class='small muted'>{regs}</p>" if regs else "")
                         + "<table><tr><th>Ημ/νία</th><th>Παραστατικό</th><th class='n'>Κύρια εισφορά</th>"
                         "<th class='n'>Πρόσθ. τέλη / προσαυξ.</th><th>Περίοδοι " + str(y) + " που εξοφλήθηκαν</th><th>Αντιστοίχιση</th>"
                         "<th>Έως την προθεσμία</th></tr>")
            for kp in rel:
                d = _date(kp["date"])
                inside = bool(d and d <= deadline_for(y))
                per = "; ".join(f"{e(a['period'])}: {_fmt(a['amount'])}" for a in kp.get("allocations") or []
                                if a.get("period", "").endswith(f"/{y}"))
                how = ", ".join(sorted({how_lbl.get(a.get("matched_by"), "") for a in kp.get("allocations") or []
                                        if a.get("period", "").endswith(f"/{y}")} - {""}))
                parts.append(f"<tr class='{'hl' if inside else ''}'><td>{e(kp['date'])}</td><td class='small'>{e(kp.get('doc') or '')}</td>"
                             f"<td class='n'>{_fmt(kp['main'])}</td><td class='n'>{_fmt(kp['fees']) if kp['fees'] else ''}</td>"
                             f"<td class='small'>{per}</td><td class='small'>{e(how)}</td><td>{'✔' if inside else '—'}</td></tr>")
            parts.append("</table>")
            other = len(f.get("keao_payments") or []) - len(rel)
            if other > 0:
                parts.append(f"<p class='small muted'>{other} ακόμη καταβολή(-ές) ΚΕΑΟ αφορούν άλλες χρήσεις και δεν εμφανίζονται.</p>")
        elif f.get("keao_payments"):
            parts.append(f"<p class='small muted'>Καμία από τις {len(f['keao_payments'])} καταβολές ΚΕΑΟ δεν αφορά περιόδους {y}.</p>")
        elif not f.get("keao_available"):
            parts.append("<p class='small muted'>Δεν διαβάστηκαν πιστώσεις ΚΕΑΟ εργοδότη — τα ποσά που μεταφέρθηκαν στο ΚΕΑΟ "
                         "λογίζονται απλήρωτα.</p>")
        # κινήσεις καρτέλας
        if f.get("movements"):
            tl = {"apd": "χρέωση ΑΠΔ", "payment": "πληρωμή", "transfer": "μεταφορά στο ΚΕΑΟ"}
            parts.append(f"<h2>Κινήσεις Οικονομικής Καρτέλας — {fname}</h2>"
                         "<table><tr><th>Ημ/νία</th><th>Τύπος κίνησης</th><th>Παραστατικό</th><th>Περίοδος</th>"
                         "<th class='n'>Χρέωση</th><th class='n'>Πίστωση</th><th>Χαρακτηρισμός</th></tr>")
            for mv in f["movements"]:
                parts.append(f"<tr><td>{e(mv['date'])}</td><td>{e(mv['kind'])}</td><td class='small'>{e(mv['doc'])}</td>"
                             f"<td>{e(mv['period'])}</td><td class='n'>{_fmt(mv['debit']) if mv['debit'] else ''}</td>"
                             f"<td class='n'>{_fmt(mv['credit']) if mv['credit'] else ''}</td>"
                             f"<td class='small'>{tl.get(mv['type'], '')}</td></tr>")
            parts.append("</table>")
        if f.get("notes"):
            parts.append("<div class='expl'>" + "".join(f"<p class='small'>• {e(n)}</p>" for n in f["notes"]) + "</div>")
        parts.append("</div>")
    parts.append("<div class='foot'>Πηγές: e-ΕΦΚΑ «Οικονομική Καρτέλα Εργοδότη» (ΕΦΚΑ/ΙΚΑ, νέο ΟΠΣ) και «Οικονομική Καρτέλα "
                 "Εργοδότη ΤΕΚΑ» (κινήσεις έως το τέλος Φεβρουαρίου του επόμενου έτους)· ΚΕΑΟ «Κινήσεις Οφειλέτη → Πιστώσεις» "
                 "των μητρώων εργοδότη (Α.Μ. = ΑΜΕ). Κάθε πληρωμή της καρτέλας εξοφλεί την παλαιότερη ανοιχτή ΑΠΔ· η «ΠΕΕ ΛΟΓΩ "
                 "ΔΙΑΦΟΡΑΣ ΔΗΛΩΘΕΝΤΩΝ» μεταφέρει την απλήρωτη διαφορά στο ΚΕΑΟ· οι καταβολές του ΚΕΑΟ (κύρια εισφορά) "
                 "αντιστοιχίζονται στις μεταφορές με σειρά μεταφοράς. Προθεσμία: χρήση + 2 μήνες (δώρο Χριστουγέννων στην ΑΠΔ "
                 "Ιανουαρίου). Παράγεται αυτόματα από το ScanmyData.</div></body></html>")
    return "".join(parts)


def render_report(res: Dict[str, Any], afm: str, name: str, pdf_path: Path, ame: str = "") -> Optional[str]:
    try:
        from e3.checks.efka_teka_reconciliation import render_pdf
    except Exception:
        from efka_teka_reconciliation import render_pdf  # type: ignore
    return render_pdf(build_report_html(res, afm, name, ame), pdf_path)
