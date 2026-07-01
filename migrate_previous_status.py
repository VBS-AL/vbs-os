"""Migration: add previous_status to orders"""
import sqlite3

conn = sqlite3.connect("vbs.db")
cur = conn.cursor()

cols = [r[1] for r in cur.execute("PRAGMA table_info(orders)").fetchall()]
if "previous_status" not in cols:
    cur.execute("ALTER TABLE orders ADD COLUMN previous_status TEXT")
    print("Added orders.previous_status")
else:
    print("already exists")

conn.commit()
conn.close()
print("Done.")
