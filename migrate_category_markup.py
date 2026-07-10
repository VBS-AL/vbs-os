"""Migration: create app_settings table and seed category markup defaults."""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")

DEFAULTS = {
    # Category markups (sell price = cost × (1 + markup%))
    "markup.plate":        "0",
    "markup.structural":   "0",
    "markup.beam":         "0",
    "markup.consumables":  "0",
    "markup.hardware":     "0",
    # Internal labor cost rates ($/hr — what labor actually costs VBS)
    "labor_cost.general_labor":       "0",
    "labor_cost.steel_fabrication":   "0",
    "labor_cost.aluminum_structural": "0",
}

conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

cur.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        updated_at TEXT
    )
""")

for key, val in DEFAULTS.items():
    cur.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
        (key, val),
    )

conn.commit()
conn.close()
print("✓ app_settings table ready — category markups seeded at 0%")
print("  Configure them at /inventory/settings after starting the server.")
