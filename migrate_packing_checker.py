"""
Migration: add checker enforcement columns to packing_lists
  - checker_id       INTEGER (FK to users)
  - check_confirmed  INTEGER DEFAULT 0
  - check_confirmed_at DATETIME
"""
import shutil, sqlite3, pathlib, sys

DB   = pathlib.Path("vbs.db")
BAK  = pathlib.Path("vbs_backup_pre_checker.db")

if not DB.exists():
    sys.exit("vbs.db not found — run from the vbs-os directory")

shutil.copy2(DB, BAK)
print(f"Backup saved to {BAK}")

con = sqlite3.connect(DB)
cur = con.cursor()

existing = {row[1] for row in cur.execute("PRAGMA table_info(packing_lists)")}

added = []
for col, defn in [
    ("checker_id",          "INTEGER REFERENCES users(id)"),
    ("check_confirmed",     "INTEGER DEFAULT 0"),
    ("check_confirmed_at",  "DATETIME"),
]:
    if col not in existing:
        cur.execute(f"ALTER TABLE packing_lists ADD COLUMN {col} {defn}")
        added.append(col)
        print(f"  + {col}")
    else:
        print(f"  = {col} already exists")

con.commit()
con.close()
print("Done." if added else "Nothing to do.")
