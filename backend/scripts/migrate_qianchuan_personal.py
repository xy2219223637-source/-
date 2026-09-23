"""Run after an operator has backed up PostgreSQL; SQLite backs itself up."""
from __future__ import annotations

import argparse

from app.database import engine, migrate_qianchuan_upload_uniqueness


def main() -> None:
    parser = argparse.ArgumentParser(description="千川上传唯一约束改为个人范围")
    parser.add_argument("--postgres-backup-confirmed", action="store_true",
                        help="已独立备份 PostgreSQL 并确认可回滚")
    args = parser.parse_args()
    migrate_qianchuan_upload_uniqueness(engine,
        postgres_backup_confirmed=args.postgres_backup_confirmed)


if __name__ == "__main__":
    main()
