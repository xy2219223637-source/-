from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import engine
from app.services.video_repair.migration_v3 import migrate_video_repair_v3


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移 AI 成品视频返修 V3 数据模型")
    parser.add_argument("--postgres-backup-confirmed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(migrate_video_repair_v3(
        engine,
        postgres_backup_confirmed=args.postgres_backup_confirmed,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
