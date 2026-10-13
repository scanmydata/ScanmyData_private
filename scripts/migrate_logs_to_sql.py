#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Μετατροπή ΟΛΩΝ των logs από αρχεία κειμένου/JSONL σε SQLite (μη καταστροφικά, idempotent).

  * data/<ομάδα>/activity.log, activity.log.jsonl, e3_brain_retrieval_log.jsonl  ->  data/<ομάδα>/activity.db
  * data/activity.log*, data/error.log* (logging της εφαρμογής)                   ->  data/app_log.db
  * data/<ομάδα>/error.log (αν είναι κείμενο logging)                              ->  data/app_log.db (grp=<ομάδα>)
  * προαιρετικά: αντίγραφο ομάδας από το Drive (--backup-pattern "data/_drive_backup_20261013_{group}") — εισάγονται
    το απομακρυσμένο activity.log και τα αντίγραφα activity_log_jsonl_____ (ίδιο περιεχόμενο εισάγεται μία φορά).

Δεν σβήνει και δεν αλλάζει τα αρχικά αρχεία. Ξανατρέξιμο δεν διπλασιάζει εγγραφές.

Παραδείγματα:
    python scripts/migrate_logs_to_sql.py
    python scripts/migrate_logs_to_sql.py --backup-pattern "data/_drive_backup_20261013_{group}"
    python scripts/migrate_logs_to_sql.py --group tony --status
"""
import argparse
import hashlib
import io
import os
import re
import sys

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import activity_store  # noqa: E402
import app_log_store  # noqa: E402

_TEXT_LOG_RE = re.compile(rb"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


def _looks_like_text_log(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    if not head or b"\x00" in head:
        return False
    return bool(_TEXT_LOG_RE.match(head.lstrip()))


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _import_activity(gdir: str, path: str, label: str, source_id: str = "", default_action: str = "") -> int:
    st = activity_store.import_legacy(gdir, path=path, source_id=source_id, default_action=default_action)
    print(f"    {label}: {st['lines']} γραμμές -> {st['inserted']} νέες εγγραφές")
    return st["inserted"]


def migrate_group(data_root: str, group: str, backup_pattern: str, app_db: str) -> None:
    gdir = os.path.join(data_root, group)
    print(f"[{group}]")
    for name, act in (("activity.log", ""), ("activity.log.jsonl", ""), ("e3_brain_retrieval_log.jsonl", "e3_brain_retrieval")):
        p = os.path.join(gdir, name)
        if os.path.isfile(p):
            _import_activity(gdir, p, "τοπικό " + name, default_action=act)

    err = os.path.join(gdir, "error.log")
    if os.path.isfile(err):
        if _looks_like_text_log(err):
            st = app_log_store.import_if_changed(app_db, [err], source="error", grp=group)
            print(f"    τοπικό error.log: {st['lines']} γραμμές -> {st['inserted']} εγγραφές στο app_log")
        else:
            print("    τοπικό error.log: ΠΑΡΑΛΕΙΦΘΗΚΕ (δεν είναι αρχείο κειμένου logging)")

    if backup_pattern:
        bdir = backup_pattern.replace("{group}", group)
        if not os.path.isabs(bdir):
            bdir = os.path.join(ROOT, bdir)
        if os.path.isdir(bdir):
            tag = "drive-" + os.path.basename(bdir)
            seen = set()
            for n in sorted(os.listdir(bdir)):
                p = os.path.join(bdir, n)
                if not os.path.isfile(p):
                    continue
                is_act = n == "activity.log" or n == "activity.log.jsonl" or re.fullmatch(r"activity_log_jsonl_+", n)
                is_ret = n == "e3_brain_retrieval_log.jsonl"
                if not (is_act or is_ret):
                    continue
                digest = _md5(p)
                if digest in seen:
                    print(f"    backup {n[:40]}…: ίδιο περιεχόμενο με προηγούμενο — παραλείπεται")
                    continue
                seen.add(digest)
                _import_activity(gdir, p, "backup " + n[:40], source_id=tag + ":" + n,
                                 default_action="e3_brain_retrieval" if is_ret else "")
            eb = os.path.join(bdir, "error.log")
            if os.path.isfile(eb):
                if _looks_like_text_log(eb):
                    st = app_log_store.import_if_changed(app_db, [eb], source="error", grp=group)
                    print(f"    backup error.log: {st['lines']} γραμμές -> {st['inserted']} εγγραφές στο app_log")
                else:
                    sz = round(os.path.getsize(eb) / 1048576, 1)
                    print(f"    backup error.log ({sz}MB): ΠΑΡΑΛΕΙΦΘΗΚΕ — δυαδικό αρχείο (περιέχει journal βάσης, όχι κείμενο logging)")
        else:
            print(f"    (δεν βρέθηκε backup {bdir})")
    print(f"    -> activity.db: {activity_store.count(gdir)} εγγραφές, έτοιμη: {activity_store.is_ready(gdir)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    ap.add_argument("--group", default="")
    ap.add_argument("--backup-pattern", default="", help='π.χ. "data/_drive_backup_20261013_{group}"')
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    app_db = app_log_store.default_db_path(a.data_root)

    if a.status:
        print(f"app_log.db: {len(app_log_store.recent(app_db, 10**6, 'DEBUG')) if os.path.exists(app_db) else 0} εγγραφές")
        for g in sorted(os.listdir(a.data_root)):
            gd = os.path.join(a.data_root, g)
            if os.path.isdir(gd) and not g.startswith((".", "_")):
                print(f"{g}: activity.db {activity_store.count(gd)} εγγραφές · έτοιμη: {activity_store.is_ready(gd)}")
        return 0

    if not a.group:
        print("[global app logs]")
        for base, src in (("activity.log", "app"), ("error.log", "error")):
            paths = app_log_store.rotated_family(os.path.join(a.data_root, base))
            if paths:
                st = app_log_store.import_if_changed(app_db, paths, source=src)
                print(f"    {base}* ({len(paths)} αρχεία): {st['lines']} γραμμές -> {st['inserted']} εγγραφές στο app_log")
    groups = [a.group] if a.group else sorted(
        n for n in os.listdir(a.data_root)
        if os.path.isdir(os.path.join(a.data_root, n)) and not n.startswith((".", "_")))
    for g in groups:
        migrate_group(a.data_root, g, a.backup_pattern, app_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
