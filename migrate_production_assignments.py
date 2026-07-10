"""
Migration: add assigned_to_id to production_stages (if not already present)
Run once: py migrate_production_assignments.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Check existing columns
cur.execute("PRAGMA table_info(production_stages)")
cols = [row[1] for row in cur.fetchall()]
print("Existing columns:", cols)

added = []

if "assigned_to_id" not in cols:
    cur.execute("ALTER TABLE production_stages ADD COLUMN assigned_to_id INTEGER REFERENCES users(id)")
    added.append("assigned_to_id")

if "started_at" not in cols:
    cur.execute("ALTER TABLE production_stages ADD COLUMN started_at DATETIME")
    added.append("started_at")

if "completed_at" not in cols:
    cur.execute("ALTER TABLE production_stages ADD COLUMN completed_at DATETIME")
    added.append("completed_at")

if "notes" not in cols:
    cur.execute("ALTER TABLE production_stages ADD COLUMN notes TEXT")
    added.append("notes")

conn.commit()
conn.close()

if added:
    print(f"✓ Added columns: {', '.join(added)}")
else:
    print("✓ All columns already present — nothing to do.")
