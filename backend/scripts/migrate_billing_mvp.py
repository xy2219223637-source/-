"""Run from backend: python -m scripts.migrate_billing_mvp --backup-confirmed."""
import argparse
import json

from app.database import engine
from app.services.billing.migration import migrate_billing


def main():
    parser = argparse.ArgumentParser(description="显式创建积分表，不发积分、不修改历史费用")
    parser.add_argument("--backup-confirmed", action="store_true", help="确认已完成独立数据库备份")
    args = parser.parse_args()
    print(json.dumps(migrate_billing(engine, backup_confirmed=args.backup_confirmed), ensure_ascii=False))


if __name__ == "__main__":
    main()
