"""Migration: add revision column to quotes + create quote_revisions table"""
import sqlite3, sys

db_path = "vbs.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Add revision column if not exists
cols = [r[1] for r in cur.execute("PRAGMA table_info(quotes)").fetchall()]
if "revision" not in cols:
    cur.execute("ALTER TABLE quotes ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    print("Added quotes.revision")
else:
    print("quotes.revision already exists")

# Create quote_revisions table
cur.execute("""
CREATE TABLE IF NOT EXISTS quote_revisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id        INTEGER NOT NULL REFERENCES quotes(id),
    revision_number INTEGER NOT NULL,
    snapshot        TEXT NOT NULL,
    edited_by_id    INTEGER REFERENCES users(id),
    change_note     TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
# Add paint_override to quote_line_items if missing
li_cols = [r[1] for r in cur.execute("PRAGMA table_info(quote_line_items)").fetchall()]
if "paint_override" not in li_cols:
    cur.execute("ALTER TABLE quote_line_items ADD COLUMN paint_override TEXT")
    print("Added quote_line_items.paint_override")
else:
    print("quote_line_items.paint_override already exists")

print("quote_revisions table ready")

conn.commit()
conn.close()
print("Done.")
