#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Μετάβαση των JSON παραστατικών κάθε ομάδας στη βάση SQLite της (invoice_store) — μη καταστροφικά, idempotent.

  data/<ομάδα>/<ΑΦΜ>_invoices.json, <ΑΦΜ>_summary.json, epsilon/<ΑΦΜ>_epsilon_invoices.json  ->  data/<ομάδα>/invoices.db

Τα JSON ΔΕΝ αλλάζουν ούτε σβήνονται (παραμένουν το export που ταξιδεύει στο Drive). Ξανατρέξιμο δεν διπλασιάζει
τίποτα: εισάγονται μόνο τα αρχεία που άλλαξαν από την τελευταία εισαγωγή.

Χρήση:
    python scripts/migrate_invoices_to_sql.py                  # όλες οι ομάδες
    python scripts/migrate_invoices_to_sql.py --group tony
    python scripts/migrate_invoices_to_sql.py --status         # μόνο πλήθη
    python scripts/migrate_invoices_to_sql.py --verify         # σύγκριση αρχείων <-> βάσης (κωδικός εξόδου 1 αν διαφέρουν)
    python scripts/migrate_invoices_to_sql.py --rebuild        # σβήνει τη βάση (όχι τα JSON) και την ξαναχτίζει
"""
import argparse
import io
import os
import sys
import time

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import invoice_store  # noqa: E402


def _groups(data_root: str, only: str):
    if only:
        return [only]
    return sorted(g for g in os.listdir(data_root)
                  if os.path.isdir(os.path.join(data_root, g)) and not g.startswith((".", "_")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    ap.add_argument("--group", default="")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.data_root)
    invoice_store.configure(root, enabled=True)

    rc = 0
    for g in _groups(root, a.group):
        gdir = os.path.join(root, g)
        if not os.path.isdir(gdir):
            print(f"[{g}] δεν υπάρχει")
            continue
        print(f"[{g}]")
        if a.status:
            st = invoice_store.stats(gdir)
            print(f"    βάση: {st['stores']} store, {st['docs']} εγγραφές  {st['by_kind']}")
            continue
        if a.rebuild:
            for suffix in ("", "-wal", "-shm"):
                p = invoice_store.db_path(gdir) + suffix
                if os.path.exists(p):
                    os.remove(p)
            print("    βάση σβήστηκε (τα JSON δεν αγγίχθηκαν)")
        t0 = time.time()
        if not a.verify:
            res = invoice_store.sync_group(gdir, force=True)
            print(f"    αρχεία: {res['files']} · εισήχθησαν: {res['imported']} · αμετάβλητα: {res['unchanged']} · "
                  f"αδιάβαστα: {res['failed']} · σβησμένα: {res['removed']} ({time.time() - t0:.1f}s)")
            if res["failed"]:
                rc = 1
        probs = invoice_store.verify_group(gdir)
        st = invoice_store.stats(gdir)
        print(f"    βάση: {st['stores']} store, {st['docs']} εγγραφές · επαλήθευση: "
              f"{'ΟΚ' if not probs else str(len(probs)) + ' διαφορές'}")
        for pr in probs[:20]:
            print("      -", pr)
        if probs:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
