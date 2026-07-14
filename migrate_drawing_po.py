"""
Migration: add drawing_file and customer_po columns to quotes and orders tables.
Run once: py migrate_drawing_po.py
"""
import sqlite3
import os

DB_PATH = "vbs.db"
DRAWINGS_DIR = os.path.join("app", "static", "drawings")


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} not found. Run from the vbs-os directory.")
        return

    # Ensure drawings directory exists
    os.makedirs(DRAWINGS_DIR, exist_ok=True)
    print(f"[OK] drawings dir: {DRAWINGS_DIR}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    changes = [
        ("quotes",  "drawing_file", "TEXT"),
        ("quotes",  "customer_po",  "TEXT"),
        ("orders",  "drawing_file", "TEXT"),
        ("orders",  "customer_po",  "TEXT"),
    ]

    for table, col, col_type in changes:
        if column_exists(cur, table, col):
            print(f"[SKIP] {table}.{col} already exists")
        else:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            print(f"[ADD]  {table}.{col} ({col_type})")

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
