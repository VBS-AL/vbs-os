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
    "markup.steel_ibeam":          