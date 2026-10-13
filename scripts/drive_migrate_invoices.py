#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Μία φορά, ΜΕ ΕΛΕΓΧΟ: ο συγχρονισμός των παραστατικών στο Google Drive περνά από τα JSON στην κρυπτογραφημένη
invoices.db (βλ. docs/INVOICE_STORE.md). Ο κανονικός συγχρονισμός (push/pull της εφαρμογής) κάνει το ίδιο αυτόματα·
αυτό το script το κάνει ελεγχόμενα και ΕΠΑΛΗΘΕΥΕΙ πριν αγγίξει τα παλιά αρχεία:

  1. Κατεβάζει στην τοπική ομάδα όσα απομακρυσμένα JSON παραστατικών είναι ΝΕΟΤΕΡΑ από τα τοπικά (να μη χαθεί
     δουλειά που ανέβηκε από τον server).
  2. Εισάγει τα τοπικά JSON στη βάση και συγχωνεύει την τυχόν υπάρχουσα invoices.db του Drive (ανά store, νεότερο κερδίζει).
  3. Ανεβάζει κρυπτογραφημένο snapshot και ΕΠΑΛΗΘΕΥΕΙ (λήψη, αποκρυπτογράφηση, ίδιος αριθμός εγγραφών ανά είδος).
  4. Μόνο αν η επαλήθευση περάσει, στέλνει τα παλιά JSON στα ΑΠΟΡΡΙΜΜΑΤΑ του Drive (ανακτώνται ~30 ημέρες).

Δεν κάνει push ολόκληρης της ομάδας και δεν αγγίζει άλλα αρχεία.

Χρήση:
    python scripts/drive_migrate_invoices.py                  # dry-run (μόνο εμφάνιση)
    python scripts/drive_migrate_invoices.py --apply
    python scripts/drive_migrate_invoices.py --apply --group tony
"""
import argparse
import io
import os
import sqlite3
import sys
import tempfile
import time

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"), override=False)
except Exception:
    pass

import invoice_store as ist  # noqa: E402
from firebase import drive_storage as ds  # noqa: E402


def _remote_counts(plain: bytes) -> dict:
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        ist.extract_snapshot(plain, tmp)
        c = sqlite3.connect(tmp)
        try:
            return {k: int(n) for k, n in c.execute("SELECT kind, COUNT(*) FROM docs GROUP BY kind")}
        finally:
            c.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def migrate_group(g: str, data_root: str, apply: bool) -> bool:
    gdir = os.path.join(data_root, g)
    print(f"[{g}]")
    index = ds._build_group_index(g, force=True)
    remote_db = index.get("invoices.db")
    managed = {rel: m for rel, m in index.items() if ist.is_managed_rel(rel)}
    print(f"  Drive: invoices.db {'ναι' if remote_db else 'όχι'} · JSON παραστατικών: {len(managed)}")

    # 1. νεότερα απομακρυσμένα JSON -> τοπικά
    newer = 0
    for rel, meta in sorted(managed.items()):
        lp = os.path.join(gdir, rel.replace("/", os.sep))
        try:
            rm = float((meta.get("appProperties") or {}).get("local_mtime") or 0)
        except Exception:
            rm = 0.0
        if os.path.exists(lp) and not (rm and rm > os.path.getmtime(lp) + 2.0):
            continue
        newer += 1
        print(f"  - απομακρυσμένο νεότερο/νέο: {rel}")
        if apply:
            blob = ds._download_bytes(meta["id"])
            try:
                plain = ds._cipher().decrypt(blob)
            except Exception:
                plain = blob
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            with open(lp, "wb") as fh:
                fh.write(plain)
            if rm:
                os.utime(lp, (rm, rm))
    if not apply:
        print(f"  (dry-run) {newer} αρχεία θα κατέβαιναν, μετά snapshot upload + επαλήθευση + trash των JSON")
        return True

    # 2. εισαγωγή + συγχώνευση
    st = ist.sync_group(gdir, force=True, _root=data_root)
    print(f"  τοπικά JSON: εισήχθησαν {st['imported']}, αμετάβλητα {st['unchanged']}, αδιάβαστα {st['failed']}")
    if st["failed"]:
        print("  ΑΠΟΤΥΧΙΑ: αδιάβαστα JSON — δεν προχωράμε.")
        return False
    if remote_db:
        mst = ds._merge_remote_invoices_db(g, gdir, remote_db)
        print(f"  συγχώνευση invoices.db του Drive: {mst}")
    local = ist.stats(gdir)
    if not local["docs"]:
        print("  δεν υπάρχουν παραστατικά — τίποτα να ανέβει.")
        return True

    # 3. upload + επαλήθευση
    fp = ist.fingerprint(gdir)
    raw = ist.snapshot_bytes(gdir)
    enc = ds._cipher().encrypt(raw)
    mt = time.time()
    parent = ds._ensure_group_folder(g)
    cur = ds._build_group_index(g, force=True).get("invoices.db")
    res = None
    for attempt in range(1, 5):
        try:
            res = ds._upload_bytes(parent, "invoices.db", enc, mt, existing_id=(cur or {}).get("id"))
            break
        except Exception as e:  # noqa: BLE001
            print(f"    προσπάθεια {attempt} απέτυχε: {e}")
            time.sleep(3 * attempt)
    if res is None:
        print("  ΑΠΟΤΥΧΙΑ upload.")
        return False
    got = _remote_counts(ds._cipher().decrypt(ds._download_bytes(res["id"])))
    want = {k: v["docs"] for k, v in local["by_kind"].items()}
    ok = got == want
    print(f"  ανέβηκε κρυπτογραφημένη βάση ({len(enc) // 1024} KB) — επαλήθευση {got} == {want} -> "
          f"{'ΟΚ' if ok else 'ΑΠΟΤΥΧΙΑ'}")
    if not ok:
        return False
    ist.mark_pushed(gdir, fp, mt)

    # 4. trash των παλιών JSON
    done = 0
    for rel, meta in sorted(managed.items()):
        try:
            if ds._trash_file(meta["id"]):
                done += 1
        except Exception as e:  # noqa: BLE001
            print("    ΑΠΟΤΥΧΙΑ trash:", rel, e)
    print(f"  -> στα απορρίμματα του Drive: {done}/{len(managed)} JSON παραστατικών")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--group", default="")
    ap.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    a = ap.parse_args()
    if not ds.init_drive():
        print("Το Drive δεν αρχικοποιήθηκε.")
        return 2
    data_root = os.path.abspath(a.data_root)
    groups = [a.group] if a.group else sorted(
        g for g in os.listdir(data_root)
        if os.path.isdir(os.path.join(data_root, g)) and not g.startswith((".", "_")))
    rc = 0
    for g in groups:
        try:
            if not migrate_group(g, data_root, a.apply):
                rc = 1
        except Exception as e:  # noqa: BLE001
            print(f"  ΣΦΑΛΜΑ ({g}): {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
