"""
reset_beta.py — Clear test data while preserving inventory, customers, and team.

Clears (in FK-safe order):
  work_sessions, payments, invoices, packing_lists, drawing_records,
  qa_records, labor_entries, scrap_records, production_stages,
  order_line_items, orders, quote_revisions, quote_line_items, quotes

Keeps:
  users, customers, contacts, inventory_items, settings, retail_scrap_items

Run from the vbs-os directory:
    py reset_beta.py
"""

import sys
from sqlalchemy import create_engine, text

DB_PATH = "vbs.db"

TABLES = [
    # children of orders / invoices / production_stages first
    "work_sessions",
    "payments",
    "invoices",
    "packing_lists",
    "drawing_records",
    "qa_records",
    "labor_entries",
    "scrap_records",
    "production_stages",
    "order_line_items",
    # orders (parent of everything above)
    "orders",
    # quote children, then quotes
    "quote_revisions",
    "quote_line_items",
    "quotes",
]

def main():
    print("=" * 55)
    print("  VBS Beta Reset")
    print("  Keeping: inventory, customers, team")
    print("  Clearing: orders, quotes, production, invoices, fulfillment")
    print("=" * 55)
    confirm = input("\nType YES to continue: ").strip()
    if confirm != "YES":
        print("Aborted.")
        sys.exit(0)

    engine = create_engine(f"sqlite:///{DB_PATH}")

    with engine.begin() as conn:
        # Disable FK enforcement during bulk delete (SQLite default is off anyway)
        conn.execute(text("PRAGMA foreign_keys = OFF"))

        for table in TABLES:
            result = conn.execute(text(f"DELETE FROM {table}"))
            print(f"  cleared {table:<25} ({result.rowcount} rows)")

        # Reset auto-increment counters for cleared tables
        for table in TABLES:
            conn.execute(
                text("DELETE FROM sqlite_sequence WHERE name = :t"),
                {"t": table},
            )

        conn.execute(text("PRAGMA foreign_keys = ON"))

    print("\nDone. Inventory, customers, and team are untouched.")
    print("Restart the server and you're ready for a clean run.")


if __name__ == "__main__":
    main()
