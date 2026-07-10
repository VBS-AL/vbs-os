"""
Migration: add inventory_item_id, estimated_labor_hours, estimated_labor_dept to quote_line_items
Run from Windows terminal (server stopped):
  python migrate_quote_inventory_linkage.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("PRAGMA table_info(quote_line_items)")
cols = [r[1] for r in cur.fetchall()]

if "inventory_item_id" not in cols:
    cur.execute("ALTER TABLE quote_line_items ADD COLUMN inventory_item_id INTEGER REFERENCES inventory_items(id)")
    print("✓ Added inventory_item_id")
else:
    print("✓ inventory_item_id already exists")

if "estimated_labor_hours" not in cols:
    cur.execute("ALTER TABLE quote_line_items ADD COLUMN estimated_labor_hours REAL")
    print("✓ Added estimated_labor_hours")
else:
    print("✓ estimated_labor_hours already exists")

if "estimated_labor_dept" not in cols:
    cur.execute("ALTER TABLE quote_line_items ADD COLUMN estimated_labor_dept TEXT")
    print("✓ Added estimated_labor_dept")
else:
    print("✓ estimated_labor_dept already exists")

conn.commit()
conn.close()
print("Done.")
