# Van Buren Steel — Operating System

FastAPI + SQLite + HTMX web application.

## Quick Start (local dev)

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Generate DB schema
alembic upgrade head

# Run
uvicorn app.main:app --reload
```

Open http://localhost:8000 — default login: `admin@vanburen.local` / `vbs-change-me`

## Project Structure

```
app/
  main.py          # FastAPI app, dashboard route
  database.py      # SQLAlchemy engine + session
  auth.py          # JWT auth, role checkers
  models/          # All SQLAlchemy models (Ch. 1 data architecture)
    user.py        # Users + roles (7 roles from Ch. 3)
    customer.py    # Customers + Contacts
    quote.py       # Quotes + QuoteLineItems
    order.py       # Orders + OrderLineItems
    production.py  # ProductionStages, QARecords, DrawingRecords
    labor.py       # LaborEntries (3 billing depts, rates baked in)
    inventory.py   # InventoryItems, PurchaseOrders, OutsideServices
    invoice.py     # Invoices + Payments
    scrap.py       # ScrapRecords + RetailScrapItems
  routers/         # Route handlers (add one per feature area)
  templates/       # Jinja2 HTML templates
    base.html      # Nav, Tailwind, HTMX loaded here
    auth/login.html
    dashboard/index.html
alembic/           # DB migrations
requirements.txt
render.yaml        # Render.com deploy config ($7/mo starter)
```

## Roles (Chapter 3)
`owner` · `ops_manager` · `shop_foreman` · `estimator` · `receiving_lead` · `driver` · `counter_staff`

## Billing Rates (Chapter 1)
- General Labor: $80/hr
- Steel Fabrication: $100/hr
- Aluminum & Structural: $120/hr

## Numbering Convention
- Orders: `VBS-O-YY-#####`
- Quotes: `VBS-Q-YY-#####`
- POs: `VBS-P-YY-#####`
- Customers: `VBS-C-######`

## Deploy to Render.com
1. Push repo to GitHub
2. New Web Service → connect repo
3. Render auto-detects `render.yaml` (Starter plan = $7/mo)
4. Set `SECRET_KEY` env var in Render dashboard
5. Auto-deploys on every `git push`

## Environment Variables
| Key | Description |
|-----|-------------|
| `SECRET_KEY` | JWT signing key (generate random, keep secret) |
| `DATABASE_URL` | SQLite path (default: `sqlite:///./vbs.db`) |
| `ENVIRONMENT` | `development` or `production` |

## Weekend Build Plan
This weekend: Orders CRUD, Customers CRUD, Labor Entry
Next weekend: Inventory/POs, Invoicing, Reports, Dashboards
Go-live: July 13
