# -*- coding: utf-8 -*-
"""
epsilon_backup_writer.py — ανάγνωση/ενημέρωση backup της Epsilon (Pylon/SigmaX)

Στόχος: ο χρήστης ανεβάζει ένα ΤΡΕΧΟΝ backup μιας εταιρίας (zip με .bkp SQL
scripts), εμείς γράφουμε ΜΟΝΟ τα νέα άρθρα/χαρακτηρισμούς για τα παραστατικά που
σκαναρίστηκαν & χαρακτηρίστηκαν στο ScanmyData, και το κατεβάζουμε ξανά ως zip —
ΧΩΡΙΣ να πειράξουμε τίποτε άλλο (λογαριασμούς, πελάτες, ρυθμίσεις, άλλα tables).

Βασικά (επιβεβαιωμένα με ανάλυση 2 πραγματικών backup — Β: 00161, Γ: 00303):

* Κάθε .bkp = SQL script, κωδικοποίηση **cp1253**, γραμμές με **CRLF**:
      DECLARE @CMPCODE INT / SET @CMPCODE = :CMPCODE
      INSERT INTO <TABLE> (...) VALUES (...)          ← μία ανά εγγραφή
      DELETE FROM CNT WHERE CNTID='<pk>' ...          ← (αν υπάρχει counter)
      INSERT INTO CNT(...) VALUES('<pk>',<max>,...) ; ← επόμενο id
  Τιμές: N'..' = nvarchar, γυμνοί αριθμοί, Null, @CMPCODE, '' = escaped '.

* Κατηγορία βιβλίων:
      Γ (Γενική Λογιστική/διπλογραφικά)  → ο πίνακας `glpar` έχει εγγραφές·
                                           άρθρο = arts (κεφαλή, χωρίς ποσά) + trns
                                           (διπλογραφικά), ΧΩΡΙΣ ardt.
      Β (Έσοδα-Έξοδα/απλογραφικά)        → `glpar` κενός· άρθρο = arts (κεφαλή με
                                           ποσά) + ardt (γραμμές εξόδων/εσόδων) + trns.

* Πελάτες/λογαριασμοί: ΔΕΝ τους πειράζουμε. Το κάθε παραστατικό στον MDINVOICES
  φέρει ήδη CUSTVAT (ΑΦΜ) + CUSTID (#CUSTID<id>#) → τα χρησιμοποιούμε ως έχουν.
  Οι λογαριασμοί (acct) χρησιμοποιούνται ως έχουν· δεν προσθέτουμε ποτέ acct.

Το module ΔΕΝ υλοποιεί μόνο του τη λογιστική λογική· δέχεται «postings» (το ίδιο
περιεχόμενο που παράγει ήδη η γέφυρα: LCODE ανά γραμμή + κεφαλή) και τα σειριοποιεί
στους σωστούς πίνακες ανά κατηγορία.
"""
from __future__ import annotations

import io
import re
import uuid
import zipfile
import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

# Τα .bkp είναι κείμενο σε ελληνικό codepage (cp1253/ISO-8859-7). Κάποια αρχεία
# όμως περιέχουν σποραδικά bytes που ΔΕΝ ορίζονται στο cp1253 (π.χ. 0x8f). Για
# ΑΠΟΛΥΤΑ ασφαλές round-trip δουλεύουμε εσωτερικά σε **latin-1** (1:1 byte↔char,
# ποτέ δεν σκάει), και μετατρέπουμε σε/από ελληνικά ΜΟΝΟ όταν διαβάζουμε/γράφουμε
# πραγματικό κείμενο (ονόματα). Τα SQL keywords/αριθμοί/ονόματα στηλών είναι ASCII
# και ίδια και στα δύο codepages.
ENC = "latin-1"          # εσωτερικό, byte-preserving
GREEK = "cp1253"         # πραγματική κωδικοποίηση ελληνικών
NL = "\r\n"


def _to_view(s: str) -> str:
    """Πραγματικό (ελληνικό) string -> εσωτερική latin-1 όψη (τα bytes του cp1253)."""
    return str(s).encode(GREEK, "replace").decode("latin-1")


def to_greek(s: Optional[str]) -> Optional[str]:
    """Εσωτερική latin-1 όψη -> πραγματικό ελληνικό string (για client_db/ονόματα)."""
    if s is None:
        return None
    try:
        return s.encode("latin-1").decode(GREEK, "replace")
    except Exception:
        return s


def _num(x) -> float:
    try:
        return round(float(str(x).replace(",", ".")), 2)
    except Exception:
        return 0.0


def _date(x) -> Optional[str]:
    """'2026-08-28 00:00:00' / date -> 'YYYY-MM-DD'."""
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    return s[:10]


# ------------------------------------------------------------------ SQL value helpers
def q(v: Any) -> str:
    """Python value -> SQL literal στη μορφή του backup (N'..' / number / Null).
    Τα ελληνικά μετατρέπονται σε cp1253 bytes (μέσω της latin-1 όψης)."""
    if v is None:
        return "Null"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int,)):
        return str(v)
    if isinstance(v, float):
        # Το backup γράφει π.χ. 58.8 / 0 / 10.68 (χωρίς περιττά μηδενικά)
        s = ("%f" % v).rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    s = str(v).replace("'", "''")
    return "N'" + _to_view(s) + "'"


def _split_values(s: str) -> List[str]:
    """Χώρισε τα top-level commas ενός VALUES(...), σεβόμενος τα N'..' με '' escape."""
    out, buf, i, inq = [], [], 0, False
    while i < len(s):
        c = s[i]
        if inq:
            if c == "'":
                if i + 1 < len(s) and s[i + 1] == "'":
                    buf.append("''"); i += 2; continue
                inq = False; buf.append(c); i += 1; continue
            buf.append(c); i += 1; continue
        if c == "'":
            inq = True; buf.append(c); i += 1; continue
        if c == ",":
            out.append("".join(buf).strip()); buf = []; i += 1; continue
        buf.append(c); i += 1
    out.append("".join(buf).strip())
    return out


def _unq(raw: str) -> Optional[str]:
    """SQL literal -> python string (None για Null). Κρατά αριθμούς ως string."""
    raw = raw.strip()
    if raw == "Null" or raw == "":
        return None
    if raw == "@CMPCODE":
        return "@CMPCODE"
    if raw.startswith("N'") and raw.endswith("'"):
        return raw[2:-1].replace("''", "'")
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    return raw


# ------------------------------------------------------------------ one .bkp table
class BkpTable:
    """Ένα .bkp αρχείο: header + λίστα από INSERT rows + (προαιρετικός) CNT counter.

    Κρατάμε ΤΑ ΠΡΩΤΟΤΥΠΑ bytes και επεμβαίνουμε ελάχιστα (append rows, bump CNT,
    targeted edits) ώστε ό,τι δεν αγγίζουμε να μένει byte-for-byte ίδιο.
    """
    def __init__(self, name: str, raw: bytes):
        self.name = name
        self.raw = raw
        self.text = raw.decode(ENC)
        self.table = None
        self.columns: List[str] = []
        m = re.search(r"INSERT INTO (\w+) \(([^)]*)\) VALUES ", self.text)
        if m:
            self.table = m.group(1)
            self.columns = [c.strip() for c in m.group(2).split(",")]
        # CNT counter (pk name + value) if present
        self.cnt_pk = None
        self.cnt_val = None
        cm = re.search(r"INSERT INTO CNT\(CNTID,CNTVAL,CMPCODE\) VALUES\('([^']+)',(\d+),@CMPCODE\)", self.text)
        if cm:
            self.cnt_pk = cm.group(1)
            self.cnt_val = int(cm.group(2))

    # ---- reading ----
    def rows(self) -> List[Dict[str, Optional[str]]]:
        if not self.table:
            return []
        pat = re.compile(r"INSERT INTO %s \([^)]*\) VALUES \((.*)\)\s*$" % re.escape(self.table), re.M)
        out = []
        for m in pat.finditer(self.text):
            vals = _split_values(m.group(1))
            out.append({c: _unq(v) for c, v in zip(self.columns, vals)})
        return out

    def row_count(self) -> int:
        if not self.table:
            return 0
        return len(re.findall(r"INSERT INTO %s \(" % re.escape(self.table), self.text))

    # ---- writing ----
    def _values_sql(self, rowdict: Dict[str, Any]) -> str:
        parts = []
        for c in self.columns:
            if c == "CMPCODE":
                parts.append("@CMPCODE"); continue
            v = rowdict.get(c, None)
            if isinstance(v, _Raw):
                parts.append(v.s)
            else:
                parts.append(q(v))
        return ",".join(parts)

    def append_rows(self, rowdicts: List[Dict[str, Any]]) -> int:
        """Πρόσθεσε INSERT γραμμές ΠΡΙΝ το DELETE/INSERT CNT (ή στο τέλος)."""
        if not self.table or not rowdicts:
            return 0
        lines = [
            "INSERT INTO %s (%s) VALUES (%s)" % (self.table, ",".join(self.columns), self._values_sql(rd))
            for rd in rowdicts
        ]
        block = NL.join(lines) + NL
        anchor = "DELETE FROM CNT WHERE CNTID='%s'" % (self.cnt_pk or "")
        if self.cnt_pk and anchor in self.text:
            self.text = self.text.replace(anchor, block + anchor, 1)
        else:
            # χωρίς CNT: πρόσθεσε στο τέλος (μετά την τελευταία INSERT γραμμή)
            if not self.text.endswith(NL):
                self.text += NL
            self.text += block
        return len(rowdicts)

    def set_cnt(self, value: int) -> None:
        if self.cnt_pk is None:
            return
        self.text = re.sub(
            r"(INSERT INTO CNT\(CNTID,CNTVAL,CMPCODE\) VALUES\('%s',)\d+(,@CMPCODE\))" % re.escape(self.cnt_pk),
            r"\g<1>%d\g<2>" % value, self.text, count=1)
        self.cnt_val = value

    def replace_cell(self, match_col: str, match_val: str, set_col: str, set_val: str) -> int:
        """Targeted edit: σε γραμμές όπου match_col==match_val, θέσε set_col=set_val.
        Επιστρέφει πλήθος αλλαγών. Χειρίζεται ασφαλώς μία-μία τις INSERT γραμμές."""
        if not self.table:
            return 0
        mi = self.columns.index(match_col); si = self.columns.index(set_col)
        changed = 0
        out_lines = []
        for line in self.text.split(NL):
            m = re.match(r"(INSERT INTO %s \([^)]*\) VALUES \()(.*)(\))\s*$" % re.escape(self.table), line)
            if not m:
                out_lines.append(line); continue
            vals = _split_values(m.group(2))
            if mi < len(vals) and _unq(vals[mi]) == match_val:
                vals[si] = set_val
                line = m.group(1) + ",".join(vals) + m.group(3)
                changed += 1
            out_lines.append(line)
        if changed:
            self.text = NL.join(out_lines)
        return changed

    def to_bytes(self) -> bytes:
        return self.text.encode(ENC)


class _Raw:
    """Wrapper για να περάσουμε αυτούσιο SQL literal (π.χ. '@CMPCODE' ή '100')."""
    __slots__ = ("s",)
    def __init__(self, s: str): self.s = s


# Πεδία του arts template που είναι article-specific (πληρωμές/υπόλοιπα/σημειώσεις)
# και ΔΕΝ πρέπει να διαρρεύσουν από το άρθρο-πηγή στο νέο άρθρο· τα μηδενίζουμε.
_ARTS_NEUTRAL_NULL = frozenset({
    "CASHAMT", "CHEKAMT", "LCCASH", "LCCHEK", "KEPAMT", "CUSTPLACE", "SCODE", "RCODE",
    "REMARKS", "BRANCH", "FLGPER", "FLGCLOSE", "INBRCODE", "IDDoc", "RETIDENTID",
    "ART39BVAT", "MCODE", "PERIODFROM", "PERIODTO", "FLGEXTISO", "SUMXRE",
    "PXCODE1", "PXPRC1", "PXAMT1", "PXLCD1", "PXCODE2", "PXPRC2", "PXAMT2", "PXLCD2",
    "PXCODE3", "PXPRC3", "PXAMT3", "PXLCD3", "PXCODE4", "PXPRC4", "PXAMT4", "PXLCD4",
    "PXCODE5", "PXPRC5", "PXAMT5", "PXLCD5",
})
# Πεδία που στα γνήσια χειροκίνητα άρθρα είναι 0 (όχι Null).
_ARTS_NEUTRAL_ZERO = frozenset({"SUMOIL"})


def _neutralize_art(art: Dict[str, Any]) -> Dict[str, Any]:
    """Καθάρισε τα article-specific πεδία που κληρονομήθηκαν από το template:
    →Null όσα δεν αφορούν την εγγραφή μας, →0 όσα είναι σταθερά μηδενικά."""
    for k in _ARTS_NEUTRAL_NULL:
        if k in art:
            art[k] = None
    for k in _ARTS_NEUTRAL_ZERO:
        if k in art:
            art[k] = _Raw("0")
    return art


# ------------------------------------------------------------------ whole backup
class EpsilonBackup:
    def __init__(self, zip_path_or_bytes):
        if isinstance(zip_path_or_bytes, (bytes, bytearray)):
            self._src = io.BytesIO(bytes(zip_path_or_bytes))
        else:
            self._src = zip_path_or_bytes
        self.entries: Dict[str, bytes] = {}
        self.order: List[str] = []
        self._infos: Dict[str, zipfile.ZipInfo] = {}
        with zipfile.ZipFile(self._src) as z:
            for zi in z.infolist():
                self.order.append(zi.filename)
                self.entries[zi.filename] = z.read(zi.filename)
                self._infos[zi.filename] = zi
        self._tables: Dict[str, BkpTable] = {}

    # ---- table access (lazy) ----
    def table(self, filename: str) -> Optional[BkpTable]:
        if filename not in self.entries:
            return None
        if filename not in self._tables:
            self._tables[filename] = BkpTable(filename, self.entries[filename])
        return self._tables[filename]

    def _populated(self, filename: str) -> int:
        t = self.table(filename)
        return t.row_count() if t else 0

    # ---- category detection ----
    def category(self) -> str:
        """'G' αν είναι Γενική Λογιστική (glpar populated), αλλιώς 'B'."""
        for f in ("glpar.bkp", "BSNSGLPAR.bkp", "PYLONBSNSGLPAR.bkp", "GLPARCIRCUITS.bkp"):
            if self._populated(f) > 0:
                return "G"
        return "B"

    def version(self) -> str:
        raw = self.entries.get("version.ini", b"")
        m = re.search(r"Version=([\d.]+)", raw.decode("latin-1", "ignore"))
        return m.group(1) if m else ""

    # ---- readers ----
    def invoices(self) -> Dict[str, Dict[str, Any]]:
        """MARK -> {mdid, custvat, custid, mtype, state_clf, series, aa, issuedate, totals, lines[]}"""
        inv_t = self.table("MDINVOICES.bkp")
        dtl_t = self.table("MDINVOICESDTL.bkp")
        if not inv_t:
            return {}
        by_mark: Dict[str, Dict[str, Any]] = {}
        by_mdid: Dict[str, Dict[str, Any]] = {}
        for r in inv_t.rows():
            mark = r.get("MARK")
            rec = {
                "mdid": r.get("MDID"), "mark": mark, "custvat": r.get("CUSTVAT"),
                "custid": r.get("CUSTID"), "mtype": r.get("MTYPE"),
                "state_clf": r.get("STATE_CLF"), "series": r.get("SERIES"),
                "aa": r.get("AA"), "issuedate": r.get("ISSUEDATE"),
                "invoicetype": r.get("INVOICETYPE"),
                "total_net": r.get("TOTALNETVALUE"), "total_vat": r.get("TOTALVATAMOUNT"),
                "total_gross": r.get("TOTALGROSSVALUE"), "lines": [],
            }
            by_mdid[rec["mdid"]] = rec
            if mark:
                by_mark[mark] = rec
        if dtl_t:
            for d in dtl_t.rows():
                rec = by_mdid.get(d.get("MDID"))
                if rec is not None:
                    rec["lines"].append({
                        "mddtlid": d.get("MDDTLID"), "linenumber": d.get("LINENUMBER"),
                        "net": d.get("NETVALUE"), "vat": d.get("VATAMOUNT"),
                        "gross": d.get("GROSSAMOUNT"), "vatcategory": d.get("VATCATEGORY"),
                    })
        return by_mark

    def posted_mdids(self) -> set:
        t = self.table("MDMATCHARTS.bkp")
        return set(r.get("MDID") for r in t.rows()) if t else set()

    def classified_mdids(self) -> set:
        t = self.table("MDCLASSIFICATION.bkp")
        return set(r.get("MDID") for r in t.rows()) if t else set()

    def accounts(self) -> set:
        t = self.table("acct.bkp")
        return set(r.get("LCODE") for r in t.rows() if r.get("LCODE")) if t else set()

    def lcode_params(self) -> Dict[str, Dict[str, Any]]:
        """LCODE -> {mdclid, mdcltypeid, mdvat, vatexclid, taxcategory}"""
        t = self.table("MDLCODEPARAMS.bkp")
        out = {}
        if t:
            for r in t.rows():
                lc = r.get("LCODE")
                if lc:
                    out[lc] = {
                        "mdclid": r.get("MDCLID"), "mdcltypeid": r.get("MDCLTYPEID"),
                        "mdvat": r.get("MDVAT"), "vatexclid": r.get("VATEXCLID"),
                        "taxcategory": r.get("TAXCATEGORY"),
                    }
        return out

    def customer_map(self) -> Dict[str, Dict[str, Any]]:
        """ΑΦΜ -> {custid (#CUSTID#), name} από MDINVOICES (+ REASON ονόματα από arts).
        Χρησιμεύει για ενημέρωση του client_db της ομάδας. ΔΕΝ πειράζει πελάτες."""
        # names by custid from arts.REASON
        name_by_custid: Dict[str, str] = {}
        at = self.table("arts.bkp")
        if at:
            for r in at.rows():
                cid, nm = r.get("CUSTID"), r.get("REASON")
                if cid and nm and cid not in name_by_custid:
                    name_by_custid[cid] = nm
        out: Dict[str, Dict[str, Any]] = {}
        it = self.table("MDINVOICES.bkp")
        if it:
            for r in it.rows():
                vat, cid = r.get("CUSTVAT"), r.get("CUSTID")
                if not vat:
                    continue
                name = r.get("CUSTNAME") or name_by_custid.get(cid)
                if vat not in out:
                    out[vat] = {"custid": cid, "name": to_greek(name)}
                elif name and not out[vat].get("name"):
                    out[vat]["name"] = to_greek(name)
        return out

    # ---- serialization ----
    def to_zip_bytes(self) -> bytes:
        """Ανασύνθεση του zip διατηρώντας ΑΝΑ ENTRY τον αρχικό τύπο συμπίεσης,
        ημερομηνίες και δικαιώματα. Τα αμετάβλητα entries βγαίνουν με τα ίδια
        ακριβώς metadata· μόνο όσα πίνακες πειράχτηκαν ξαναγράφονται (ίδιος
        compress_type με το πρωτότυπο)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in self.order:
                t = self._tables.get(name)
                data = t.to_bytes() if t is not None else self.entries[name]
                src_zi = self._infos.get(name)
                if src_zi is not None:
                    zi = zipfile.ZipInfo(filename=name, date_time=src_zi.date_time)
                    zi.compress_type = src_zi.compress_type
                    zi.external_attr = src_zi.external_attr
                    zi.internal_attr = src_zi.internal_attr
                    zi.create_system = src_zi.create_system
                    zi.create_version = src_zi.create_version
                    zi.extract_version = src_zi.extract_version
                    zi.flag_bits = src_zi.flag_bits & ~0x08  # data-descriptor flag recomputed on write
                    z.writestr(zi, data)
                else:
                    z.writestr(name, data)
        return buf.getvalue()

    def save(self, out_path: str) -> None:
        with open(out_path, "wb") as fh:
            fh.write(self.to_zip_bytes())

    def diff_entries(self, other_bytes: bytes) -> List[str]:
        """Ποια entries άλλαξαν σε σχέση με το δοσμένο zip (για έλεγχο ασφάλειας)."""
        other = {}
        with zipfile.ZipFile(io.BytesIO(other_bytes)) as z:
            for n in z.namelist():
                other[n] = z.read(n)
        return [n for n in self.entries if self.entries[n] != other.get(n)]

    # ================================================================
    #  Posting generation (write new articles from ScanmyData postings)
    # ================================================================
    def learn_rules(self) -> "PostingRules":
        """Μάθε «σταθερούς» λογαριασμούς & templates ΑΠΟ ΤΟ ΙΔΙΟ backup, από ΟΛΑ τα
        άρθρα (συμπεριλαμβανομένων των χειροκίνητων/χωρίς-MARK), ώστε τα νέα άρθρα
        να μοιάζουν ακριβώς με τις υπάρχουσες εγγραφές του Epsilon.
        Προτίμηση σε templates ΧΩΡΙΣ MARK (FLGXML=0) — έτσι θα φύγουν οι δικές μας."""
        from collections import Counter, defaultdict
        cat = self.category()
        invs = self.invoices()
        inv_by_mdid = {r["mdid"]: r for r in invs.values()}
        arts_rows = self.table("arts.bkp").rows() if self.table("arts.bkp") else []
        ardt = defaultdict(list)
        if self.table("ardt.bkp"):
            for r in self.table("ardt.bkp").rows():
                ardt[r["ARTID"]].append(r)
        trns = defaultdict(list)
        if self.table("trns.bkp"):
            for r in self.table("trns.bkp").rows():
                trns[r["ARTID"]].append(r)
        mm = self.table("MDMATCHARTS.bkp").rows() if self.table("MDMATCHARTS.bkp") else []
        matched = set(m["ARTID"] for m in mm)
        mdid_by_artid = {m["ARTID"]: m["MDID"] for m in mm}

        def _f(x):
            try: return round(float(x or 0), 2)
            except Exception: return 0.0

        control_by_mtype = defaultdict(Counter)
        vat_by_mtype = defaultdict(Counter)
        afm_to_control: Dict[str, Counter] = defaultdict(Counter)
        afm_to_custid: Dict[str, Counter] = defaultdict(Counter)
        arts_template: Dict[str, Dict[str, Any]] = {}
        ardt_template: Dict[str, Dict[str, Any]] = {}
        trns_template: Dict[str, Dict[str, Any]] = {}
        tmpl_pref: Dict[str, int] = {}   # mtype -> pref of stored arts template

        for art in arts_rows:
            aid = art["ARTID"]; mt = art.get("MTYPE")
            tlines = trns.get(aid, [])
            credit = [t for t in tlines if t.get("CRDB") == "1"]
            debit = [t for t in tlines if t.get("CRDB") == "0"]
            ctrl = credit[0]["LCODE"] if credit else art.get("LCODE")
            if ctrl:
                control_by_mtype[mt][ctrl] += 1
            # VAT account = χρεωστική γραμμή με ποσό ≈ ΦΠΑ του άρθρου (arts.VATAMT,
            # ή, για Γ όπου είναι Null, από το συνδεδεμένο MDINVOICES).
            vatamt = _f(art.get("VATAMT"))
            if vatamt <= 0 and aid in matched:
                inv = inv_by_mdid.get(mdid_by_artid.get(aid))
                if inv:
                    vatamt = _f(inv.get("total_vat"))
            if vatamt > 0:
                for t in debit:
                    if abs(_f(t.get("AMOUNT")) - vatamt) <= 0.01:
                        vat_by_mtype[mt][t["LCODE"]] += 1
                        break
            is_nomark = aid not in matched
            pref = 2 if is_nomark else 1
            if mt not in arts_template or (is_nomark and tmpl_pref.get(mt, 0) < 2):
                arts_template[mt] = dict(art); tmpl_pref[mt] = pref
            if ardt.get(aid) and mt not in ardt_template:
                ardt_template[mt] = dict(ardt[aid][0])
            if tlines and mt not in trns_template:
                trns_template[mt] = dict(tlines[0])
            if aid in matched:
                inv = inv_by_mdid.get(mdid_by_artid.get(aid))
                afm = inv.get("custvat") if inv else None
                if afm:
                    if ctrl:
                        afm_to_control[afm][ctrl] += 1
                    if art.get("CUSTID"):
                        afm_to_custid[afm][art["CUSTID"]] += 1

        def top(counter):
            return counter.most_common(1)[0][0] if counter else None
        rules = PostingRules()
        rules.category = cat
        rules.control_by_mtype = {mt: top(c) for mt, c in control_by_mtype.items()}
        rules.vat_by_mtype = {mt: top(c) for mt, c in vat_by_mtype.items()}
        rules.afm_to_control = {a: top(c) for a, c in afm_to_control.items()}
        rules.afm_to_custid = {a: top(c) for a, c in afm_to_custid.items()}
        rules.arts_template = arts_template
        rules.ardt_template = ardt_template
        rules.trns_template = trns_template
        return rules

    def _next_ids(self):
        """Διάβασε τους CNT μετρητές ώστε να δώσουμε νέα ids χωρίς συγκρούσεις.
        Παίρνουμε max(CNT, max υπάρχον id) για ασφάλεια αν το CNT υπολείπεται."""
        def cnt(fn, pk):
            t = self.table(fn)
            if not t:
                return 0
            base = t.cnt_val if t.cnt_val is not None else 0
            mx = 0
            for r in t.rows():
                v = r.get(pk)
                if v not in (None, "") and str(v).lstrip("-").isdigit():
                    iv = int(v)
                    if iv > mx:
                        mx = iv
            return max(base, mx)
        return {
            "ARTID": cnt("arts.bkp", "ARTID"), "ARDTID": cnt("ardt.bkp", "ARDTID"),
            "TRNID": cnt("trns.bkp", "TRNID"), "MDCLSID": cnt("MDCLASSIFICATION.bkp", "MDCLSID"),
        }

    def add_articles_from_bridge(self, kinhseis: List[Dict[str, Any]],
                                 partners: Optional[List[Dict[str, Any]]] = None,
                                 username: str = "SCANMYDATA") -> Dict[str, Any]:
        """Γράψε ΝΕΑ χειροκίνητα άρθρα (ΧΩΡΙΣ MARK) από την έξοδο της γέφυρας — ό,τι
        δηλαδή περιέχει το Excel (φύλλο ΚΙΝΗΣΕΙΣ). Δεν αγγίζει myDATA/λογαριασμούς/
        πελάτες· τα άρθρα είναι καθαρές λογιστικές εγγραφές (FLGXML=0, ARTNUM=-ARTID).

        kinhseis: γραμμές με τα πεδία του φύλλου ΚΙΝΗΣΕΙΣ (ομαδοποίηση ανά ARTID):
            ARTID, MTYPE, MSIGN, ISKEPYO, ISAGRYP, CUSTID, MDATE, REASON, INVOICE,
            SUMKEPYOYP, LCODE (=λογ. συναλλασσόμενου), LCODE_DETAIL (=λογ. εξόδου),
            NETAMT_DETAIL, VATAMT_DETAIL, KEPYOPARTY_DETAIL.
        partners: [{Α/Α, ΑΦΜ, ΕΠΩΝΥΜΙΑ}] — επιστρέφονται για ενημέρωση client_db.
        """
        from collections import OrderedDict
        rules = self.learn_rules()
        ids = self._next_ids()
        accts = self.accounts()
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        arts_t = self.table("arts.bkp"); ardt_t = self.table("ardt.bkp"); trns_t = self.table("trns.bkp")
        is_g = (rules.category == "G")

        # Ανίχνευση schema γέφυρας: το Γ Excel δίνει ΕΤΟΙΜΕΣ γραμμές trns (ρητό CRDB+AMOUNT
        # ανά γραμμή), ενώ το Β δίνει NETAMT_DETAIL/VATAMT_DETAIL και συνθέτουμε εμείς τη
        # διπλή εγγραφή. Διαλέγουμε path βάσει των στηλών (όχι μόνο της κατηγορίας).
        explicit = bool(kinhseis) and ("CRDB" in kinhseis[0]) and ("AMOUNT" in kinhseis[0]) and any(
            str(r.get("CRDB") or "").strip() in ("0", "1") and str(r.get("AMOUNT") or "").strip() not in ("", "None")
            for r in kinhseis)

        groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
        for r in kinhseis:
            groups.setdefault(str(r.get("ARTID")), []).append(r)

        new_arts: List[Dict[str, Any]] = []
        new_ardt: List[Dict[str, Any]] = []
        new_trns: List[Dict[str, Any]] = []
        report = {"posted": 0, "skipped": [], "balance_ok": True, "category": rules.category,
                  "unknown_accounts": set()}

        def tmpl_for(store, mt):
            return dict(store.get(mt) or (next(iter(store.values())) if store else {}))

        for gid, rows in groups.items():
            h = rows[0]
            mt = str(h.get("MTYPE") or "1")

            # ---------- Γ path: έτοιμες γραμμές trns από το Excel (ρητό CRDB+AMOUNT) ----------
            if explicit:
                glines = []
                for r in rows:
                    lc = str(r.get("LCODE_DETAIL") or "").strip()
                    crdb = str(r.get("CRDB") or "").strip()
                    if not lc or crdb not in ("0", "1"):
                        continue
                    glines.append({"lcode": lc, "crdb": crdb, "amount": _num(r.get("AMOUNT")),
                                   "invoice": r.get("INVOICE_DETAIL") or r.get("INVOICE"),
                                   "reason": r.get("REASON_DETAIL") or r.get("REASON")})
                if not glines:
                    report["skipped"].append({"artid": gid, "reason": "χωρίς γραμμές"}); continue
                for l in glines:
                    if accts and l["lcode"] not in accts:
                        report["unknown_accounts"].add(l["lcode"])
                gdeb = round(sum(l["amount"] for l in glines if l["crdb"] == "0"), 2)
                gcred = round(sum(l["amount"] for l in glines if l["crdb"] == "1"), 2)
                if abs(gdeb - gcred) > 0.005:
                    report["balance_ok"] = False
                    report["skipped"].append({"artid": gid, "reason": f"μη ισοσκελισμός χρ {gdeb} πι {gcred}"}); continue
                custid = str(h.get("CUSTID") or "").strip()
                custid_ref = custid if custid.startswith("#") else ("#CUSTID%s#" % custid if custid else None)
                invoice_no = h.get("INVOICE"); reason = h.get("REASON")
                ids["ARTID"] += 1; artid = ids["ARTID"]
                art = _neutralize_art(tmpl_for(rules.arts_template, mt))
                art.update({
                    "ARTID": str(artid), "ARTNUM": "-%d" % artid, "MTYPE": mt,
                    "MSIGN": str(h.get("MSIGN") or "1"), "MDATE": _date(h.get("MDATE")),
                    "INVOICE": invoice_no, "REASON": reason,
                    "ISKEPYO": str(h.get("ISKEPYO") or "0"), "ISAGRYP": str(h.get("ISAGRYP") or "0"),
                    "CUSTID": _Raw(q(custid_ref)) if custid_ref else None,
                    "LCODE": None, "NETAMT": None, "VATAMT": None,
                    "FLGXML": "0", "FLGEXTXML": None,
                })
                for col in ("SUMKEPYOYP", "SUMKEPYONOTYP", "SUMKEPYOFPA", "OTHEREXPEND"):
                    if col in art:
                        art[col] = _Raw(q(_num(h.get(col)))) if h.get(col) not in (None, "") else _Raw("0")
                if "syncID" in art:
                    art["syncID"] = "{%s}" % str(uuid.uuid4()).upper()
                for f in ("CREATEDATE", "EDITDATE"):
                    if f in art: art[f] = now
                for f in ("CREATEUSERNAME", "EDITUSERNAME"):
                    if f in art and username: art[f] = username
                for f in ("UID", "IDDoc", "MDMTYPE", "syncVersion"):
                    if f in art: art[f] = None
                new_arts.append(art)
                tt = tmpl_for(rules.trns_template, mt)
                for l in glines:
                    ids["TRNID"] += 1
                    row = dict(tt)
                    row.update({"ARTID": str(artid), "TRNID": str(ids["TRNID"]), "LCODE": l["lcode"],
                                "ISUSER": "1", "INVOICE": l["invoice"], "CRDB": l["crdb"],
                                "AMOUNT": _Raw(q(round(l["amount"], 2))), "REASON": l["reason"],
                                "OILFLD": None, "MERCODE": None, "KEPYOPARTY": _Raw("100"), "ISAGRYP": None})
                    new_trns.append(row)
                report["posted"] += 1
                continue

            # ---------- Β path: σύνθεση διπλής εγγραφής από NETAMT_DETAIL/VATAMT_DETAIL ----------
            control = (h.get("LCODE") or rules.control_by_mtype.get(mt) or "").strip() or None
            vat_lc = rules.vat_by_mtype.get(mt)
            if not control:
                report["skipped"].append({"artid": gid, "reason": "χωρίς λογ. συναλλασσόμενου"}); continue
            lines = [{"lcode": str(r.get("LCODE_DETAIL") or "").strip(),
                      "net": _num(r.get("NETAMT_DETAIL")), "vat": _num(r.get("VATAMT_DETAIL")),
                      "kepyoparty": r.get("KEPYOPARTY_DETAIL")} for r in rows]
            lines = [l for l in lines if l["lcode"]]
            if not lines:
                report["skipped"].append({"artid": gid, "reason": "χωρίς γραμμές"}); continue
            if accts and control not in accts:
                report["unknown_accounts"].add(control)
            for l in lines:
                if accts and l["lcode"] not in accts:
                    report["unknown_accounts"].add(l["lcode"])
            net_tot = round(sum(l["net"] for l in lines), 2)
            vat_tot = round(sum(l["vat"] for l in lines), 2)
            gross = round(net_tot + vat_tot, 2)
            custid = str(h.get("CUSTID") or "").strip()
            custid_ref = custid if custid.startswith("#") else ("#CUSTID%s#" % custid if custid else None)
            invoice_no = h.get("INVOICE")
            reason = h.get("REASON")

            ids["ARTID"] += 1; artid = ids["ARTID"]
            art = _neutralize_art(tmpl_for(rules.arts_template, mt))
            art.update({
                "ARTID": str(artid), "ARTNUM": "-%d" % artid, "MTYPE": mt,
                "MSIGN": str(h.get("MSIGN") or "1"), "MDATE": _date(h.get("MDATE")),
                "INVOICE": invoice_no, "CUSTID": _Raw(q(custid_ref)) if custid_ref else None,
                "REASON": reason, "ISKEPYO": str(h.get("ISKEPYO") or "1"),
                "ISAGRYP": str(h.get("ISAGRYP") or "0"),
                "FLGXML": "0", "FLGEXTXML": None,
            })
            if not is_g:
                art.update({"LCODE": control, "NETAMT": _Raw(q(net_tot)), "VATAMT": _Raw(q(vat_tot)),
                            "SUMKEPYOYP": _Raw(q(net_tot)),
                            "SUMKEPYONOTYP": _Raw("0"), "SUMKEPYOFPA": _Raw(q(vat_tot)),
                            "OTHEREXPEND": _Raw(q(int(_num(h.get("OTHEREXPEND"))))) if h.get("OTHEREXPEND") not in (None, "") else _Raw("0")})
            else:
                art.update({"LCODE": None, "NETAMT": None, "VATAMT": None})
            if "syncID" in art:
                art["syncID"] = "{%s}" % str(uuid.uuid4()).upper()
            for f in ("CREATEDATE", "EDITDATE"):
                if f in art: art[f] = now
            for f in ("CREATEUSERNAME", "EDITUSERNAME"):
                if f in art and username: art[f] = username
            for f in ("UID", "IDDoc", "MDMTYPE", "syncVersion"):
                if f in art: art[f] = None
            new_arts.append(art)

            if not is_g and ardt_t:
                at = tmpl_for(rules.ardt_template, mt)
                for l in lines:
                    ids["ARDTID"] += 1
                    kp = l.get("kepyoparty")
                    kp = _num(kp) if kp not in (None, "") else 100
                    row = dict(at)
                    row.update({"ARTID": str(artid), "ARDTID": str(ids["ARDTID"]), "LCODE": l["lcode"],
                                "NETAMT": _Raw(q(round(l["net"], 2))), "VATAMT": _Raw(q(round(l["vat"], 2))),
                                "KEPYOPARTY": _Raw(q(kp)), "ISAGRYP": "0", "INTRID": None, "OILFLD": None})
                    new_ardt.append(row)

            tt = tmpl_for(rules.trns_template, mt)
            article_trns: List[Dict[str, Any]] = []
            def _trn(lcode, crdb, amount):
                ids["TRNID"] += 1
                row = dict(tt)
                row.update({"ARTID": str(artid), "TRNID": str(ids["TRNID"]), "LCODE": lcode,
                            "ISUSER": "0", "INVOICE": invoice_no, "CRDB": str(crdb),
                            "AMOUNT": _Raw(q(round(amount, 2))), "REASON": reason,
                            "OILFLD": None, "MERCODE": None, "KEPYOPARTY": _Raw("100")})
                article_trns.append(row)
            _trn(control, 1, gross)
            for l in lines:
                _trn(l["lcode"], 0, round(l["net"], 2))
            if vat_tot > 0 and vat_lc:
                _trn(vat_lc, 0, vat_tot)
            deb = sum(round(l["net"], 2) for l in lines) + (vat_tot if vat_lc else 0)
            if abs(round(deb, 2) - gross) > 0.005:
                report["balance_ok"] = False
                report["skipped"].append({"artid": gid, "reason": f"μη ισοσκελισμός χρ {deb} πι {gross}"})
                new_arts.pop()
                new_ardt[:] = [r for r in new_ardt if r["ARTID"] != str(artid)]
                continue
            new_trns.extend(article_trns)
            report["posted"] += 1

        if new_arts:
            arts_t.append_rows(new_arts); arts_t.set_cnt(ids["ARTID"])
        if new_ardt and ardt_t:
            ardt_t.append_rows(new_ardt); ardt_t.set_cnt(ids["ARDTID"])
        if new_trns:
            trns_t.append_rows(new_trns); trns_t.set_cnt(ids["TRNID"])
        report["counters"] = ids
        report["unknown_accounts"] = sorted(report["unknown_accounts"])
        report["client_db"] = [
            {"aa": p.get("Α/Α") or p.get("AA") or p.get("custid"),
             "afm": p.get("ΑΦΜ") or p.get("AFM") or p.get("afm"),
             "name": p.get("ΕΠΩΝΥΜΙΑ") or p.get("NAME") or p.get("name")}
            for p in (partners or [])
        ]
        return report

    def update_from_postings(self, postings: List[Dict[str, Any]], username: str = "SCANMYDATA") -> Dict[str, Any]:
        """Γράψε νέα άρθρα για τα postings (match με MARK). Ένα posting:
            {"mark": "...", "lines": [{"lcode","net","vat"[,"kepyoparty"]}, ...]}
        Δεν αγγίζει λογαριασμούς/πελάτες· χρησιμοποιεί υπάρχοντες.
        Επιστρέφει report: {posted, skipped:[{mark,reason}], balance_ok, ...}."""
        rules = self.learn_rules()
        invs = self.invoices()
        accts = self.accounts()
        ids = self._next_ids()
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        arts_t = self.table("arts.bkp"); ardt_t = self.table("ardt.bkp")
        trns_t = self.table("trns.bkp"); mm_t = self.table("MDMATCHARTS.bkp")
        cls_t = self.table("MDCLASSIFICATION.bkp"); inv_t = self.table("MDINVOICES.bkp")
        lcp = self.lcode_params()

        new_arts: List[Dict[str, Any]] = []
        new_ardt: List[Dict[str, Any]] = []
        new_trns: List[Dict[str, Any]] = []
        new_mm: List[Dict[str, Any]] = []
        new_cls: List[Dict[str, Any]] = []
        state_updates: List[str] = []   # MDIDs to set STATE_CLF=3
        report = {"posted": 0, "skipped": [], "balance_ok": True, "category": rules.category}

        posted_mdids = self.posted_mdids()

        for p in postings:
            mark = str(p.get("mark") or "").strip()
            inv = invs.get(mark)
            if not inv:
                report["skipped"].append({"mark": mark, "reason": "MARK δεν βρέθηκε στο backup"}); continue
            if inv["mdid"] in posted_mdids:
                report["skipped"].append({"mark": mark, "reason": "ήδη ποσταρισμένο (MDMATCHARTS)"}); continue
            lines = p.get("lines") or []
            if not lines:
                report["skipped"].append({"mark": mark, "reason": "χωρίς γραμμές"}); continue
            # validate all LCODEs exist in acct (μην εφευρίσκουμε λογαριασμούς)
            bad = [ln.get("lcode") for ln in lines if ln.get("lcode") not in accts]
            if bad:
                report["skipped"].append({"mark": mark, "reason": f"LCODE εκτός λογιστικού σχεδίου: {bad}"}); continue

            mt = inv["mtype"]
            afm = inv["custvat"]
            control = rules.afm_to_control.get(afm) or rules.control_by_mtype.get(mt)
            vat_lc = rules.vat_by_mtype.get(mt)
            custid = rules.afm_to_custid.get(afm) or inv["custid"]
            if not control:
                report["skipped"].append({"mark": mark, "reason": "δεν βρέθηκε λογαριασμός συναλλασσόμενου (control)"}); continue

            net_tot = round(sum(float(ln.get("net") or 0) for ln in lines), 2)
            vat_tot = round(sum(float(ln.get("vat") or 0) for ln in lines), 2)
            gross = round(net_tot + vat_tot, 2)
            reason = p.get("reason") or inv.get("custname") or (self.customer_map().get(afm, {}) or {}).get("name") or ""
            invoice_no = "%s%s" % (inv.get("series") or "", inv.get("aa") or "")

            ids["ARTID"] += 1
            artid = ids["ARTID"]
            # ---- arts header (clone template of same mtype) ----
            tmpl = dict(rules.arts_template.get(mt) or (next(iter(rules.arts_template.values())) if rules.arts_template else {}))
            art = dict(tmpl)
            art.update({
                "ARTID": str(artid), "ARTNUM": "-%d" % artid,
                "MDATE": inv.get("issuedate"), "INVOICE": invoice_no,
                "CUSTID": _Raw(q(custid)), "REASON": reason,
                "SYNCID": None,
            })
            # amounts differ per category
            if rules.category == "B":
                art.update({"LCODE": control, "NETAMT": _Raw(q(net_tot)), "VATAMT": _Raw(q(vat_tot)),
                            "SUMKEPYOYP": _Raw(q(net_tot)), "SUMKEPYONOTYP": _Raw("0"),
                            "SUMKEPYOFPA": _Raw(q(vat_tot)), "CASHAMT": _Raw("0"), "CHEKAMT": _Raw("0")})
            else:  # Γ: κεφαλή χωρίς ποσά/λογαριασμό
                art.update({"LCODE": None, "NETAMT": None, "VATAMT": None})
            if "syncID" in art:
                art["syncID"] = "{%s}" % str(uuid.uuid4()).upper()
            for f in ("CREATEDATE", "EDITDATE"):
                if f in art: art[f] = now
            for f in ("CREATEUSERNAME", "EDITUSERNAME"):
                if f in art and username: art[f] = username
            for f in ("UID", "IDDoc", "MDMTYPE"):
                if f in art: art[f] = None
            new_arts.append(art)

            # ---- ardt lines (Β only) ----
            if rules.category == "B" and ardt_t:
                at = rules.ardt_template.get(mt) or (next(iter(rules.ardt_template.values())) if rules.ardt_template else {})
                for ln in lines:
                    ids["ARDTID"] += 1
                    row = dict(at)
                    row.update({"ARTID": str(artid), "ARDTID": str(ids["ARDTID"]),
                                "LCODE": ln["lcode"], "NETAMT": _Raw(q(round(float(ln.get("net") or 0), 2))),
                                "VATAMT": _Raw(q(round(float(ln.get("vat") or 0), 2))),
                                "KEPYOPARTY": _Raw(q(ln.get("kepyoparty", 100))), "ISAGRYP": "0",
                                "INTRID": None, "OILFLD": None})
                    new_ardt.append(row)

            # ---- trns double-entry (Β & Γ) ----
            tt = rules.trns_template.get(mt) or (next(iter(rules.trns_template.values())) if rules.trns_template else {})
            def _trn(lcode, crdb, amount):
                ids["TRNID"] += 1
                row = dict(tt)
                row.update({"ARTID": str(artid), "TRNID": str(ids["TRNID"]), "LCODE": lcode,
                            "ISUSER": "0", "INVOICE": invoice_no, "CRDB": str(crdb),
                            "AMOUNT": _Raw(q(round(amount, 2))), "REASON": reason,
                            "OILFLD": None, "MERCODE": None, "KEPYOPARTY": _Raw("100")})
                new_trns.append(row)
            # credit supplier (gross), debit each expense line (net), debit VAT (total)
            _trn(control, 1, gross)
            for ln in lines:
                _trn(ln["lcode"], 0, round(float(ln.get("net") or 0), 2))
            if vat_tot > 0 and vat_lc:
                _trn(vat_lc, 0, vat_tot)
            # balance check
            deb = sum(round(float(ln.get("net") or 0), 2) for ln in lines) + (vat_tot if vat_lc else 0)
            if abs(round(deb, 2) - gross) > 0.005:
                report["balance_ok"] = False
                report["skipped"].append({"mark": mark, "reason": f"ΜΗ ισοσκελισμό: χρέωση {deb} πίστωση {gross}"})
                # roll back this article's rows
                new_arts.pop();
                new_ardt[:] = [r for r in new_ardt if r["ARTID"] != str(artid)]
                new_trns[:] = [r for r in new_trns if r["ARTID"] != str(artid)]
                continue

            # ---- MDMATCHARTS + MDCLASSIFICATION + STATE_CLF ----
            new_mm.append({"MDID": inv["mdid"], "ARTID": str(artid)})
            # map invoice detail line -> classification via LCODE->MDLCODEPARAMS
            inv_lines = inv["lines"]
            for i, ln in enumerate(lines):
                ids["MDCLSID"] += 1
                params = lcp.get(ln["lcode"], {})
                mdclid = ln.get("mdclid") or params.get("mdclid")
                mdcltypeid = ln.get("mdcltypeid") or params.get("mdcltypeid")
                mddtlid = None
                if i < len(inv_lines):
                    mddtlid = inv_lines[i].get("mddtlid")
                gross_line = round(float(ln.get("net") or 0) + float(ln.get("vat") or 0), 2)
                new_cls.append({"MDCLSID": str(ids["MDCLSID"]), "MDID": inv["mdid"], "MDDTLID": mddtlid,
                                "MDCLTYPEID": mdcltypeid, "MDCLID": mdclid, "AMOUNT": _Raw(q(gross_line)),
                                "KIND": "0", "VATAMOUNT": None, "VATCATEGORY": None,
                                "VATEXCEMPTIONCATEGORY": None, "LINENUMBER": None})
            state_updates.append(inv["mdid"])
            report["posted"] += 1

        # ---- commit: append rows + bump CNT + STATE_CLF ----
        if new_arts:
            arts_t.append_rows(new_arts); arts_t.set_cnt(ids["ARTID"])
        if new_ardt and ardt_t:
            ardt_t.append_rows(new_ardt); ardt_t.set_cnt(ids["ARDTID"])
        if new_trns:
            trns_t.append_rows(new_trns); trns_t.set_cnt(ids["TRNID"])
        if new_mm and mm_t:
            mm_t.append_rows(new_mm)
        if new_cls and cls_t:
            cls_t.append_rows(new_cls); cls_t.set_cnt(ids["MDCLSID"])
        for mdid in state_updates:
            inv_t.replace_cell("MDID", mdid, "STATE_CLF", "N'3'")
        report["counters"] = ids
        return report


def read_bridge_xlsx(path: str):
    """Διάβασε το Excel της γέφυρας -> (kinhseis_rows, partners_rows).
    Φύλλα: 'ΚΙΝΗΣΕΙΣ' (γραμμές άρθρων) + 'ΣΥΝΑΛΛΑΣΣΟΜΕΝΟΙ' (Α/Α, ΑΦΜ, ΕΠΩΝΥΜΙΑ)."""
    import pandas as pd
    xls = pd.ExcelFile(path)
    def sheet(name_options):
        for n in xls.sheet_names:
            if n in name_options:
                return pd.read_excel(path, sheet_name=n, dtype=str).fillna("")
        return None
    kin = sheet(("ΚΙΝΗΣΕΙΣ", "KINHSEIS"))
    par = sheet(("ΣΥΝΑΛΛΑΣΣΟΜΕΝΟΙ", "SYNALLASSOMENOI"))
    kinhseis = kin.to_dict("records") if kin is not None else []
    partners = par.to_dict("records") if par is not None else []
    return kinhseis, partners


class PostingRules:
    """Σταθερές που «μάθαμε» από το ίδιο backup για πιστή αναπαραγωγή άρθρων."""
    category = "B"
    control_by_mtype: Dict[str, str] = {}
    vat_by_mtype: Dict[str, str] = {}
    afm_to_control: Dict[str, str] = {}
    afm_to_custid: Dict[str, str] = {}
    arts_template: Dict[str, Dict[str, Any]] = {}
    ardt_template: Dict[str, Dict[str, Any]] = {}
    trns_template: Dict[str, Dict[str, Any]] = {}
