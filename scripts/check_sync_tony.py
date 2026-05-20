import os, sys, json, traceback
from pprint import pprint

REPO_ROOT = os.path.abspath(os.path.dirname(__file__) + os.sep + '..')
os.chdir(REPO_ROOT)

GROUP = 'tony'
DATA_DIR = os.path.join(REPO_ROOT, 'data')
SOURCE_DIR = os.path.join(DATA_DIR, GROUP)

print('Repo root:', REPO_ROOT)
print('Checking local dir:', SOURCE_DIR)
print('Exists:', os.path.isdir(SOURCE_DIR))

# Build local key set using push rules (root xls/xlsx -> imports/<filename>)
local_keys = set()
local_map = []
EXT = {'.json':'_json','.xlsx':'_xlsx','.xls':'_xls','.pdf':'_pdf','.csv':'_csv','.txt':'_txt','.xml':'_xml'}

for root, dirs, files in os.walk(SOURCE_DIR):
    for fname in files:
        if fname.startswith('.'):
            continue
        file_path = os.path.join(root, fname)
        rel_path = os.path.relpath(file_path, SOURCE_DIR).replace('\\','/')
        if '/' not in rel_path:
            name_no_ext, ext = os.path.splitext(fname)
            ext = ext.lower()
            if ext in ('.xls', '.xlsx'):
                fk = 'imports/' + fname
            else:
                suffix = NEXT.get(ext, '_' + ext.lstrip('.'))
                fk = f"{name_no_ext}{suffix}"
        else:
            fk = rel_path
        local_keys.add(fk)
        local_map.append((fk, file_path))

print('\nLocal mapped keys ({}):'.format(len(local_keys)))
for k,p in sorted(local_map):
    print('  ', k, '<=', p)

# Now read Firebase
try:
    from firebase import firebase_config
    inited = False
    try:
        inited = firebase_config.init_firebase()
        print('\nFirebase init result:', inited)
    except Exception as e:
        print('\nWarning: firebase_config.init_firebase() failed:', e)

    remote_raw = None
    try:
        remote_raw = firebase_config.firebase_read_data_compressed(f'/groups/{GROUP}/files') or {}
        print('\nFetched remote data from /groups/{}/files (top-level keys: {})'.format(GROUP, len(remote_raw) if isinstance(remote_raw, dict) else 0))
    except Exception as e:
        print('\nError reading remote data:', e)
        traceback.print_exc()
        remote_raw = {}

    # collect remote file keys
    remote_keys = set()
    def collect(obj, prefix=''):
        if not isinstance(obj, dict):
            return
        for key,val in obj.items():
            cur = (prefix + '/' + str(key)).lstrip('/') if prefix else str(key)
            if isinstance(val, dict) and 'content' in val and '_meta' in val:
                remote_keys.add(cur)
            elif isinstance(val, dict):
                collect(val, cur)
    collect(remote_raw, '')

    print('\nRemote keys ({}):'.format(len(remote_keys)))
    for k in sorted(remote_keys):
        print('  ', k)

    missing_on_remote = sorted(local_keys - remote_keys)
    extra_on_remote = sorted(remote_keys - local_keys)

    print('\nLocal keys missing in Firebase ({}):'.format(len(missing_on_remote)))
    for k in missing_on_remote:
        print('  ', k)

    print('\nRemote keys not present locally ({}):'.format(len(extra_on_remote)))
    for k in extra_on_remote:
        print('  ', k)

    print('\nRemote top-level children under /groups/{}/files:'.format(GROUP))
    if isinstance(remote_raw, dict):
        for k in sorted(remote_raw.keys()):
            print('  ', k)

except Exception as exc:
    print('\nFailed to run Firebase comparison:', exc)
    traceback.print_exc()
    sys.exit(2)

print('\nDone')
