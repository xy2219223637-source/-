from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from sqlalchemy import Engine, func, inspect, select

from app.database import Base
from app.models import QianchuanReviewFeedback, VideoRepairInput, VideoRepairTask


REPAIR_MODELS = (QianchuanReviewFeedback, VideoRepairInput, VideoRepairTask)
REPAIR_TABLES = tuple(model.__table__ for model in REPAIR_MODELS)
REPAIR_TABLE_NAMES = {table.name for table in REPAIR_TABLES}
PRESERVED_TABLE_NAMES = ("video_assets", "editing_plan_versions", "video_lineage_segments")


def _counts(bind: Engine) -> dict[str, int]:
    existing = set(inspect(bind).get_table_names())
    with bind.connect() as connection:
        return {
            name: connection.execute(
                select(func.count()).select_from(Base.metadata.tables[name])
            ).scalar_one()
            for name in PRESERVED_TABLE_NAMES if name in existing
        }


def _backup_sqlite(bind: Engine) -> Path | None:
    database = bind.url.database
    if not database or database == ":memory:":
        return None
    source = Path(database).resolve()
    if not source.exists():
        return None
    target = source.with_name(
        source.name + f".video-repair-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(source, target)
    return target


def migrate_video_repair(bind: Engine, *, postgres_backup_confirmed: bool = False) -> dict:
    """Create only the repair sidecar tables; never mutate source video records."""
    dialect = bind.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(f"不支持视频修复迁移的数据库类型: {dialect}")
    existing = set(inspect(bind).get_table_names())
    if dialect == "postgresql" and "qianchuan_delivery_plans" not in existing:
        raise RuntimeError("请先执行 migrate_qianchuan_delivery.py，再创建视频修复旁路表")
    missing = REPAIR_TABLE_NAMES - existing
    before = _counts(bind)
    backup: Path | None = None
    if missing:
        if dialect == "postgresql" and not postgres_backup_confirmed:
            raise RuntimeError("创建视频修复旁路表前必须备份 PostgreSQL，并显式确认")
        if dialect == "sqlite":
            backup = _backup_sqlite(bind)
        Base.metadata.create_all(bind=bind, tables=list(REPAIR_TABLES), checkfirst=True)
    after_tables = set(inspect(bind).get_table_names())
    if not REPAIR_TABLE_NAMES <= after_tables:
        raise RuntimeError("视频修复旁路表创建不完整")
    after = _counts(bind)
    if before != after:
        raise RuntimeError("迁移前后原素材、方案或来源链路行数发生变化")
    inspector = inspect(bind)
    expected_indexes = {
        "qianchuan_review_feedback": {"ix_qianchuan_review_owner_asset", "ix_qianchuan_review_plan"},
        "video_repair_inputs": {"ix_video_repair_input_owner_asset", "ix_video_repair_input_external_ref"},
        "video_repair_tasks": {"ix_video_repair_owner_created", "uq_video_repair_active_input_source_owner"},
    }
    for table_name, names in expected_indexes.items():
        actual = {item["name"] for item in inspector.get_indexes(table_name)}
        if not names <= actual:
            raise RuntimeError(f"{table_name} 索引创建不完整")
    return {
        "status": "migrated" if missing else "already_current",
        "created_tables": sorted(missing),
        "preserved_row_counts": after,
        "backup_path": str(backup) if backup else None,
    }
