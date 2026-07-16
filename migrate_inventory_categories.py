"""
Migration: remap old InventoryCategory values → new granular category codes.

Old → New:
  plate      → steel_plate
  structural → steel_bar
  beam       → steel_ibeam

Also seeds any missing markup.* keys and removes stale old markup keys.

Run once after pulling this update (server stopped):
  py migrate_inventory_categories.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")

REMAP = {
    "plate":      "steel_plate",
    "structural": "steel_bar",
    "beam":       "steel_ibeam",
}

# New markup keys to seed (INSERT OR IGNORE preserves any values already set)
NEW_MARKUP_DEFAULTS = {
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
    "markup.aluminum_structural":  "0",
    "markup.aluminum_sheet":       "0",
    "markup.stainless_structural": "0",
    "markup.stainless_sheet":      "0",
    "markup.misc":                 "0",
    "markup.bumper_posts":         "0",
    "markup.consumables":          "0",
    "markup.hardware":             "0",
    "markup.retail":               "200",
}

conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

# ── 1. Remap inventory_items.category ────────────────────────────────────────
print("Remapping inventory categories...")
total_remapped = 0
for old, new in REMAP.items():
    cur.execute("SELECT COUNT(*) FROM inventory_items WHERE category = ?", (old,))
    count = cur.fetchone()[0]
    if count:
        cur.execute("UPDATE inventory_items SET category = ? WHERE category = ?", (new, old))
        print(f"  ✓ {count} item(s): '{old}' → '{new}'")
        total_remapped += count
    else:
        print(f"  — No items with category '{old}'")

if total_remapped == 0:
    print("  Nothing to remap.")

# ── 2. Seed new markup keys ───────────────────────────────────────────────────
print("\nSeeding markup keys...")
seeded = 0
for key, val in NEW_MARKUP_DEFAULTS.items():
    cur.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", (key, val))
    if cur.rowcount:
        seeded += 1
print(f"  ✓ {seeded} new key(s) added (existing values preserved)")

# ── 3. Remove stale old markup keys ──────────────────────────────────────────
stale_keys = ["markup.plate", "markup.structural", "markup.beam"]
removed = 0
for key in stale_keys:
    cur.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    if cur.rowcount:
        print(f"  ✓ Removed stale key: {key}")
        removed += 1

conn.commit()
conn.close()

print(f"\n✓ Done — {total_remapped} item(s) remapped, {seeded} markup key(s) seeded, {removed} stale key(s) cleaned up.")
print("  Set your markup rates at /inventory/settings after restarting the server.")
