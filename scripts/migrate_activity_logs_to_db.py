#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Μετάβαση των data/<ομάδα>/activity.log στη βάση SQLite data/<ομάδα>/activity.db.

Μη καταστροφική και idempotent: το activity.log ΔΕΝ αλλάζει/σβήνεται· ξανατρέξιμο δεν διπλασιάζει εγγραφές
(μοναδικό δακτυλικό αποτύπωμα ανά γραμμή) και συνεχίζει από το αποθηκευμένο offset.

Χρήση:
    python scripts/migrate_activity_logs_to_db.py                # όλες οι ομάδες στο ./data
    python scripts/migrate_activity_logs_to_db.py --group tony   # μία ομάδα
    python scripts/migrate_activity_logs_to_db.py --data-root /srv/scanmydata/data --status
"""
import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import activity_store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    ap.add_argument("--group", default="")
    ap.add_argument("--status", action="store_true", help="μόνο κατάσταση (δεν εισάγει)")
    a = ap.parse_args()
    groups = [a.group] if a.group else sorted(
        n for n in os.listdir(a.data_root)
        if os.path.isdir(os.path.join(a.data_root, n)) and not n.startswith((".", "_")))
    for g in groups:
        gdir = os.path.join(a.data_root, g)
        legacy = activity_store.legacy_path(gdir)
        size_mb = round(os.path.getsize(legacy) / 1048576, 1) if os.path.exists(legacy) else 0
        if a.status:
            print(f"{g}: activity.log {size_mb}MB · βάση: {activity_store.count(gdir)} εγγραφές · έτοιμη: {activity_store.is_ready(gdir)}")
            continue
        if not os.path.exists(legacy):
            continue
        st = activity_store.import_legacy(gdir, progress=lambda s, g=g: print(f"  {g}: {s['lines']} γραμμές…", end="\r"))
        print(f"{g}: {st['lines']} γραμμές -> {st['inserted']} νέες εγγραφές · βάση {activity_store.count(gdir)} · ολοκληρώθηκε={st['done']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
