"""
clear_test_data.py
------------------
Wipes all transactional data for a clean beta test.

KEEPS:  users, customers (+ contacts), inventory items, app_settings
CLEARS: orders, quotes, invoices, payments, labor entries,
        work sessions, production stages, QA records, drawing records,
        quote revisions, scrap records, purchase orders, inventory adjustments

Run from the vbs-os folder (stop uvicorn first):
    py clear_test_data.py
"""

import sys

def clear():
    from app.database import SessionLocal
    import app.models  # noqa — registers all models

    from app.models.invoice import Payment, Invoice
    from app.models.work_session import WorkSession
    from app.models.labor import LaborEntry
    from app.models.production import ProductionStage, QARecord, DrawingRecord
    from app.models.order import OrderLineItem, Order
    from app.models.quote import QuoteLineItem, QuoteRevision, Quote
    from app.models.inventory import InventoryAdjustment, POLineItem, PurchaseOrder, OutsideService
    from app.models.scrap import ScrapRecord, RetailScrapItem

    db = SessionLocal()
    try:
        steps = [
            ("Payments",                Payment),
            ("Invoices",                Invoice),
            ("Work Sessions",           WorkSession),
            ("Labor Entries",           LaborEntry),
            ("QA Records",              QARecord),
            ("Drawing Records",         DrawingRecord),
            ("Production Stages",       ProductionStage),
            ("Order Line Items",        OrderLineItem),
            ("Orders",                  Order),
            ("Quote Line Items",        QuoteLineItem),
            ("Quote Revisions",         QuoteRevision),
            ("Quotes",                  Quote),
            ("Scrap Records",           ScrapRecord),
            ("Retail Scrap Items",      RetailScrapItem),
            ("PO Line Items",           POLineItem),
            ("Purchase Orders",         PurchaseOrder),
            ("Outside Services",        OutsideService),
            ("Inventory Adjustments",   InventoryAdjustment),
        ]

        total = 0
        for label, Model in steps:
            count = db.query(Model).delete(synchronize_session=False)
            print(f"  ✓ {label}: {count} deleted")
            total += count

        db.commit()
        print(f"\nDone — {total} rows removed.")
        print("Kept: users, customers, contacts, inventory items, app_settings\n")

    except Exception as e:
        db.rollback()
        print(f"\n❌ Error: {e}")
        print("No changes were saved.")
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    confirm = input("This will permanently delete all test data. Type YES to continue: ")
    if confirm.strip() == "YES":
        clear()
    else:
        print("Cancelled.")
