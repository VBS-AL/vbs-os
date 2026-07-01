import sqlite3, os

db_path = os.path.join(os.path.dirname(__file__), "vbs.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("PRAGMA table_info(customers)")
cols = [r[1] for r in cur.fetchall()]
print("Existing columns:", cols)

for col, typedef in [("address_line1","TEXT"), ("city","TEXT"), ("state","VARCHAR(50)"), ("zip_code","VARCHAR(20)")]:
    if col not in cols:
        cur.execute(f"ALTER TABLE customers ADD COLUMN {col} {typedef}")
        print(f"Added: {col}")
    else:
        print(f"Already exists: {col}")

cur.execute("UPDATE customers SET address_line1 = address WHERE address IS NOT NULL AND address_line1 IS NULL")
conn.commit()
conn.close()
print("Migration complete.")
