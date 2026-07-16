"""Migration: create app_settings table and seed category markup defaults."""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")

DEFAULTS = {
    # ── Carbon Steel markups (sell price = cost × (1 + markup%)) ──────────
    "markup.steel_pipe":           "0",
    "markup.steel_rect_tube":      "0",
    "markup.steel_sq_tube":        "0",
    "markup.steel_channel":        "0",
    "markup.steel_bar":            "0",
    "markup.steel_ibeam":          "0",
    "markup.wide_flange":          "0",
    "markup.columns":              "0",
    "markup.steel_plate":          "0",
    "markup.steel_sheet":          "0",
    # ── Aluminum ──────────────────────────────────────────────────────────
    "markup.aluminum_structural":  "0",
    "markup.aluminum_sheet":       "0",
    # ── Stainless ─────────────────────────────────────────────────────────
    "markup.stainless_structural": "0",
    "markup.stainless_sheet":      "0",
    # ── Other ─────────────────────────────────────────────────────────────
    "markup.misc":                 "0",
    "markup.bumper_posts":         "0",
    "markup.consumables":          "0",
    "markup.hardware":             "0",
    "markup.retail":               "200",   # sell = cost × 3 (3× cost)
    # ── Internal labor cost rates ($/hr) ───────────────────────────────────
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
