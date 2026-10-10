from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from sqlalchemy import Engine, func, inspect, select

from app.database import Base
from app.models import (
    QianchuanAccountBinding,
    QianchuanCapabilityProbe,
    QianchuanConnection,
    QianchuanDeliveryPlan,
    QianchuanPlanOperation,
    QianchuanUpload,
)


PLAN_MODELS = (QianchuanDeliveryPlan, QianchuanPlanOperation, QianchuanCapabilityProbe)
PLAN_TABLES = tuple(model.__table__ for model in PLAN_MODELS)
PLAN_TABLE_NAMES = {table.name for table in PLAN_TABLES}
LEGACY_TABLES = (
    QianchuanConnection.__table__,
    QianchuanAccountBinding.__table__,
    QianchuanUpload.__table__,
)


def _counts(bind: Engine) -> dict[str, int]:
    existing = set(inspect(bind).get_table_names())
    with bind.connect() as connection:
        return {
            table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in LEGACY_TABLES if table.name in existing
        }


def _backup_sqlite(bind: Engine) -> Path | None:
    database = bind.url.database
    if not database or database == ":memory:":
        return None
    source = Path(database).resolve()
    if not source.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = source.with_name(source.name + f".qianchuan-delivery-{stamp}.bak")
    shutil.copy2(source, target)
    return target


def migrate_qianchuan_delivery(bind: Engine, *, postgres_backup_confirmed: bool = False) -> dict:
    """Create sidecar tables once, preserving and verifying all legacy Qianchuan rows."""
    dialect = bind.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(f"不支持千川计划迁移的数据库类型: {dialect}")
    existing = set(inspect(bind).get_table_names())
    missing = PLAN_TABLE_NAMES - existing
    before = _counts(bind)
    backup: Path | None = None
    if missing:
        if dialect == "postgresql" and not postgres_backup_confirmed:
            raise RuntimeError("创建千川计划旁路表前必须备份 PostgreSQL，并显式确认")
        if dialect == "sqlite":
            backup = _backup_sqlite(bind)
        Base.metadata.create_all(bind=bind, tables=list(PLAN_TABLES), checkfirst=True)

    inspector = inspect(bind)
    after_tables = set(inspector.get_table_names())
    if not PLAN_TABLE_NAMES <= after_tables:
        raise RuntimeError("千川计划旁路表创建不完整")
    after = _counts(bind)
    if before != after:
        raise RuntimeError("迁移前后原千川表行数发生变化，拒绝继续")

    expected_indexes = {
        "qianchuan_delivery_plans": {
            "ix_qianchuan_plan_owner_asset", "ix_qianchuan_plan_status_updated",
            "ix_qianchuan_plan_binding", "uq_qianchuan_plan_platform_ad",
        },
        "qianchuan_plan_operations": {
            "ix_qianchuan_operation_plan_created", "ix_qianchuan_operation_status_created",
        },
        "qianchuan_capability_probes": {
            "ix_qianchuan_capability_owner", "ix_qianchuan_capability_binding_name_expiry",
        },
    }
    for table_name, names in expected_indexes.items():
        actual = {item["name"] for item in inspector.get_indexes(table_name)}
        if not names <= actual:
            raise RuntimeError(f"{table_name} 索引创建不完整")

    return {
        "status": "migrated" if missing else "already_current",
        "created_tables": sorted(missing),
        "legacy_row_counts": after,
        "backup_path": str(backup) if backup else None,
    }

