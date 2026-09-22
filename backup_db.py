#!/usr/bin/env python3
"""Create a timestamped, consistent backup of the local finance database."""

import os
import sqlite3
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("FINANCE_DB_FILE", PROJECT_DIR / "finance.db"))
BACKUP_DIR = PROJECT_DIR / "backups"


def main():
    if not DATABASE.exists():
        raise SystemExit(f"Database not found: {DATABASE}")

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"finance-{timestamp}.db"

    source = sqlite3.connect(DATABASE)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        destination.close()
        source.close()

    if result != "ok":
        backup_path.unlink(missing_ok=True)
        raise SystemExit(f"Backup integrity check failed: {result}")

    print(f"Backup created: {backup_path}")
    print("Integrity check: ok")


if __name__ == "__main__":
    main()
