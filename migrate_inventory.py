"""
Migration: update inventory_items schema + create inventory_adjustments table.
Run once from the vbs-os directory:
    python migrate_inventory.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# ── 1. Add new columns to inventory_items ─────────────────────────────────
cur.execute("PRAGMA table_info(inventory_items)")
existing_cols = {row[1] for row in cur.fetchall()}
print(f"Existing inventory_items columns: {existing_cols}")

new_columns = [
    ("sku",               "TEXT"),
    ("name",              "TEXT"),
    ("quantity_on_hand",  "REAL DEFAULT 0"),
    ("reorder_threshold", "REAL"),
    ("cost_per_unit",     "REAL"),
    ("supplier_name",     "TEXT"),
    ("supplier_contact",  "TEXT"),
    ("created_at",        "DATETIME DEFAULT CURRENT_TIMESTAMP"),
]

for col, col_type in new_columns:
    if col not in existing_cols:
        cur.execute(f"ALTER TABLE inventory_items ADD COLUMN {col} {col_type}")
        print(f"  + Added column: {col}")
    else:
        print(f"  ~ Already exists: {col}")

# ── 2. Create inventory_adjustments ───────────────────────────────────────
cur.execute("""
CREATE TABLE IF NOT EXISTS inventory_adjustments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id        INTEGER NOT NULL REFERENCES inventory_items(id),
    delta          REAL NOT NULL,
    reason         TEXT NOT NULL,
    order_id       INTEGER REFERENCES orders(id),
    notes          TEXT,
    recorded_by_id INTEGER REFERENCES users(id),
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
print("inventory_adjustments table ready")

conn.commit()
conn.close()
print("\nMigration complete.")
