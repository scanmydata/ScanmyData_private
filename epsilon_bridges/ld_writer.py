# -*- coding: utf-8 -*-
"""
ld_writer.py — φτιάχνει το XML του HyperLog από τις γραμμές γέφυρας του
ScanmyData και το γράφει ως αρχείο .ld.

    SX = Β' κατηγορία (Έσοδα-Έξοδα)      -> <DATA ModuleCode="12" FileType="SX">
    GL = Γ' κατηγορία (Γενική Λογιστική) -> <DATA ModuleCode="3"  FileType="GL">

Δέχεται ακριβώς τα dicts που ήδη μπαίνουν στο `df_moves` των
`export_multiclient_strict` / `export_g_category`, και τη λίστα
ΣΥΝΑΛΛΑΣΣΟΜΕΝΟΙ (`partners_rows`).

    from ld_writer import write_ld
    ok, issues = write_ld(flat, partners_rows, "out/2026.ld")
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    # όταν φορτώνεται ως μέρος του package epsilon_bridges
    from .ld_codec import encode_ld
except ImportError:  # pragma: no cover - fallback για απευθείας εκτέλεση/δοκιμή
    from ld_codec import encode_ld

NL = "\r\n"

# ---------------------------------------------------------------- πεδία ----
SX_ARTICLE = ["MTYPE", "MSIGN", "ISKEPYO", "ISAGRYP", "LCODE", "CUSTID", "INVOICE", "MDATE", "REASON",
              "CASHAMT", "LCCASH", "CHEQUEAMT", "LCCHEQUE",
              "TAXAMT1", "TAXAMT2", "TAXAMT3", "TAXAMT4", "TAXAMT5",
              "LCTAX1", "LCTAX2", "LCTAX3", "LCTAX4", "LCTAX5",
              "KEPYOAMT", "ISBUILD", "INBRCODE", "SUMKEPYOYP", "SUMKEPYONOTYP", "SUMKEPYOFPA",
              "OTHEREXPEND", "CASHREGISTERID", "HASRETAILID", "CANCELGROUPID", "CANCELED"]
SX_DETAIL = ["LCODE", "NETAMT", "VATAMT", "ISAGRYP", "KEPYOPARTY"]

GL_ARTICLE = ["MTYPE", "ISKEPYO", "CUSTID", "INVOICE", "MDATE", "REASON", "ISAGRYP",
              "KEPYOAMT", "INBRCODE", "SUMKEPYOYP", "SUMKEPYONOTYP", "SUMKEPYOFPA",
              "ISBUILD", "OTHEREXPEND", "CANCELED", "CANCELGROUPID", "ART39BVAT"]
GL_DETAIL = ["LCODE", "CRDB", "AMOUNT", "INVOICE", "REASON", "ISAGRYP", "KEPYOPARTY"]

CUSTOMER = ["ID", "NAME", "VAT", "JOB", "DOYCODE", "CUSTVAT", "ADDRESS", "ZIP", "CITY",
            "PHONE1", "PHONE2", "PHONE3", "FAX1", "FAX2", "EMAIL", "ISKEPYO", "ISDIMOSIOU",
            "ISEA", "ISAGRYP", "ACCADDR", "STRADDR", "STRNAME", "BANK1", "BANKACC1",
            "BANK2", "BANKACC2", "EACOUNTRY", "EAPREFIX", "EAVAT", "IDTYPE", "IDNUMB"]

AMOUNT_FIELDS = {"CASHAMT", "CHEQUEAMT", "KEPYOAMT", "SUMKEPYOYP", "SUMKEPYONOTYP", "SUMKEPYOFPA",
                 "NETAMT", "VATAMT", "AMOUNT", "KEPYOPARTY",
                 "TAXAMT1", "TAXAMT2", "TAXAMT3", "TAXAMT4", "TAXAMT5"}

_CTRL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


# ----------------------------------------------------------- μορφοποίηση ---
def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v != v:            # NaN
        return ""
    return str(v).strip()


def _esc(v: Any) -> str:
    t = _s(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return _CTRL.sub("", t)


def _num(v: Any) -> str:
    """Ποσά όπως τα γράφει το HyperLog: κόμμα δεκαδικό, κομμένα τα μηδενικά."""
    t = _s(v)
    if t == "":
        return ""
    try:
        n = float(t.replace(" ", "").replace(",", "."))
    except ValueError:
        return ""
    out = f"{n:.2f}"
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return out.replace(".", ",")


def _date(v: Any) -> str:
    """Δέχεται datetime/date, 'dd/mm/yyyy' ή 'yyyy-mm-dd' — βγάζει 'dd/mm/yyyy'."""
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.strftime("%d/%m/%Y")
    t = _s(v)
    if not t:
        return ""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return t


def _emit(fields: Sequence[str], rec: Dict[str, Any], indent: int) -> List[str]:
    pad = "\t" * indent
    out = []
    for f in fields:
        raw = rec.get(f)
        if f == "MDATE":
            val = _date(raw)
        elif f in AMOUNT_FIELDS:
            val = _num(raw)
        else:
            val = _esc(raw)
        out.append(f"{pad}<{f}>{val}</{f}>")
    return out


# ------------------------------------------------------- ομαδοποίηση -------
def detect_kind(moves: Sequence[Dict[str, Any]]) -> str:
    cols = set(moves[0].keys()) if moves else set()
    return "GL" if ("CRDB" in cols or "AMOUNT" in cols) else "SX"


def _pick(rec: Dict[str, Any], *names: str) -> Any:
    for n in names:
        if n in rec and rec[n] is not None:
            return rec[n]
    return None


def group_articles(moves: Sequence[Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    groups: Dict[Any, Dict[str, Any]] = {}
    order: List[Any] = []
    for i, r in enumerate(moves):
        key = _s(r.get("ARTID")) or f"#{i}"
        if key not in groups:
            groups[key] = {"artid": key, "header": r, "details": []}
            order.append(key)
        groups[key]["details"].append(r)

    out = []
    for key in order:
        g = groups[key]
        h = g["header"]
        sum_vat = 0.0
        for r in g["details"]:
            try:
                sum_vat += float(_s(_pick(r, "VATAMT_DETAIL", "VATAMT") or 0).replace(",", "."))
            except ValueError:
                pass
        common = {
            "MTYPE": h.get("MTYPE"), "ISKEPYO": h.get("ISKEPYO"), "ISAGRYP": h.get("ISAGRYP"),
            "CUSTID": h.get("CUSTID"), "INVOICE": h.get("INVOICE"), "MDATE": h.get("MDATE"),
            "REASON": h.get("REASON"), "SUMKEPYOYP": h.get("SUMKEPYOYP"),
            "SUMKEPYONOTYP": h.get("SUMKEPYONOTYP", "0"),
            "SUMKEPYOFPA": h["SUMKEPYOFPA"] if "SUMKEPYOFPA" in h else sum_vat,
            "OTHEREXPEND": h.get("OTHEREXPEND"), "ISBUILD": h.get("ISBUILD", "0"),
            "INBRCODE": h.get("INBRCODE"), "KEPYOAMT": h.get("KEPYOAMT"),
            "CANCELED": h.get("CANCELED"), "CANCELGROUPID": h.get("CANCELGROUPID"),
        }
        if kind == "SX":
            header = dict(common, MSIGN=h.get("MSIGN", "1"), LCODE=h.get("LCODE"),
                          CASHAMT=h.get("CASHAMT"), LCCASH=h.get("LCCASH"),
                          CHEQUEAMT=h.get("CHEQUEAMT"), LCCHEQUE=h.get("LCCHEQUE"),
                          CASHREGISTERID=h.get("CASHREGISTERID"), HASRETAILID=h.get("HASRETAILID"))
            details = [{
                "LCODE": _pick(r, "LCODE_DETAIL", "LCODE"),
                "NETAMT": _pick(r, "NETAMT_DETAIL", "NETAMT"),
                "VATAMT": _pick(r, "VATAMT_DETAIL", "VATAMT"),
                "ISAGRYP": _pick(r, "ISAGRYP_DETAIL", "ISAGRYP"),
                "KEPYOPARTY": _pick(r, "KEPYOPARTY_DETAIL", "KEPYOPARTY"),
            } for r in g["details"]]
        else:
            header = dict(common, ART39BVAT=h.get("ART39BVAT"))
            details = []
            for r in g["details"]:
                amount = r.get("AMOUNT")
                if amount is None:
                    try:
                        amount = float(_s(r.get("NETAMT") or 0).replace(",", ".")) + \
                                 float(_s(r.get("VATAMT") or 0).replace(",", "."))
                    except ValueError:
                        amount = ""
                details.append({
                    "LCODE": _pick(r, "LCODE_DETAIL", "LCODE"),
                    "CRDB": r.get("CRDB"),
                    "AMOUNT": amount,
                    "INVOICE": r.get("INVOICE_DETAIL", h.get("INVOICE")),
                    "REASON": r.get("REASON_DETAIL", h.get("REASON")),
                    "ISAGRYP": _pick(r, "ISAGRYP_DETAIL", "ISAGRYP"),
                    "KEPYOPARTY": _pick(r, "KEPYOPARTY", "KEPYOPARTY_DETAIL"),
                })
        out.append({"artid": g["artid"], "header": header, "details": details})
    return out


def normalize_partners(rows: Iterable[Dict[str, Any]], doycode: str = "") -> List[Dict[str, Any]]:
    out = []
    for r in rows or []:
        out.append({
            "ID": _pick(r, "ID", "Α/Α"),
            "NAME": _pick(r, "NAME", "ΕΠΩΝΥΜΙΑ"),
            "VAT": _pick(r, "VAT", "ΑΦΜ"),
            "JOB": r.get("JOB", ""),
            "DOYCODE": _pick(r, "DOYCODE", "ΔΟΥ") or doycode or "",
            "CUSTVAT": r.get("CUSTVAT", ""),
            "ADDRESS": r.get("ADDRESS", ""), "ZIP": r.get("ZIP", ""), "CITY": r.get("CITY", ""),
            "ISKEPYO": r.get("ISKEPYO", ""), "ISDIMOSIOU": r.get("ISDIMOSIOU", ""),
            "ISEA": r.get("ISEA", ""), "ISAGRYP": r.get("ISAGRYP", ""),
        })
    return out


# ------------------------------------------------------------- έλεγχοι -----
def validate(kind: str, articles: Sequence[Dict[str, Any]],
             customers: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []

    def add(level, code, message):
        issues.append({"level": level, "code": code, "message": message})

    cust_ids = {_s(c["ID"]) for c in customers if _s(c.get("ID"))}

    for a in articles:
        at = f"ARTID={a['artid']}"
        h = a["header"]
        if not _s(h.get("MTYPE")):
            add("error", "missing_mtype", f"{at}: λείπει MTYPE (υποχρεωτικό)")
        if not _date(h.get("MDATE")):
            add("error", "missing_mdate", f"{at}: λείπει/άκυρο MDATE (υποχρεωτικό, ηη/μμ/εεεε)")
        if not a["details"]:
            add("error", "no_details", f"{at}: δεν υπάρχουν γραμμές DETAIL")

        if kind == "SX":
            if not _s(h.get("LCODE")):
                add("error", "missing_lcode", f"{at}: λείπει LCODE κεφαλίδας (υποχρεωτικό στη Β')")
            ms = _s(h.get("MSIGN"))
            if ms and ms not in ("1", "-1"):
                add("warn", "bad_msign", f'{at}: MSIGN="{ms}" — έγκυρες τιμές 1 ή -1')
        else:
            try:
                mt = float(_s(h.get("MTYPE")))
                if mt < 10:
                    add("error", "bad_mtype_gl", f"{at}: MTYPE={_s(h.get('MTYPE'))} — στη Γ' πρέπει να είναι ≥ 10")
            except ValueError:
                pass
            deb = cred = 0.0
            for d in a["details"]:
                try:
                    v = float(_s(d.get("AMOUNT") or 0).replace(",", "."))
                except ValueError:
                    v = 0.0
                if _s(d.get("CRDB")) == "1":
                    cred += v
                else:
                    deb += v
            if abs(deb - cred) > 0.005:
                add("error", "unbalanced",
                    f"{at}: το άρθρο δεν ισοσκελίζει — χρέωση {deb:.2f} / πίστωση {cred:.2f}")

        for d in a["details"]:
            if not _s(d.get("LCODE")):
                add("error", "missing_detail_lcode", f"{at}: γραμμή χωρίς LCODE")

        cid = _s(h.get("CUSTID"))
        if cid and cust_ids and cid not in cust_ids:
            add("warn", "custid_not_listed",
                f"{at}: CUSTID={cid} δεν υπάρχει στους ΣΥΝΑΛΛΑΣΣΟΜΕΝΟΥΣ του αρχείου")

    for c in customers:
        at = f"CUSTOMER ID={_s(c.get('ID')) or '?'}"
        if not _s(c.get("ID")):
            add("error", "customer_no_id", "Συναλλασσόμενος χωρίς ID")
        if not _s(c.get("NAME")):
            add("error", "customer_no_name", f"{at}: λείπει ΕΠΩΝΥΜΙΑ")
        if not re.fullmatch(r"\d{9}", _s(c.get("VAT"))):
            add("warn", "customer_bad_vat", f'{at}: ΑΦΜ "{_s(c.get("VAT"))}" δεν είναι 9 ψηφία')
        if not _s(c.get("DOYCODE")):
            add("warn", "customer_no_doycode", f"{at}: λείπει DOYCODE")
    return issues


# ------------------------------------------------------------ σειριοποίηση -
def build_xml(kind: str, articles: Sequence[Dict[str, Any]],
              customers: Sequence[Dict[str, Any]] = (),
              version: str = "", declare: str = "iso-8859-7") -> str:
    art_fields = SX_ARTICLE if kind == "SX" else GL_ARTICLE
    det_fields = SX_DETAIL if kind == "SX" else GL_DETAIL
    attrs = [f'ModuleCode="{"12" if kind == "SX" else "3"}"', f'FileType="{kind}"']
    if version:
        attrs.append(f'Version="{_esc(version)}"')

    L = [f'<?xml version="1.0" encoding="{declare}" ?>', "<DATA " + " ".join(attrs) + ">", "\t<ARTICLES>"]
    for a in articles:
        L.append("\t\t<ARTICLE>")
        L += _emit(art_fields, a["header"], 3)
        L.append("\t\t\t<DETAILS>")
        for d in a["details"]:
            L.append("\t\t\t\t<DETAIL>")
            L += _emit(det_fields, d, 5)
            L.append("\t\t\t\t</DETAIL>")
        L.append("\t\t\t</DETAILS>")
        L.append("\t\t</ARTICLE>")
    L.append("\t</ARTICLES>")

    if customers:
        L.append("\t<CUSTOMERS>")
        for c in customers:
            L.append("\t\t<CUSTOMER>")
            L += _emit(CUSTOMER, c, 3)
            L.append("\t\t</CUSTOMER>")
        L.append("\t</CUSTOMERS>")

    L.append("</DATA>")
    return NL.join(L) + NL


def from_bridge(moves: Sequence[Dict[str, Any]],
                partners: Sequence[Dict[str, Any]] = (),
                kind: str = "auto", version: Optional[str] = None,
                doycode: str = "", include_customers: bool = True,
                declare: str = "iso-8859-7") -> Tuple[str, str, List[Dict[str, str]], Dict[str, int]]:
    """Επιστρέφει (xml, kind, issues, stats)."""
    k = detect_kind(moves) if kind == "auto" else kind
    if version is None:
        version = "26.7.1" if k == "SX" else ""
    articles = group_articles(moves, k)
    customers = normalize_partners(partners, doycode) if include_customers else []
    issues = validate(k, articles, customers)
    xml = build_xml(k, articles, customers, version=version, declare=declare)
    stats = {"articles": len(articles), "details": sum(len(a["details"]) for a in articles),
             "customers": len(customers)}
    return xml, k, issues, stats


def emit_ld_from_bridge(moves: Sequence[Dict[str, Any]], partners: Sequence[Dict[str, Any]],
                        out_path: str, *, kind: str = "auto", doycode: str = "",
                        include_customers: bool = True, force: bool = False,
                        version: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Convenience για τους exporters του ScanmyData: γράφει το .ld και επιστρέφει
    issues στη μορφή του app ({"code", "level", "message"[, "path"]}).

    * Σε επιτυχία προσθέτει {"code": "ld_created", "level": "info",
      "path": out_path} ώστε το UI/route να βρει τη διαδρομή του αρχείου.
    * Τα errors/warnings του validator μεταφέρονται με prefix "ld_" στον κωδικό.
    * Αν υπάρχουν errors (και force=False), ΔΕΝ γράφεται αρχείο — επιστρέφονται
      μόνο τα ld_ errors, οπότε το route ξέρει ότι δεν υπάρχει .ld να στείλει.
    """
    ok, issues = write_ld(
        moves, partners, out_path,
        kind=kind, doycode=doycode,
        include_customers=include_customers, force=force, version=version,
    )
    out: List[Dict[str, str]] = []
    if ok:
        out.append({
            "code": "ld_created", "level": "info",
            "message": f"Δημιουργήθηκε αρχείο HyperLog: {out_path}",
            "path": out_path,
        })
    for i in issues:
        out.append({
            "code": "ld_" + str(i.get("code", "")),
            "level": str(i.get("level", "warn")),
            "message": str(i.get("message", "")),
        })
    return out


def write_ld(moves: Sequence[Dict[str, Any]], partners: Sequence[Dict[str, Any]],
             out_path: str, *, kind: str = "auto", doycode: str = "",
             include_customers: bool = True, force: bool = False,
             version: Optional[str] = None) -> Tuple[bool, List[Dict[str, str]]]:
    """
    Γράφει κρυπτογραφημένο .ld. Επιστρέφει (ok, issues).
    Αν υπάρχουν issues με level="error" δεν γράφει τίποτα, εκτός αν force=True.
    """
    xml, _kind, issues, _stats = from_bridge(
        moves, partners, kind=kind, version=version,
        doycode=doycode, include_customers=include_customers)
    if any(i["level"] == "error" for i in issues) and not force:
        return False, issues
    with open(out_path, "wb") as fh:
        fh.write(encode_ld(xml))
    return True, issues
