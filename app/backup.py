"""
Hourly PostgreSQL backup → Supabase Storage
Runs Mon–Fri, 7am–4pm EST via APScheduler (started in main.py lifespan).

Required environment variables:
  DATABASE_URL          — PostgreSQL connection string
  SUPABASE_URL          — e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  — service_role key (not anon key)
  BACKUP_BUCKET         — Supabase storage bucket name (default: vbs-backups)

Retention: keeps the 72 most recent backups (~3 days of hourly coverage).
"""

import os
import subprocess
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DATABASE_URL  = os.getenv("DATABASE_URL", "")
SUPABASE_URL  = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "")
BUCKET        = os.getenv("BACKUP_BUCKET", "vbs-backups")
KEEP_BACKUPS  = 72   # 3 days of business-hour backups


def run_backup() -> None:
    """Create a pg_dump backup and upload it to Supabase Storage."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Backup skipped — SUPABASE_URL or SUPABASE_SERVICE_KEY not set.")
        return

    if not DATABASE_URL or "postgresql" not in DATABASE_URL:
        logger.warning("Backup skipped — DATABASE_URL not set or not PostgreSQL.")
        return

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path    = f"/tmp/vbs_backup_{timestamp}.sql"
    remote_name = f"vbs_{timestamp}.sql"

    try:
        # 1. pg_dump to a SQL file
        result = subprocess.run(
            ["pg_dump", "--no-owner", "--no-acl", DATABASE_URL],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.error(f"pg_dump failed: {result.stderr.decode()}")
            return

        with open(tmp_path, "wb") as f:
            f.write(result.stdout)
        logger.info(f"pg_dump snapshot created: {tmp_path} ({len(result.stdout)} bytes)")

        # 2. Upload to Supabase Storage
        from supabase import create_client
        client = create_client(SUPABASE_URL, SUPABASE_KEY)

        with open(tmp_path, "rb") as f:
            data = f.read()

        client.storage.from_(BUCKET).upload(
            path=remote_name,
            file=data,
            file_options={"content-type": "application/sql"},
        )
        logger.info(f"Backup uploaded to Supabase: {BUCKET}/{remote_name}")

        # 3. Prune old backups
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
        files_sorted = sorted(files, key=lambda f: f.get("name", ""))
        excess = len(files_sorted) - KEEP_BACKUPS
        if excess > 0:
            to_delete = [f["name"] for f in files_sorted[:excess]]
            client.storage.from_(BUCKET).remove(to_delete)
            logger.info(f"Pruned {excess} old backup(s): {to_delete}")
    except Exception as e:
        logger.warning(f"Backup pruning failed (non-fatal): {e}")
