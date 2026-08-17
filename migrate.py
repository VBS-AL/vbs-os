"""
One-time migration: add new columns to existing tables.
Run with: py migrate.py
Then restart the server normally.
"""
import sqlite3, os, sys

db_path = 'vbs.db'
if not os.path.exists(db_path):
    print(f"ERROR: {db_path} not found — run this from the vbs-os directory.")
    sys.exit(1)

con = sqlite3.connect(db_path)
cur = con.cursor()

migrations = [
    # orders
    ("orders",              "staging_location",         "ALTER TABLE orders ADD COLUMN staging_location VARCHAR"),
    ("orders",              "preferred_delivery_method","ALTER TABLE orders ADD COLUMN preferred_delivery_method VARCHAR"),
    # order_line_items
    ("order_line_items",    "labor_rate_snapshot",      "ALTER TABLE order_line_items ADD COLUMN labor_rate_snapshot FLOAT"),
    ("order_line_items",    "third_party_cost",         "ALTER TABLE order_line_items ADD COLUMN third_party_cost FLOAT"),
    ("order_line_items",    "third_party_markup",       "ALTER TABLE order_line_items ADD COLUMN third_party_markup FLOAT"),
    # quote_line_items
    ("quote_line_items",    "labor_rate_snapshot",      "ALTER TABLE quote_line_items ADD COLUMN labor_rate_snapshot FLOAT"),
    ("quote_line_items",    "internal_notes",           "ALTER TABLE quote_line_items ADD COLUMN internal_notes TEXT"),
]

def existing_cols(table):
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}

applied = 0
for table, col, sql in migrations:
    if col in existing_cols(table):
        print(f"  skip  {table}.{col} (already exists)")
        continue
    try:
        cur.execute(sql)
        print(f"  added {table}.{col}")
        applied += 1
    except sqlite3.OperationalError as e:
        print(f"  ERROR {table}.{col}: {e}")

con.commit()
con.close()
print(f"\n{applied} column(s) added. Migration complete — restart the server.")
