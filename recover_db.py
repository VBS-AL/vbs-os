"""
DB recovery v3 — proper connection cleanup + immutable URI.
Run: py recover_db.py
"""
import sqlite3
import os
import shutil

OLD_DB = "vbs.db"
NEW_DB = "vbs_recovered.db"
BACKUP = "vbs_backup_malformed.db"

if not os.path.exists(BACKUP):
    shutil.copy2(OLD_DB, BACKUP)
    print(f"Backed up malformed DB to {BACKUP}")
else:
    print(f"Malformed backup already exists: {BACKUP}")

def remove_if_exists(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except PermissionError:
        pass

recovered = 0
skipped   = 0
success   = False

# ── Attempt 1: iterdump via immutable URI ─────────────────────────────────
print("\n[Attempt 1] Opening with immutable URI flag...")
old = new = None
try:
    old = sqlite3.connect(f"file:{OLD_DB}?immutable=1", uri=True)
    new = sqlite3.connect(NEW_DB)
    for line in old.iterdump():
        try:
            new.execute(line)
            recovered += 1
        except Exception as e:
            skipped += 1
            if skipped <= 5:
                print(f"  Skip: {e!s:.80}")
    new.commit()
    success = True
    print(f"Attempt 1 OK: {recovered} statements, {skipped} skipped.")
except sqlite3.DatabaseError as e:
    print(f"Attempt 1 failed: {e}")
finally:
    if old: old.close()
    if new: new.close()

# ── Attempt 2: table-by-table ─────────────────────────────────────────────
if not success:
    print("\n[Attempt 2] Table-by-table raw extraction...")
    remove_if_exists(NEW_DB)
    old = new = None
    try:
        old = sqlite3.connect(f"file:{OLD_DB}?immutable=1", uri=True)
        new = sqlite3.connect(NEW_DB)

        for (sql,) in old.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall():
            try:
                new.execute(sql)
            except Exception as e:
                print(f"  Schema skip: {e!s:.60}")
        new.commit()

        for (tbl,) in old.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall():
            try:
                rows = old.execute(f'SELECT * FROM "{tbl}"').fetchall()
                if rows:
                    placeholders = ",".join(["?"] * len(rows[0]))
                    new.executemany(
                        f'INSERT OR IGNORE INTO "{tbl}" VALUES ({placeholders})', rows
                    )
                    new.commit()
                    print(f"  {tbl}: {len(rows)} rows")
                    recovered += len(rows)
            except Exception as e:
                print(f"  {tbl}: FAILED — {e!s:.80}")
                skipped += 1

        success = True
        print(f"Attempt 2 done: {recovered} rows, {skipped} tables failed.")
    except sqlite3.DatabaseError as e:
        print(f"Attempt 2 also failed: {e}")
    finally:
        if old: old.close()
        if new: new.close()

# ── Fallback: fresh empty DB ──────────────────────────────────────────────
if not success:
    print("\n[Fallback] Both attempts failed — creating fresh empty database.")
    print("Server create_all() will rebuild schema on next start.")
    remove_if_exists(NEW_DB)
    sqlite3.connect(NEW_DB).close()

# ── Packing list migration ────────────────────────────────────────────────
print("\n[Migration] Applying packing list schema...")
conn = sqlite3.connect(NEW_DB)
cur  = conn.cursor()

cur.execute("PRAGMA table_info(inventory_items)")
cols = [r[1] for r in cur.fetchall()]
if cols and "weight_per_unit" not in cols:
    cur.execute("ALTER TABLE inventory_items ADD COLUMN weight_per_unit REAL")
    print("  Added weight_per_unit to inventory_items")
elif not cols:
    print("  inventory_items not present yet — server startup will create it")
else:
    print("  weight_per_unit already present")

cur.execute("""
CREATE TABLE IF NOT EXISTS packing_lists (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER NOT NULL REFERENCES orders(id),
    shipped_via       TEXT,
    shipped_via_other TEXT,
    date_shipped      DATE,
    ship_to           TEXT,
    sold_to           TEXT,
    contact_name      TEXT,
    contact_phone     TEXT,
    cartons           INTEGER,
    total_weight      REAL,
    order_complete    INTEGER DEFAULT 0,
    balance_to_follow INTEGER DEFAULT 0,
    packed_by         TEXT,
    checked_by        TEXT,
    notes             TEXT,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by_id     INTEGER REFERENCES users(id)
)""")
print("  packing_lists table ready")
conn.commit()
conn.close()

# ── Swap in the new DB ────────────────────────────────────────────────────
os.replace(NEW_DB, OLD_DB)
print(f"\nDone. vbs.db replaced. Malformed copy: {BACKUP}")
if not success:
    print("\n⚠  Fresh DB — re-seed needed.")
    print("   Default login after restart: admin@vanburen.local / vbs-change-me")
