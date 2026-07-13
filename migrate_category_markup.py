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
    # ── Stainless ──────────────────────────────────────────────────────�