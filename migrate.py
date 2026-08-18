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
    # maintenance_tasks
    ("maintenance_tasks",   "assigned_user_ids",        "ALTER TABLE maintenance_tasks ADD COLUMN assigned_user_ids TEXT"),
    # production_stages — piece count for mid-job weld checks
    ("production_stages",   "pieces_completed",         "ALTER TABLE production_stages ADD COLUMN pieces_completed INTEGER DEFAULT 0"),
    # packing_lists — delivery proof photo
    ("packing_lists",       "delivery_photo_path",      "ALTER TABLE packing_lists ADD COLUMN delivery_photo_path VARCHAR"),
    # customers — customer number + AR/accounting contact
    ("customers",           "customer_number",           "ALTER TABLE customers ADD COLUMN customer_number VARCHAR"),
    ("customers",           "ar_contact_name",           "ALTER TABLE customers ADD COLUMN ar_contact_name VARCHAR"),
    ("customers",           "ar_contact_title",          "ALTER TABLE customers ADD COLUMN ar_contact_title VARCHAR"),
    ("customers",           "ar_contact_phone",          "ALTER TABLE customers ADD COLUMN ar_contact_phone VARCHAR"),
    ("customers",           "ar_contact_email",          "ALTER TABLE customers ADD COLUMN ar_contact_email VARCHAR"),
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

# Backfill customer_number for existing customers that don't have one yet
cur.execute("SELECT id FROM customers WHERE customer_number IS NULL ORDER BY id ASC")
rows = cur.fetchall()
if rows:
    cur.execute("SELECT customer_number FROM customers WHERE customer_number LIKE 'VBS-C-%' ORDER BY customer_number DESC LIMIT 1")
    last = cur.fetchone()
    next_n = (int(last[0].split("-")[-1]) + 1) if last else 1
    for (cid,) in rows:
        num = f"VBS-C-{next_n:05d}"
        cur.execute("UPDATE customers SET customer_number = ? WHERE id = ?", (num, cid))
        next_n += 1
    con.commit()
    print(f"  backfilled customer_number for {len(rows)} existing customer(s)")

con.close()
print(f"\n{applied} column(s) added. Migration complete — restart the server.")
