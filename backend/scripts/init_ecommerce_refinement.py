"""Create ecommerce refinement tables without altering or copying legacy data."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect

from app.database import Base, engine
from app.models import ecommerce_refinement  # noqa: F401


def init_ecommerce_refinement(bind, *, dry_run: bool = False) -> list[str]:
    tables = [table for table in Base.metadata.sorted_tables
              if table.name.startswith("ecommerce_video_refinement_") or table.name == "ecommerce_video_lineage_segments"]
    with bind.begin() as connection:
        existing = set(inspect(connection).get_table_names())
        required = {"ecommerce_products", "ecommerce_video_assets"}
        if not required.issubset(existing):
            raise RuntimeError("请先初始化电商商品管理和商品素材库")
        pending = [table.name for table in tables if table.name not in existing]
        if not dry_run:
            Base.metadata.create_all(connection, tables=tables)
        return pending


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化电商商品精修数据表, 保留旧商品库和历史精修数据")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    pending = init_ecommerce_refinement(engine, dry_run=args.dry_run)
    print({"dry_run": args.dry_run, "tables": pending})


if __name__ == "__main__":
    main()
