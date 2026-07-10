"""
Phase 2 Migration — Timer / Clock System
Adds: work_sessions table, default_billing_dept to users,
      stage_id to labor_entries
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "vbs.db")

def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())

def table_exists(cursor, table):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None

def run():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    # ── 1. work_sessions table ──────────────────────────────────────────────
    if not table_exists(cur, "work_sessions"):
        cur.execute("""
            CREATE TABLE work_sessions (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id              INTEGER NOT NULL REFERENCES orders(id),
                stage_id              INTEGER NOT NULL REFERENCES production_stages(id),
                employee_id           INTEGER NOT NULL REFERENCES users(id),
                billing_dept          TEXT    NOT NULL,
                billing_rate          REAL    NOT NULL,
                started_at            DATETIME NOT NULL,
                paused_at             DATETIME,
                ended_at              DATETIME,
                total_paused_minutes  REAL    NOT NULL DEFAULT 0.0,
                status                TEXT    NOT NULL DEFAULT 'active',
                pause_reason          TEXT,
                is_overtime           INTEGER NOT NULL DEFAULT 0,
                duration_minutes      REAL,
                labor_entry_id        INTEGER REFERENCES labor_entries(id),
                notes                 TEXT,
                created_at            DATETIME DEFAULT (datetime('now'))
            )
        """)
        print("✓ Created work_sessions table")
    else:
        print("  work_sessions table already exists — skipping")

    # ── 2. default_billing_dept on users ────────────────────────────────────
    if not column_exists(cur, "users", "default_billing_dept"):
        cur.execute("ALTER TABLE users ADD COLUMN default_billing_dept TEXT")
        print("✓ Added default_billing_dept to users")
    else:
        print("  users.default_billing_dept already exists — skipping")

    # ── 3. stage_id on labor_entries ────────────────────────────────────────
    if not column_exists(cur, "labor_entries", "stage_id"):
        cur.execute("ALTER TABLE labor_entries ADD COLUMN stage_id INTEGER REFERENCES production_stages(id)")
        print("✓ Added stage_id to labor_entries")
    else:
        print("  labor_entries.stage_id already exists — skipping")

    conn.commit()
    conn.close()
    print("\nPhase 2 migration complete.")

if __name__ == "__main__":
    run()
