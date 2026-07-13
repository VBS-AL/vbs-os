"""
Migration: add internal_notes column to quote_line_items and order_line_items.

Run once (server stopped):
  py migrate_line_item_notes.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")
conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

for table in ("quote_line_items", "order_line_items"):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    if "internal_notes" not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN internal_notes TEXT")
        print(f"  ✓ Added internal_notes to {table}")
    else:
        print(f"  — internal_notes already exists in {table}")

conn.commit()
conn.close()
print("Done.")
