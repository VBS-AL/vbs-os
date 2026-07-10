"""
Migration: update inventory_items category values to new physical area names.
Run from vbs-os directory (server must be stopped):
    python migrate_inventory_categories.py

Old → New mapping:
    raw_material → structural
    consumable   → consumables
    hardware     → hardware  (unchanged)
    other        → consumables
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

mappings = [
    ("raw_material", "structural"),
    ("consumable",   "consumables"),
    ("other",        "consumables"),
    # hardware stays as hardware
]

for old, new in mappings:
    cur.execute(
        "UPDATE inventory_items SET category = ? WHERE category = ?",
        (new, old)
    )
    print(f"  {old} → {new}: {cur.rowcount} rows updated")

conn.commit()
conn.close()
print("Done.")
