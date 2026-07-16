"""Migration: add retail_markup column to inventory_items table."""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")

conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

# Check if column already exists
cur.execute("PRAGMA table_info(inventory_items)")
cols = [row[1] for row in cur.fetchall()]

if "retail_markup" not in cols:
    cur.execute("ALTER TABLE inventory_items ADD COLUMN retail_markup REAL DEFAULT 3.0")
    print("✓ Added retail_markup column to inventory_items (default 3.0)")
else:
    print("  retail_markup column already exists — skipping")

conn.commit()
conn.close()
print("✓ Migration complete.")
