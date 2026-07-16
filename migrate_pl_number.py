"""Add pl_number column to packing_lists. Run: py migrate_pl_number.py"""
import sqlite3, shutil, os

DB = "vbs.db"
shutil.copy2(DB, DB + ".bak")

conn = sqlite3.connect(DB)
cur  = conn.cursor()
cur.execute("PRAGMA table_info(packing_lists)")
cols = [r[1] for r in cur.fetchall()]
if "pl_number" not in cols:
    cur.execute("ALTER TABLE packing_lists ADD COLUMN pl_number TEXT")
    print("Added pl_number to packing_lists")
else:
    print("pl_number already exists")
conn.commit()
conn.close()
print("Done.")
