"""Clear every DB-backed session claim.

Run this BEFORE committing firebed.db.

Why: DATABASE_URL is unset, so app.py falls back to sqlite:///<repo>/firebed.db --
the committed file *is* the production database. A deploy therefore ships whatever
session state the local copy happened to have, and the login in
firebase/firebase_auth_routes.py blocks any account whose current_session_id is set
and whose last_active_at is younger than SESSION_TIMEOUT_SECONDS. That is what
produced "Ο λογαριασμός είναι ήδη ενεργός σε άλλη συσκευή/σύνδεση" straight after a
deploy: the DB arrived saying the account was already logged in somewhere.

    py scripts/clear_all_sessions.py
"""
import os
import sqlite3

base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
db_path = os.getenv('DATABASE_URL') or ('sqlite:///' + os.path.join(base, 'firebed.db'))
if db_path.startswith('sqlite:///'):
    db_path = db_path.replace('sqlite:///', '')

print('DB path:', db_path)
if not os.path.exists(db_path):
    print('Database not found:', db_path)
    raise SystemExit(1)

con = sqlite3.connect(db_path)
cur = con.cursor()

# tab_alive_at is added by the idempotent migration in app.py, so it may not exist
# yet on a DB that has not been booted against current code.
columns = {row[1] for row in cur.execute('PRAGMA table_info(user)')}
session_columns = [c for c in ('current_session_id', 'session_started_at',
                               'last_active_at', 'tab_alive_at') if c in columns]
missing = {'current_session_id', 'session_started_at', 'last_active_at', 'tab_alive_at'} - columns
if missing:
    print('Note: columns not present in this DB, skipping them:', ', '.join(sorted(missing)))

# Any of these being set can make the account look "live" to the login check, so
# count rows where *any* is set rather than keying off current_session_id alone.
where_any_set = ' OR '.join(f'{c} IS NOT NULL' for c in session_columns)

before = cur.execute(f'SELECT COUNT(*) FROM user WHERE {where_any_set}').fetchone()[0]
print('Users with session state before clear:', before)

cur.execute(f"UPDATE user SET {', '.join(f'{c}=NULL' for c in session_columns)} "
            f'WHERE {where_any_set}')
con.commit()

after = cur.execute(f'SELECT COUNT(*) FROM user WHERE {where_any_set}').fetchone()[0]
print('Users with session state after clear:', after)
con.close()
print('Done. Safe to commit firebed.db now.')
