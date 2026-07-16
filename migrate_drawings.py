"""
Migration: extend drawing_records with 4 new columns and migrate legacy
order.drawing_file values to DrawingRecord rows.

Run once from the vbs-os directory:
    py migrate_drawings.py
"""
import sqlite3
import shutil
import os
from datetime import datetime

DB_PATH = "vbs.db"
BACKUP_PATH = "vbs.db.bak"


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} not found. Run from the vbs-os directory.")
        return

    # Back up the database first
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"[BAK] {DB_PATH} -> {BACKUP_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ── Step 1: Add new columns to drawing_records ────────────────────────
    new_cols = [
        ("display_name",   "TEXT"),
        ("revision",       "TEXT"),
        ("uploaded_by_id", "INTEGER"),
        ("stage_context",  "TEXT"),
    ]
    for col, col_type in new_cols:
        if column_exists(cur, "drawing_records", col):
            print(f"[SKIP] drawing_records.{col} already exists")
        else:
            cur.execute(f"ALTER TABLE drawing_records ADD COLUMN {col} {col_type}")
            print(f"[ADD]  drawing_records.{col} ({col_type})")

    conn.commit()

    # ── Step 2: Migrate legacy order.drawing_file values ─────────────────
    # Only runs if the orders table has drawing_file column
    if not column_exists(cur, "orders", "drawing_file"):
        print("[SKIP] orders.drawing_file column not present — no legacy migration needed")
        conn.close()
        print("Done.")
        return

    cur.execute("""
        SELECT o.id, o.drawing_file
        FROM orders o
        WHERE o.drawing_file IS NOT NULL
          AND o.drawing_file != ''
    """)
    legacy_rows = cur.fetchall()
    migrated = 0
    skipped = 0

    for order_id, drawing_file in legacy_rows:
        # Check if a DrawingRecord already exists for this order with this file_reference
        cur.execute("""
            SELECT id FROM drawing_records
            WHERE order_id = ? AND file_reference = ?
        """, (order_id, drawing_file))
        if cur.fetchone():
            skipped += 1
            continue

        now_str = datetime.utcnow().isoformat()
        cur.execute("""
            INSERT INTO drawing_records
                (order_id, drawing_type, file_reference, status,
                 display_name, stage_context, created_at)
            VALUES
                (?, 'drawing', ?, 'pending', ?, 'drawings', ?)
        """, (order_id, drawing_file, drawing_file, now_str))
        migrated += 1

    conn.commit()
    conn.close()

    print(f"[MIG]  Migrated {migrated} legacy drawing_file rows to drawing_records")
    if skipped:
        print(f"[SKIP] {skipped} already had a matching DrawingRecord")
    print("Done.")


if __name__ == "__main__":
    main()
