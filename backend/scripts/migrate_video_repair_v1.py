"""Explicit, idempotent migration for Qianchuan video-repair sidecar tables."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json

from app.database import engine
from app.services.video_repair.migration import migrate_video_repair


def main() -> None:
    parser = argparse.ArgumentParser(description="创建千川审核失败视频修复旁路表")
    parser.add_argument(
        "--postgres-backup-confirmed", action="store_true",
        help="已独立备份 PostgreSQL 并确认可回滚",
    )
    args = parser.parse_args()
    result = migrate_video_repair(
        engine, postgres_backup_confirmed=args.postgres_backup_confirmed)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
