# -*- coding: utf-8 -*-
"""
Δοκιμές της βάσης παραστατικών (invoice_store.py) και της σύνδεσής της στο app.py.

Οι ΠΡΑΓΜΑΤΙΚΕΣ συναρτήσεις (json_read/json_write/_unchar_collect/...) εξάγονται με AST από το app.py και τρέχουν
πάνω σε συνθετικά δεδομένα σε προσωρινό φάκελο — δεν χρειάζεται ούτε εκκίνηση της εφαρμογής ούτε data/.

    python tools/test_invoice_store.py
"""
import ast
import datetime
import errno
import io
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
import invoice_store  # noqa: E402

src = open("app.py", encoding="utf-8").read()
tree = ast.parse(src)
NAMES = {
    "json_read", "json_write", "_write_json_atomic", "_load_json", "_save_json", "_atomic_write",
    "_epsilon_stamp_times", "_epsilon_stamp_commit", "_eps_is_target", "_eps_keyed", "_eps_state",
    "_eps_classification_sig", "_EPS_STAMP_CACHE", "_EPS_STAMP_LOCK", "_EPS_CLASS_KEYS",
    "load_epsilon_cache_for_vat", "save_epsilon_cache_for_vat", "epsilon_file_path_for",
    "_unchar_digits", "_unchar_pick", "_unchar_type_code", "_unchar_inputs_from_store", "_unchar_collect",
    "_UNCHAR_BLOCKED_TYPE_CODES", "RECEIPT_FALLBACK_MARK", "get_customer_summary_file", "get_customer_docs_file",
    "_find_afm_in_epsilon", "_FETCH_BATCH", "_cf_read", "_cf_write", "_idkey", "customer_file_batch",
    "append_doc_to_customer_file", "append_summary_to_customer_file", "_doc_identity_key",
    "_same_receipt_identity", "_norm_id_value", "_pfloat_any",
}
nodes, got = [], set()
for n in tree.body:
    nm = getattr(n, "name", None)
    if nm is None and isinstance(n, (ast.Assign, ast.AnnAssign)):
        t = n.targets[0] if isinstance(n, ast.Assign) else n.target
        nm = getattr(t, "id", None)
    if nm in NAMES:
        nodes.append(n)
        got.add(nm)
assert not (NAMES - got), NAMES - got
mod = ast.Module(body=nodes, type_ignores=[])
ast.fix_missing_locations(mod)

TMP = tempfile.mkdtemp(prefix="invstore_test_")
DATA = os.path.join(TMP, "data")
GROUP = os.path.join(DATA, "g1")
os.makedirs(GROUP)
invoice_store.configure(DATA)


def _first(*a):
    for x in a:
        if x not in (None, ""):
            return x
    return ""


ns = dict(
    Any=Any, Dict=Dict, Optional=Optional, List=List, os=os, json=json, time=time, threading=threading, errno=errno,
    tempfile=tempfile, datetime=datetime, re=re, OrderedDict=OrderedDict, log=logging.getLogger("t"),
    invoice_store=invoice_store, DATA_DIR=DATA, _first=_first,
    group_path=lambda *p: os.path.join(GROUP, *p) if p else GROUP,
    secure_filename=lambda s: re.sub(r"[^A-Za-z0-9_.\-]", "_", s),
    get_active_fiscal_year=lambda: 2026,
    parse_year_from_date_string=lambda s: int(s[-4:]) if re.search(r"\d{4}$", s or "") else None,
    INVOICE_TYPE_LABELS={"1.1": "Τιμολόγιο Πώλησης"},
    _is_legacy_fetch_mode_enabled=lambda: False,
    _is_receipt_record=lambda d: bool(d.get("is_receipt") or str(d.get("type") or "").startswith("8")),
)
exec(compile(mod, "x", "exec"), ns)

bad = 0


def chk(name, cond, extra=""):
    global bad
    bad += (not cond)
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra else ""))


random.seed(11)
VAT = "801393294"


def mk(i, cls):
    return {"mark": "4000%011d" % i, "issueDate": "%02d/%02d/2026" % (1 + i % 27, 1 + i % 12), "series": "A", "aa": str(i),
            "AA": str(i), "type": "1.1", "vatCategory": "1", "totalNetValue": "%d,00" % (100 + i), "totalVatAmount": "24,00",
            "totalValue": "%d,00" % (124 + i), "classification": cls, "AFM_issuer": "1234%05d" % (i % 40),
            "Name_issuer": "ΠΡΟΜΗΘΕΥΤΗΣ %d" % (i % 40)}


classes = ["αχαρακτηριστο", "χαρακτηρισμενο", "ΑΧΑΡΑΚΤΗΡΙΣΤΟ", ""]
summ = [mk(i, random.choice(classes)) for i in range(700)]
summ.append(dict(mk(900, "αχαρακτηριστο"), is_receipt=True))
summ.append(dict(mk(901, "αχαρακτηριστο"), mark="500000000000000"))
docs = [mk(i, random.choice(classes)) for i in range(0, 700, 2)] + [mk(5, "χαρακτηρισμενο"), mk(9, "χαρακτηρισμενο")]
eps = [dict(mk(i, ""), lines=[{"id": "x", "amount": "1"}]) for i in range(0, 700, 5)]

inv_path = os.path.join(GROUP, f"{VAT}_invoices.json")
sum_path = os.path.join(GROUP, f"{VAT}_summary.json")
eps_dir = os.path.join(GROUP, "epsilon")
os.makedirs(eps_dir)
eps_path = os.path.join(eps_dir, f"{VAT}_epsilon_invoices.json")
ns["get_customer_docs_file"] = lambda vat: os.path.join(GROUP, f"{vat}_invoices.json")
ns["get_customer_summary_file"] = lambda vat: os.path.join(GROUP, f"{vat}_summary.json")
for path, payload in ((inv_path, docs), (sum_path, summ), (eps_path, eps)):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

# --- 0. classify
chk("classify invoices", invoice_store.classify(inv_path) == (GROUP, "invoices", VAT))
chk("classify summary", invoice_store.classify(sum_path) == (GROUP, "summary", VAT))
chk("classify epsilon", invoice_store.classify(eps_path) == (GROUP, "epsilon", VAT))
chk("classify legacy shadow unmanaged", invoice_store.classify(os.path.join(GROUP, "epsilon_invoices.json")) is None)
chk("classify data-root unmanaged", invoice_store.classify(os.path.join(DATA, "1_invoices.json")) is None)
chk("classify legacy data/epsilon unmanaged", invoice_store.classify(os.path.join(DATA, "epsilon", "1_epsilon_invoices.json")) is None)
chk("classify _backup dir unmanaged", invoice_store.classify(os.path.join(DATA, "_x", "1_invoices.json")) is None)
chk("classify temp filtered unmanaged", invoice_store.classify(os.path.join(TMP, "x_filtered_epsilon_invoices.json")) is None)

# --- 1. parity _unchar_collect: βάση (invoice_store) vs αρχεία (store ανενεργό)
invoice_store.configure(None)
ref = ns["_unchar_collect"](VAT)
invoice_store.configure(DATA)
new = ns["_unchar_collect"](VAT)
chk("unchar parity: same marks & order", list(ref["items"]) == list(new["items"]), (len(ref["items"]), len(new["items"])))
chk("unchar parity: hidden/fiscal", ref["hidden_other_year"] == new["hidden_other_year"] and ref["fiscal_year"] == new["fiscal_year"])
chk("unchar parity: rec/docs/code identical", all(
    ref["items"][m]["rec"] == new["items"][m]["rec"] and ref["items"][m]["docs"] == new["items"][m]["docs"]
    and ref["items"][m]["code"] == new["items"][m]["code"] for m in ref["items"]))
chk("unchar non-trivial", len(ref["items"]) > 20, len(ref["items"]))

# --- 2. json_read / json_write
a = ns["json_read"](sum_path)
chk("json_read == file", a == summ)
a.append(mk(5000, "αχαρακτηριστο"))
ns["json_write"](sum_path, a)
chk("json_write -> file export", json.load(open(sum_path, encoding="utf-8")) == a)
chk("json_read after write", ns["json_read"](sum_path) == a)
chk("db has new doc", invoice_store.find_by_mark(GROUP, "4000%011d" % 5000, kind="summary")[0]["doc"]["aa"] == "5000")

# --- 3. εξωτερική αλλαγή / σβήσιμο αρχείου
time.sleep(0.05)
with open(sum_path, "w", encoding="utf-8") as fh:
    json.dump(a[:10], fh)
chk("json_read sees external change", len(ns["json_read"](sum_path)) == 10)
chk("unchar after external change uses fresh data", len(ns["_unchar_collect"](VAT)["items"]) <= 10)
gone = os.path.join(GROUP, "000111222_summary.json")
invoice_store.write(gone, [{"mark": "1"}])
os.remove(gone)
chk("deleted file -> not found", invoice_store.read(gone) == (False, None))

# --- 4. epsilon
chk("load_epsilon_cache_for_vat", len(ns["load_epsilon_cache_for_vat"](VAT)) == len(eps))
e2 = ns["load_epsilon_cache_for_vat"](VAT)
e2.append(dict(mk(7000, ""), lines=[]))
ns["save_epsilon_cache_for_vat"](VAT, e2)
disk = json.load(open(eps_path, encoding="utf-8"))
chk("epsilon save: file has new + _saved_at stamp", len(disk) == len(eps) + 1 and disk[-1].get("_saved_at"))
chk("epsilon load after save", len(ns["load_epsilon_cache_for_vat"](VAT)) == len(eps) + 1)
legacy = os.path.join(GROUP, "epsilon_invoices.json")
ns["json_write"](legacy, [{"mark": "1"}])
chk("legacy shadow stays file-only", json.load(open(legacy)) == [{"mark": "1"}])

# --- 5. _find_afm_in_epsilon
want = eps[10]
chk("_find_afm_in_epsilon by mark", ns["_find_afm_in_epsilon"](mark=want["mark"]) == want["AFM_issuer"])
chk("_find_afm_in_epsilon miss", ns["_find_afm_in_epsilon"](mark="999999999999999") == "")

# --- 6. batch λήψης
with ns["customer_file_batch"]():
    for i in range(8000, 8050):
        ns["append_doc_to_customer_file"](mk(i, "αχαρακτηριστο"), VAT)
        ns["append_summary_to_customer_file"](mk(i, "αχαρακτηριστο"), VAT)
wantm = {"4000%011d" % i for i in range(8000, 8050)}
d = json.load(open(inv_path, encoding="utf-8"))
s_ = json.load(open(sum_path, encoding="utf-8"))
chk("batch fetch exports both files", sum(1 for x in d if x["mark"] in wantm) == 50 and
    sum(1 for x in s_ if x["mark"] in wantm) == 50)
chk("verify after all", not invoice_store.verify_group(GROUP), invoice_store.verify_group(GROUP)[:3])

# --- 7. έκδοση / κρυφή μνήμη
v0 = invoice_store.version(sum_path)
invoice_store.write(sum_path, [{"mark": "1"}])
chk("version bumps", invoice_store.version(sum_path) == v0 + 1)
chk("read gets new content (no stale cache)", ns["json_read"](sum_path) == [{"mark": "1"}])

# --- 8. store-level
rawp = os.path.join(eps_dir, "222333444_epsilon_invoices.json")
invoice_store.write(rawp, {"items": [{"mark": "1"}], "x": 1})
chk("raw (dict root) roundtrip", invoice_store.read(rawp) == (True, {"items": [{"mark": "1"}], "x": 1}))
uni = [{"mark": "1", "Name_issuer": "ΤΡΟΧΟΚΙΝΗΣΗ ΑΕΒΕ", "z": 1, "a": 2}]
pu = os.path.join(GROUP, "333_summary.json")
invoice_store.write(pu, uni)
got = invoice_store.read(pu)[1]
chk("unicode + key order preserved", got == uni and list(got[0]) == ["mark", "Name_issuer", "z", "a"])
chk("empty list store", invoice_store.write(pu, []) and invoice_store.read(pu) == (True, []) and open(pu).read() == "[]")

p3 = os.path.join(GROUP, "555_summary.json")
invoice_store.write(p3, [{"mark": "a"}])
_orig = invoice_store._replace_with_retry


def _boom(*a, **k):
    raise PermissionError("locked")


invoice_store._replace_with_retry = _boom
raised = False
try:
    invoice_store.write(p3, [{"mark": "a"}, {"mark": "b"}])
except OSError:
    raised = True
invoice_store._replace_with_retry = _orig
chk("export failure raises and rolls back", raised and invoice_store.read(p3) == (True, [{"mark": "a"}]) and
    json.load(open(p3, encoding="utf-8")) == [{"mark": "a"}])
chk("no temp leftovers", not [f for f in os.listdir(GROUP) if f.startswith(".tmp_json_")])

g2 = os.path.join(DATA, "g2")
os.makedirs(g2)
with open(os.path.join(g2, "invoices.db"), "wb") as fh:
    fh.write(b"not a sqlite database" * 100)
p4 = os.path.join(g2, "777_invoices.json")
json.dump([{"mark": "keep"}], open(p4, "w", encoding="utf-8"))
chk("corrupt db rebuilt from export", invoice_store.read(p4) == (True, [{"mark": "keep"}]) and
    any(".corrupt-" in f for f in os.listdir(g2)))

sup = invoice_store.suppliers(GROUP, vat=VAT, kind="invoices")
chk("suppliers query", len(sup) > 5 and sup[0]["count"] >= sup[-1]["count"])
chk("find_by_issuer", all(r["doc"]["AFM_issuer"] == "123400010"
                          for r in invoice_store.find_by_issuer(GROUP, "123400010", kind="invoices")))

# --- 9. ταυτοχρονισμός (threads)
errs = []
tp = [os.path.join(GROUP, f"{v}_summary.json") for v in ("111", "222", "333x")]


def tw(k):
    try:
        for i in range(25):
            p = tp[(k + i) % 3]
            cur = list(invoice_store.read(p)[1] or [])
            cur.append({"mark": f"t{k}-{i}", "AFM_issuer": str(k)})
            invoice_store.write(p, cur)
    except Exception as e:  # noqa: BLE001
        errs.append(repr(e))


ths = [threading.Thread(target=tw, args=(k,)) for k in range(6)]
[t.start() for t in ths]
[t.join() for t in ths]
chk("threads: no errors and db == files", not errs and not invoice_store.verify_group(GROUP), errs[:2])

print("\nBAD =", bad)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if bad else 0)
