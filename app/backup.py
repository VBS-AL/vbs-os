"""
Hourly SQLite backup → Supabase Storage
Runs Mon–Fri, 7am–4pm EST via APScheduler (started in main.py lifespan).

Required environment variables:
  SUPABASE_URL          — e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  — service_role key (not anon key)
  DB_PATH               — path to the SQLite file (default: vbs.db)
  BACKUP_BUCKET         — Supabase storage bucket name (default: vbs-backups)

Retention: keeps the 72 most recent backups (~3 days of hourly coverage).
"""

import os
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH       = os.getenv("DB_PATH", "vbs.db")
SUPABASE_URL  = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "")
BUCKET        = os.getenv("BACKUP_BUCKET", "vbs-backups")
KEEP_BACKUPS  = 72   # 3 days × 24 hours — actually 3 days of business-hour backups


def _safe_sqlite_copy(src_path: str, dst_path: str) -> None:
    """Use SQLite's own backup API for a consistent snapshot (safe under live writes)."""
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def run_backup() -> None:
    """Create a timestamped backup and upload it to Supabase Storage."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Backup skipped — SUPABASE_URL or SUPABASE_SERVICE_KEY not set.")
        return

    if not os.path.exists(DB_PATH):
        logger.warning(f"Backup skipped — DB not found at {DB_PATH}")
        return

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path    = f"/tmp/vbs_backup_{timestamp}.db"
    remote_name = f"vbs_{timestamp}.db"

    try:
        # 1. Safe copy
        _safe_sqlite_copy(DB_PATH, tmp_path)
        logger.info(f"SQLite snapshot created: {tmp_path}")

        # 2. Upload to Supabase Storage
        from supabase import create_client
        client = create_client(SUPABASE_URL, SUPABASE_KEY)

        with open(tmp_path, "rb") as f:
            data = f.read()

        client.storage.from_(BUCKET).upload(
            path=remote_name,
            file=data,
            file_options={"content-type": "application/octet-stream"},
        )
        logger.info(f"Backup uploaded to Supabase: {BUCKET}/{remote_name}")

        # 3. Prune old backups — keep only the most recent KEEP_BACKUPS files
        _prune_old_backups(client)

    except Exception as e:
        logger.error(f"Backup failed: {e}", exc_info=True)

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _prune_old_backups(client) -> None:
    """Delete oldest backups beyond the retention limit."""
    try:
        files = client.storage.from_(BUCKET).list()
        # Sort oldest-first by name (names are timestamps so lexicographic works)
        files_sorted = sorted(files, key=lambda f: f.get("name", ""))
        excess = len(files_sorted) - KEEP_BACKUPS
        if excess > 0:
            to_delete = [f["name"] for f in files_sorted[:excess]]
            client.storage.from_(BUCKET).remove(to_delete)
            logger.info(f"Pruned {excess} old backup(s): {to_delete}")
    except Exception as e:
        logger.warning(f"Backup pruning failed (non-fatal): {e}")
