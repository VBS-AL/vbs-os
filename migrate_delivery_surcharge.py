"""
Delivery Surcharge Migration
Adds: is_delivery_surcharge to order_line_items and quote_line_items
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")

def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())

def run():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    if not column_exists(cur, "order_line_items", "is_delivery_surcharge"):
        cur.execute("ALTER TABLE order_line_items ADD COLUMN is_delivery_surcharge INTEGER NOT NULL DEFAULT 0")
        print("✓ Added is_delivery_surcharge to order_line_items")
    else:
        print("  order_line_items.is_delivery_surcharge already exists — skipping")

    if not column_exists(cur, "quote_line_items", "is_delivery_surcharge"):
        cur.execute("ALTER TABLE quote_line_items ADD COLUMN is_delivery_surcharge INTEGER NOT NULL DEFAULT 0")
        print("✓ Added is_delivery_surcharge to quote_line_items")
    else:
        print("  quote_line_items.is_delivery_surcharge already exists — skipping")

    conn.commit()
    conn.close()
    print("\nDelivery surcharge migration complete.")

if __name__ == "__main__":
    run()
