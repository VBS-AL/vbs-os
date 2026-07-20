from app.database import get_db
from sqlalchemy import text

db = next(get_db())

# Unlink inventory references from orders/quotes (keep the line items themselves)
db.execute(text("UPDATE order_line_items SET inventory_item_id = NULL"))
db.execute(text("UPDATE quote_line_items SET inventory_item_id = NULL"))

# Clear inventory
db.execute(text("DELETE FROM inventory_items"))
db.execute(text("DELETE FROM sqlite_sequence WHERE name='inventory_items'"))

db.commit()
print("Inventory cleared. Orders and quotes are untouched.")
