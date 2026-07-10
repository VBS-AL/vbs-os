"""
Migration: add estimated_labor_hours and estimated_labor_dept to order_line_items
Run from Windows terminal (server stopped):
  py migrate_order_labor_estimates.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("PRAGMA table_info(order_line_items)")
cols = [r[1] for r in cur.fetchall()]

if "estimated_labor_hours" not in cols:
    cur.execute("ALTER TABLE order_line_items ADD COLUMN estimated_labor_hours REAL")
    print("✓ Added estimated_labor_hours")
else:
    print("✓ estimated_labor_hours already exists")

if "estimated_labor_dept" not in cols:
    cur.execute("ALTER TABLE order_line_items ADD COLUMN estimated_labor_dept TEXT")
    print("✓ Added estimated_labor_dept")
else:
    print("✓ estimated_labor_dept already exists")

conn.commit()
conn.close()
print("Done.")
