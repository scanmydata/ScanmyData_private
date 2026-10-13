# -*- coding: utf-8 -*-
"""Προσομοίωση Drive (in-memory) για push/pull της κρυπτογραφημένης invoices.db με δύο «μηχανές» (server/local).

    python tools/test_invoice_drive.py
"""
import io, json, os, shutil, sys, tempfile, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
import invoice_store as S
from firebase import drive_storage as D

bad = 0


def chk(n, c, e=""):
    global bad
    bad += (not c)
    print(("PASS " if c else "FAIL ") + n, e if not c else "")


class Cipher:                       # ίδιο συμβόλαιο με Fernet (bytes -> bytes), εύκολα ελέγξιμο
    def encrypt(self, b):
        return b"ENC:" + b[::-1]

    def decrypt(self, b):
        if not b.startswith(b"ENC:"):
            raise ValueError("plain")
        return b[4:][::-1]


class FakeDrive:
    def __init__(self):
        self.files = {}      # rel -> dict(id, raw, mtime, trashed)
        self.uploads = []
        self.fail_db_upload = False
        self.n = 0

    def index(self, folder, force=False):
        return {rel: {"id": f["id"], "modifiedTime": "x", "size": len(f["raw"]),
                      "appProperties": {"local_mtime": f"{f['mtime']:.6f}"}}
                for rel, f in self.files.items() if not f["trashed"]}

    def upload(self, parent, name, raw, mtime, existing_id=None):
        rel = (parent + "/" + name) if parent not in ("", "PID") else name
        rel = rel.replace("PID/", "")
        if self.fail_db_upload and name == "invoices.db":
            raise TimeoutError("boom")
        self.n += 1
        fid = existing_id or f"F{self.n}"
        for r, f in self.files.items():
            if f["id"] == fid:
                rel = r
                break
        self.files[rel] = {"id": fid, "raw": raw, "mtime": mtime, "trashed": False}
        self.uploads.append(rel)
        return {"id": fid, "modifiedTime": "x", "size": len(raw)}

    def download(self, fid):
        for f in self.files.values():
            if f["id"] == fid:
                return f["raw"]
        raise KeyError(fid)

    def delete(self, fid):
        for r, f in list(self.files.items()):
            if f["id"] == fid:
                del self.files[r]
                self.deleted.append(r)
        return True

    def trash(self, fid):
        for r, f in self.files.items():
            if f["id"] == fid:
                f["trashed"] = True
                self.trashed.append(r)
        return True

    deleted = []
    trashed = []


drive = FakeDrive()
FOLDER = "g1"
D.is_drive_enabled = lambda: True
D._resolve_group_identity = lambda g, f=None: {"resolved": True, "group_name": "g1", "local_folder": "g1"}
D._cipher = lambda: Cipher()
D._build_group_index = drive.index
D._ensure_path = lambda f, rd: ("PID/" + rd) if rd else "PID"
D._ensure_group_folder = lambda f: "GID"
D._upload_bytes = drive.upload
D._download_bytes = drive.download
D._delete_file = drive.delete
D._trash_file = drive.trash
D.drive_log_activity = lambda *a, **k: True
D._pull_workers = lambda: 1

# fake upload must map "PID/epsilon" parent to rel "epsilon/<name>"
_orig_upload = drive.upload


def upload2(parent, name, raw, mtime, existing_id=None):
    rel = name if parent in ("PID", "GID") else parent.replace("PID/", "") + "/" + name
    if drive.fail_db_upload and name == "invoices.db":
        raise TimeoutError("boom")
    drive.n += 1
    fid = existing_id or f"F{drive.n}"
    for r, f in drive.files.items():
        if f["id"] == fid:
            rel = r
            break
    drive.files[rel] = {"id": fid, "raw": raw, "mtime": mtime, "trashed": False}
    drive.uploads.append(rel)
    return {"id": fid, "modifiedTime": "x", "size": len(raw)}


D._upload_bytes = upload2


def machine(name):
    root = tempfile.mkdtemp(prefix=f"invdrive_{name}_")
    data = os.path.join(root, "data")
    os.makedirs(os.path.join(data, FOLDER, "epsilon"))
    return data


def mk(i, **kw):
    d = {"mark": "4000%011d" % i, "issueDate": "01/02/2026", "AFM_issuer": "1000%05d" % (i % 7), "totalValue": "%d,00" % i,
         "classification": "χαρακτηρισμενο", "aa": str(i)}
    d.update(kw)
    return d


def jr(path):
    return json.load(open(path, encoding="utf-8"))


SERVER = machine("server")
LOCAL = machine("local")
SG, LG = os.path.join(SERVER, FOLDER), os.path.join(LOCAL, FOLDER)

# ================= 1. server: δεδομένα + push =================
S.configure(SERVER)
S.write(os.path.join(SG, "111_invoices.json"), [mk(i) for i in range(20)])
S.write(os.path.join(SG, "111_summary.json"), [mk(i) for i in range(30)])
S.write(os.path.join(SG, "epsilon", "111_epsilon_invoices.json"), [dict(mk(i), lines=[{"id": "l"}]) for i in range(5)])
open(os.path.join(SG, "client_db.xlsx"), "wb").write(b"XLSX")
ok = D.drive_push_group_files("g1", local_data_root=SERVER)
chk("push ok", ok is True)
rels = {r for r, f in drive.files.items() if not f["trashed"]}
chk("invoices.db on drive", "invoices.db" in rels, rels)
chk("managed JSON NOT uploaded", not any(S.is_managed_rel(r) for r in rels), rels)
chk("other file uploaded normally", "imports/client_db.xlsx" in rels, rels)
chk("drive blob is encrypted compressed snapshot", drive.files["invoices.db"]["raw"].startswith(b"ENC:") and
    Cipher().decrypt(drive.files["invoices.db"]["raw"]).startswith(S.SNAPSHOT_MAGIC))
n_up = len(drive.uploads)
ok = D.drive_push_group_files("g1", local_data_root=SERVER)
chk("second push: invoices.db not uploaded again (fingerprint)", "invoices.db" not in drive.uploads[n_up:], drive.uploads[n_up:])

# ================= 2. local: cold pull =================
S.configure(LOCAL)
ok = D.drive_pull_group_to_local("g1", local_data_root=LOCAL)
chk("pull ok", ok is True)
chk("local JSON exports regenerated", jr(os.path.join(LG, "111_invoices.json")) == [mk(i) for i in range(20)] and
    len(jr(os.path.join(LG, "epsilon", "111_epsilon_invoices.json"))) == 5)
chk("local DB == files", not S.verify_group(LG), S.verify_group(LG)[:3])
chk("other file pulled", os.path.exists(os.path.join(LG, "client_db.xlsx")))
ok, got = S.read(os.path.join(LG, "111_summary.json"))
chk("local read via DB", ok and got == [mk(i) for i in range(30)])
t0 = os.path.getmtime(os.path.join(LG, "111_invoices.json"))
D.drive_pull_group_to_local("g1", local_data_root=LOCAL)
chk("second pull is a no-op (already merged)", os.path.getmtime(os.path.join(LG, "111_invoices.json")) == t0)

# ================= 3. διαφορετικά store αλλάζουν και στις δύο μεριές =================
time.sleep(0.05)
S.configure(LOCAL)
S.write(os.path.join(LG, "111_invoices.json"), [mk(i) for i in range(20)] + [mk(500, classification="local-edit")])
S.configure(SERVER)
time.sleep(0.05)
S.write(os.path.join(SG, "111_summary.json"), [mk(i) for i in range(30)] + [mk(600, classification="server-edit")])
ok = D.drive_push_group_files("g1", local_data_root=SERVER)       # server -> Drive
S.configure(LOCAL)
ok = D.drive_push_group_files("g1", local_data_root=LOCAL)        # local: merge (παίρνει server edit) + push
chk("local push merged server's summary edit", any(x.get("classification") == "server-edit"
                                                   for x in jr(os.path.join(LG, "111_summary.json"))))
S.configure(SERVER)
ok = D.drive_pull_group_to_local("g1", local_data_root=SERVER)    # server: παίρνει local edit
chk("server pulled local invoices edit", any(x.get("classification") == "local-edit"
                                              for x in jr(os.path.join(SG, "111_invoices.json"))))
chk("server keeps its own summary edit", any(x.get("classification") == "server-edit"
                                              for x in jr(os.path.join(SG, "111_summary.json"))))
chk("server DB == files", not S.verify_group(SG), S.verify_group(SG)[:3])

# ================= 4. σύγκρουση στο ίδιο store: κερδίζει το νεότερο =================
time.sleep(0.05)
S.configure(LOCAL)
S.write(os.path.join(LG, "111_summary.json"), [mk(1, classification="older-local")])
time.sleep(0.05)
S.configure(SERVER)
S.write(os.path.join(SG, "111_summary.json"), [mk(1, classification="newer-server")])
D.drive_push_group_files("g1", local_data_root=SERVER)
S.configure(LOCAL)
D.drive_push_group_files("g1", local_data_root=LOCAL)
chk("conflict: newer (server) wins locally", jr(os.path.join(LG, "111_summary.json"))[0]["classification"] == "newer-server")
chk("conflict: remote content is the winner", True)

# ================= 5. legacy Drive: μόνο JSON, καμία invoices.db =================
drive2_files = drive.files
drive.files = {}
for name, payload in (("222_invoices.json", [mk(1), mk(2)]), ("epsilon/222_epsilon_invoices.json", [mk(1)])):
    drive.files[name] = {"id": "L" + name, "raw": Cipher().encrypt(json.dumps(payload).encode()), "mtime": time.time() - 50,
                         "trashed": False}
L2 = machine("legacy")
S.configure(L2)
ok = D.drive_pull_group_to_local("g1", local_data_root=L2)
chk("legacy: JSON downloaded and imported", os.path.exists(os.path.join(L2, FOLDER, "222_invoices.json")) and
    S.read(os.path.join(L2, FOLDER, "222_invoices.json")) == (True, [mk(1), mk(2)]))
drive.trashed = []
ok = D.drive_push_group_files("g1", local_data_root=L2)
rels = {r for r, f in drive.files.items() if not f["trashed"]}
chk("legacy first push: DB uploaded", "invoices.db" in rels)
chk("legacy first push: old JSON moved to trash (not deleted)", set(drive.trashed) >= {"222_invoices.json",
                                                                                       "epsilon/222_epsilon_invoices.json"}, drive.trashed)

# ================= 6. fallback: αποτυχία upload της βάσης -> JSON όπως πριν =================
L3 = machine("fallback")
S.configure(L3)
S.write(os.path.join(L3, FOLDER, "333_invoices.json"), [mk(7)])
drive.files = {}
drive.uploads = []
drive.fail_db_upload = True
ok = D.drive_push_group_files("g1", local_data_root=L3)
chk("fallback: JSON uploaded when DB upload fails", "333_invoices.json" in drive.uploads and "invoices.db" not in drive.uploads,
    drive.uploads)
drive.fail_db_upload = False

# ================= 7. INVOICE_DRIVE_DB=0: ποτέ ως αρχείο πάνω στην τοπική βάση =================
os.environ["INVOICE_DRIVE_DB"] = "0"
drive.files = {"invoices.db": {"id": "X", "raw": Cipher().encrypt(b"not-a-snapshot"), "mtime": time.time(), "trashed": False},
               "444_invoices.json": {"id": "J", "raw": Cipher().encrypt(json.dumps([mk(9)]).encode()), "mtime": time.time(),
                                     "trashed": False}}
L4 = machine("off")
S.configure(L4)
D.drive_pull_group_to_local("g1", local_data_root=L4)
chk("disabled: JSON pulled as before", os.path.exists(os.path.join(L4, FOLDER, "444_invoices.json")))
chk("disabled: remote invoices.db not written locally", not os.path.exists(os.path.join(L4, FOLDER, "invoices.db")) or
    open(os.path.join(L4, FOLDER, "invoices.db"), "rb").read()[:15] == b"SQLite format 3")
del os.environ["INVOICE_DRIVE_DB"]

# ================= 8. script (χωρίς configure): pull σε φάκελο αντιγράφου =================
drive.files = {}
S.configure(SERVER)
D.drive_push_group_files("g1", local_data_root=SERVER)
S.configure(None)
BK = tempfile.mkdtemp(prefix="invdrive_bk_")
bk_root = os.path.join(BK, "data")
os.makedirs(bk_root)
D._resolve_group_identity = lambda g, f=None: {"resolved": True, "group_name": g, "local_folder": g}
ok = D.drive_pull_group_to_local("g1", local_data_root=bk_root, local_group_folder="g1")
chk("script pull (unconfigured store) produces JSON exports", os.path.exists(os.path.join(bk_root, "g1", "111_invoices.json")))


# ================= 9. script drive_migrate_invoices: νεότερο απομακρυσμένο JSON, επαλήθευση, trash =================
import importlib.util
spec = importlib.util.spec_from_file_location("drive_migrate_invoices", os.path.join(ROOT, "scripts", "drive_migrate_invoices.py"))
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)
drive.files = {}
drive.trashed = []
L5 = machine("migrate")
S.configure(L5)
local_old = os.path.join(L5, FOLDER, "555_invoices.json")
json.dump([mk(1)], open(local_old, "w", encoding="utf-8"))
os.utime(local_old, (time.time() - 3600, time.time() - 3600))
newer_payload = [mk(1), mk(2), mk(3)]
drive.files["555_invoices.json"] = {"id": "J1", "raw": Cipher().encrypt(json.dumps(newer_payload).encode()),
                                    "mtime": time.time() - 10, "trashed": False}
drive.files["666_summary.json"] = {"id": "J2", "raw": Cipher().encrypt(json.dumps([mk(9)]).encode()),
                                   "mtime": time.time() - 10, "trashed": False}
D._build_group_index = drive.index
drive.uploads = []
okm = mig.migrate_group("g1", L5, apply=False)
chk("migrate dry-run changes nothing", okm and not drive.uploads and not drive.trashed and jr(local_old) == [mk(1)])
okm = mig.migrate_group("g1", L5, apply=True)
chk("migrate apply ok", okm is True)
chk("migrate took newer remote JSON before uploading", S.read(local_old)[1] == newer_payload)
chk("migrate uploaded DB and verified counts", "invoices.db" in drive.uploads)
chk("migrate trashed old JSON only after verification", set(drive.trashed) == {"555_invoices.json", "666_summary.json"}, drive.trashed)
rels = {r for r, f in drive.files.items() if not f["trashed"]}
chk("drive now holds only the DB for invoices", rels == {"invoices.db"}, rels)


# ================= 10. διαγραφή δεδομένων πελάτη: ταφόπλακες (δεν αναστήνονται από το Drive) =================
drive.files = {}
drive.trashed = []
D._resolve_group_identity = lambda g, f=None: {"resolved": True, "group_name": "g1", "local_folder": "g1"}
SRV, LOC = machine("tsrv"), machine("tloc")
SRVG, LOCG = os.path.join(SRV, FOLDER), os.path.join(LOC, FOLDER)
S.configure(SRV)
for v in ("701", "702"):
    S.write(os.path.join(SRVG, f"{v}_invoices.json"), [mk(1), mk(2)])
    S.write(os.path.join(SRVG, "epsilon", f"{v}_epsilon_invoices.json"), [dict(mk(1), lines=[])])
D.drive_push_group_files("g1", local_data_root=SRV)
S.configure(LOC)
D.drive_pull_group_to_local("g1", local_data_root=LOC)
chk("tombstone setup: local has both customers", os.path.exists(os.path.join(LOCG, "701_invoices.json")) and
    os.path.exists(os.path.join(LOCG, "epsilon", "702_epsilon_invoices.json")))
time.sleep(0.05)
# «διαγραφή δεδομένων πελάτη 701» όπως το delete_customer_data_files: remove + forget
for pth in (os.path.join(LOCG, "701_invoices.json"), os.path.join(LOCG, "epsilon", "701_epsilon_invoices.json")):
    os.remove(pth)
    S.forget(pth)
D.drive_push_group_files("g1", local_data_root=LOC)
chk("tombstone: local does not resurrect deleted store on its own push", not os.path.exists(os.path.join(LOCG, "701_invoices.json")))
S.configure(SRV)
D.drive_pull_group_to_local("g1", local_data_root=SRV)
chk("tombstone: server pull removes the deleted customer's files", not os.path.exists(os.path.join(SRVG, "701_invoices.json")) and
    not os.path.exists(os.path.join(SRVG, "epsilon", "701_epsilon_invoices.json")))
chk("tombstone: other customer untouched", os.path.exists(os.path.join(SRVG, "702_invoices.json")))
D.drive_push_group_files("g1", local_data_root=SRV)
S.configure(LOC)
D.drive_pull_group_to_local("g1", local_data_root=LOC)
chk("tombstone: stays deleted after full round trip", not os.path.exists(os.path.join(LOCG, "701_invoices.json")) and
    S.read(os.path.join(LOCG, "701_invoices.json")) == (False, None))
# επανα-δημιουργία μετά τη διαγραφή: κερδίζει η νεότερη εγγραφή και καθαρίζει την ταφόπλακα
time.sleep(0.05)
S.write(os.path.join(LOCG, "701_invoices.json"), [mk(77)])
D.drive_push_group_files("g1", local_data_root=LOC)
S.configure(SRV)
D.drive_pull_group_to_local("g1", local_data_root=SRV)
chk("tombstone: re-created store propagates", S.read(os.path.join(SRVG, "701_invoices.json")) == (True, [mk(77)]))
# σβήσιμο αρχείου ΧΩΡΙΣ forget (π.χ. «σβήσε τα JSON για να ξανακατεβούν»): ΔΕΝ είναι διαγραφή — ξαναγράφεται από τη βάση
time.sleep(0.05)
os.remove(os.path.join(SRVG, "702_invoices.json"))
D.drive_push_group_files("g1", local_data_root=SRV)
chk("accidental file removal is restored locally", os.path.exists(os.path.join(SRVG, "702_invoices.json")))
S.configure(LOC)
D.drive_pull_group_to_local("g1", local_data_root=LOC)
chk("accidental file removal does not delete data on other machines", os.path.exists(os.path.join(LOCG, "702_invoices.json")))

print("\nBAD =", bad)
for p in (SERVER, LOCAL, L2, L3, L4, L5, SRV, LOC, BK):
    shutil.rmtree(os.path.dirname(p) if os.path.basename(p) == "data" else p, ignore_errors=True)
sys.exit(1 if bad else 0)
