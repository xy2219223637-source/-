"""Explicit, idempotent migration for Qianchuan delivery sidecar tables."""
from __future__ import annotations

import argparse
import json

from app.database import engine
from app.services.qianchuan_plan.migration import migrate_qianchuan_delivery


def main() -> None:
    parser = argparse.ArgumentParser(description="创建千川全域计划旁路表")
    parser.add_argument("--postgres-backup-confirmed", action="store_true",
                        help="已独立备份 PostgreSQL 并确认可回滚")
    args = parser.parse_args()
    result = migrate_qianchuan_delivery(
        engine, postgres_backup_confirmed=args.postgres_backup_confirmed)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

