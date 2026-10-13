#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Καθαρισμός/μετάβαση των logs στο Google Drive (μία φορά, ΜΕ ΕΛΕΓΧΟ):

  1. Ανεβάζει ΚΡΥΠΤΟΓΡΑΦΗΜΕΝΟ snapshot της τοπικής activity.db κάθε ομάδας (συγχωνεύοντας πρώτα ό,τι υπάρχει ήδη
     στο Drive) και ΕΠΑΛΗΘΕΥΕΙ ότι κατεβαίνει/αποκρυπτογραφείται και έχει τον ίδιο αριθμό εγγραφών.
  2. Μόνο αν η επαλήθευση περάσει, στέλνει στα ΑΠΟΡΡΙΜΜΑΤΑ του Drive (trash — ανακτάται για ~30 ημέρες):
       * το plaintext activity.log (αντικαταστάθηκε από την κρυπτογραφημένη βάση),
       * «σκουπίδια» ονομάτων (activity_log_jsonl_____, client_db_xls_bak_…____, *.db-journal*),
       * δυαδικά αρχεία με όνομα *.log (π.χ. error.log που είναι journal βάσης).
  3. Δεν αγγίζει κανένα άλλο αρχείο και δεν κάνει push ολόκληρης της ομάδας.

Χρήση:
    python scripts/drive_cleanup_logs.py                 # dry-run (μόνο εμφάνιση)
    python scripts/drive_cleanup_logs.py --apply         # εκτέλεση
    python scripts/drive_cleanup_logs.py --apply --group tony
"""
import argparse
import io
import os
import sys
import tempfile
import time

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"), override=False)
except Exception:
    pass

import activity_store  # noqa: E402
from firebase import drive_storage as ds  # noqa: E402


def _trash(file_id: str) -> bool:
    try:
        ds._call_with_retry(ds._service().files().update(fileId=file_id, body={"trashed": True}, fields="id").execute)
        return True
    except Exception as e:  # noqa: BLE001
        print("    ΑΠΟΤΥΧΙΑ trash:", e)
        return False


def _remote_db_count(blob: bytes) -> int:
    try:
        plain = ds._cipher().decrypt(blob)
    except Exception:
        plain = blob
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as fh:
            fh.write(plain)
        import sqlite3
        c = sqlite3.connect(tmp)
        try:
            return int(c.execute("select count(*) from activity").fetchone()[0])
        finally:
            c.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--group", default="")
    a = ap.parse_args()
    if not ds.init_drive():
        print("Το Drive δεν αρχικοποιήθηκε.")
        return 2
    data_root = os.path.join(ROOT, "data")
    groups = [a.group] if a.group else sorted(
        g for g in os.listdir(data_root)
        if os.path.isdir(os.path.join(data_root, g)) and not g.startswith((".", "_")))
    for g in groups:
        gdir = os.path.join(data_root, g)
        print(f"[{g}]")
        index = ds._build_group_index(g, force=True)
        local_rows = activity_store.count(gdir)
        remote_db = index.get("activity.db")
        print(f"  τοπική βάση: {local_rows} εγγραφές · remote activity.db: {'ναι' if remote_db else 'όχι'}")

        verified = False
        if local_rows:
            if a.apply:
                if remote_db:
                    n = ds._merge_remote_activity_db(g, gdir, remote_db)
                    print(f"  συγχώνευση υπάρχουσας remote βάσης: +{n}")
                raw = activity_store.snapshot_bytes(gdir)
                enc = ds._cipher().encrypt(raw)
                parent = ds._ensure_group_folder(g)
                mt = time.time()
                res, last_err = None, None
                for attempt in range(1, 5):          # παροδικά σφάλματα του resumable upload
                    try:
                        # ξαναβρίσκουμε το id (μπορεί να δημιουργήθηκε σε προηγούμενη απόπειρα)
                        cur = ds._build_group_index(g, force=True).get("activity.db")
                        res = ds._upload_bytes(parent, "activity.db", enc, mt, existing_id=(cur or {}).get("id"))
                        break
                    except Exception as e:  # noqa: BLE001
                        last_err = e
                        print(f"    προσπάθεια {attempt} απέτυχε: {e}")
                        time.sleep(3 * attempt)
                if res is None:
                    print(f"  ΑΠΟΤΥΧΙΑ upload: {last_err}")
                    continue
                blob = ds._download_bytes(res["id"])
                got = _remote_db_count(blob)
                expect = activity_store.count(gdir)
                verified = (got == expect)
                print(f"  ανέβηκε κρυπτογραφημένη βάση ({len(enc)//1024} KB) — επαλήθευση: {got}/{expect} εγγραφές -> "
                      f"{'ΟΚ' if verified else 'ΑΠΟΤΥΧΙΑ'}")
                if verified:
                    activity_store.mark_pushed(gdir)
                    activity_store.set_meta(gdir, "drive_merged_remote_mtime", f"{mt:.6f}")
            else:
                print("  (dry-run) θα ανέβαινε κρυπτογραφημένο snapshot της βάσης")
                verified = True
        else:
            verified = bool(remote_db)

        index = ds._build_group_index(g, force=True)
        to_trash = []
        for rel, meta in index.items():
            name = os.path.basename(rel)
            reason = ""
            if ds.is_junk_name(name):
                reason = "σκουπίδι (αντίγραφο/journal)"
            elif name == "activity.log":
                reason = "plaintext activity.log (αντικαταστάθηκε από την κρυπτογραφημένη βάση)"
            elif name.endswith(".log") and 0 < int(meta.get("size") or 0) < 300 * 1024 * 1024:
                # Παλιά push ανέβαζαν τα .log κρυπτογραφημένα· αποκρυπτογράφηση και έλεγχος: κείμενο ή δυαδικό;
                try:
                    blob = ds._download_bytes(meta["id"])
                    try:
                        plain = ds._cipher().decrypt(blob)
                    except Exception:
                        plain = blob
                    if b"\x00" in plain[:4096]:
                        reason = "δυαδικό αρχείο με όνομα .log (journal βάσης, όχι log)"
                except Exception as e:  # noqa: BLE001
                    print("    (αδυναμία ελέγχου)", name, e)
            if reason:
                to_trash.append((rel, meta, reason))
        for rel, meta, reason in sorted(to_trash):
            sz = round(int(meta.get("size") or 0) / 1024)
            print(f"  - {rel[:60]} ({sz} KB): {reason}")
        if a.apply and to_trash:
            if not verified:
                print("  ΔΕΝ γίνεται καθαρισμός: η επαλήθευση της βάσης δεν πέρασε.")
                continue
            done = sum(1 for rel, meta, _ in to_trash if _trash(meta["id"]))
            print(f"  -> στα απορρίμματα του Drive: {done}/{len(to_trash)}")
        elif to_trash:
            print(f"  (dry-run) θα πήγαιναν στα απορρίμματα: {len(to_trash)} αρχεία")
    return 0


if __name__ == "__main__":
    sys.exit(main())
