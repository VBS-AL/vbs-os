"""Migration: add preferred_delivery_method to orders and quotes tables."""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")

conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

for table in ("orders", "quotes"):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    if "preferred_delivery_method" not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN preferred_delivery_method TEXT")
        print(f"Added preferred_delivery_method to {table}")
    else:
        print(f"  {table}.preferred_delivery_method already exists — skipping")

conn.commit()
conn.close()
print("Migration complete.")
