import sqlite3, os

db_path = os.path.join(os.path.dirname(__file__), "vbs.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Quote table columns
cur.execute("PRAGMA table_info(quotes)")
quote_cols = [r[1] for r in cur.fetchall()]
print("Quote columns:", quote_cols)

for col, typedef in [
    ("priority", "VARCHAR(20) DEFAULT 'standard'"),
    ("paint_spec", "VARCHAR(100)"),
    ("drawings_required", "BOOLEAN DEFAULT 0"),
    ("notes", "TEXT"),
    ("sent_at", "DATETIME"),
]:
    if col not in quote_cols:
        cur.execute(f"ALTER TABLE quotes ADD COLUMN {col} {typedef}")
        print(f"Added to quotes: {col}")

# QuoteLineItem columns
cur.execute("PRAGMA table_info(quote_line_items)")
li_cols = [r[1] for r in cur.fetchall()]
print("QuoteLineItem columns:", li_cols)

for col, typedef in [
    ("paint_spec", "VARCHAR(100)"),
]:
    if col not in li_cols:
        cur.execute(f"ALTER TABLE quote_line_items ADD COLUMN {col} {typedef}")
        print(f"Added to quote_line_items: {col}")

conn.commit()
conn.close()
print("Migration complete.")
