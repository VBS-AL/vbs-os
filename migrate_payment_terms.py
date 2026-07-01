"""Add payment_terms to customers table."""
import sqlite3, os

DB = os.path.join(os.path.dirname(__file__), "vbs.db")
conn = sqlite3.connect(DB)
cur = conn.cursor()

cur.execute("PRAGMA table_info(customers)")
cols = [r[1] for r in cur.fetchall()]

if "payment_terms" not in cols:
    cur.execute("ALTER TABLE customers ADD COLUMN payment_terms INTEGER DEFAULT 0")
    print("Added payment_terms column.")
else:
    print("payment_terms already exists — skipping.")

conn.commit()
conn.close()
print("Done.")
