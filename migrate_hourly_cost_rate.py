"""Migration: add hourly_cost_rate column to users table."""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")
conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

cur.execute("PRAGMA table_info(users)")
cols = [row[1] for row in cur.fetchall()]

if "hourly_cost_rate" not in cols:
    cur.execute("ALTER TABLE users ADD COLUMN hourly_cost_rate REAL")
    conn.commit()
    print("✓ Added hourly_cost_rate to users")
else:
    print("— hourly_cost_rate already exists, skipping")

conn.close()
