"""
Migration: add inventory_item_id to order_line_items.
Run from vbs-os directory (server must be stopped):
    python migrate_order_inventory_linkage.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("PRAGMA table_info(order_line_items)")
existing = {r[1] for r in cur.fetchall()}

if "inventory_item_id" not in existing:
    cur.execute("ALTER TABLE order_line_items ADD COLUMN inventory_item_id INTEGER REFERENCES inventory_items(id)")
    print("+ Added inventory_item_id to order_line_items")
else:
    print("~ inventory_item_id already exists")

conn.commit()
conn.close()
print("Done.")
