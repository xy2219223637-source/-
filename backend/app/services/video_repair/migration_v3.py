from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from sqlalchemy import Engine, MetaData, inspect, text
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base
from app.models import (
    VideoRepairAttachment,
    VideoRepairInput,
    VideoRepairJob,
    VideoRepairPreview,
    VideoRepairTask,
)


def _backup_sqlite(bind: Engine) -> Path | None:
    database = bind.url.database
    if not database or database == ":memory:":
        return None
    source = Path(database).resolve()
    if not source.exists():
        return None
    target = source.with_name(
        source.name + f".video-repair-v3-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(source, target)
    return target


def _add_columns(connection) -> None:
    dialect = connection.dialect.name
    input_columns = {row["name"] for row in inspect(connection).get_columns("video_repair_inputs")}
    if "expected_result" not in input_columns:
        connection.execute(text(
            "ALTER TABLE video_repair_inputs ADD COLUMN expected_result TEXT NOT NULL DEFAULT ''"))
    task_columns = {row["name"] for row in inspect(connection).get_columns("video_repair_tasks")}
    additions = {
        "product_id": "INTEGER NULL REFERENCES products(id)",
        "schema_version": "VARCHAR(32) NOT NULL DEFAULT 'video_repair_task.v1'",
        "draft_revision": "INTEGER NOT NULL DEFAULT 1",
        "accepted_preview_id": "INTEGER NULL",
        "cancel_reason": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in task_columns:
            connection.execute(text(
                f"ALTER TABLE video_repair_tasks ADD COLUMN {name} {definition}"))
    connection.execute(text(
        "UPDATE video_repair_tasks SET product_id = ("
        "SELECT product_id FROM video_assets WHERE video_assets.id = video_repair_tasks.source_asset_id"
        ") WHERE product_id IS NULL"))
    missing = connection.execute(text(
        "SELECT count(*) FROM video_repair_tasks WHERE product_id IS NULL")).scalar_one()
    if missing:
        raise RuntimeError(f"存在 {missing} 条返修任务无法从源成品回填 product_id")


def _rebuild_sqlite_tasks(connection) -> None:
    original = VideoRepairTask.__table__
    existing_columns = {row["name"] for row in inspect(connection).get_columns(original.name)}
    expected = [column.name for column in original.columns]
    if set(expected) != existing_columns:
        raise RuntimeError("video_repair_tasks 字段状态未知，拒绝自动重建 V3 表")
    temporary_name = "video_repair_tasks_v3_migration"
    if temporary_name in inspect(connection).get_table_names():
        raise RuntimeError("发现未完成的视频修复 V3 迁移临时表")
    metadata = MetaData()
    # Some legacy optional tables reference deployments that were never enabled.
    # Reflect their names without recursively resolving those absent FK targets.
    metadata.reflect(bind=connection, resolve_fks=False)
    temporary = original.to_metadata(metadata, name=temporary_name)
    names = ", ".join(expected)
    connection.execute(CreateTable(temporary))
    connection.execute(text(
        f"INSERT INTO {temporary_name} ({names}) SELECT {names} FROM video_repair_tasks"))
    before = connection.execute(text("SELECT count(*) FROM video_repair_tasks")).scalar_one()
    after = connection.execute(text(f"SELECT count(*) FROM {temporary_name}")).scalar_one()
    if before != after:
        raise RuntimeError("视频修复 V3 迁移行数不一致")
    connection.execute(text("DROP TABLE video_repair_tasks"))
    connection.execute(text(f"ALTER TABLE {temporary_name} RENAME TO video_repair_tasks"))
    for index in original.indexes:
        connection.execute(CreateIndex(index))


def _upgrade_postgres_constraints(connection) -> None:
    connection.execute(text(
        "ALTER TABLE video_repair_tasks ALTER COLUMN product_id SET NOT NULL"))
    connection.execute(text(
        "ALTER TABLE video_repair_tasks DROP CONSTRAINT IF EXISTS ck_video_repair_status"))
    connection.execute(text(
        "ALTER TABLE video_repair_tasks ADD CONSTRAINT ck_video_repair_status CHECK (status IN ("
        "'ANALYZING','NEEDS_CONFIRMATION','NEED_MORE_MATERIAL','NON_VIDEO_FIXABLE','APPROVED',"
        "'RENDERING','AWAITING_MANUAL_DECISION','AWAITING_MATERIAL','CONFIGURING',"
        "'PREVIEW_QUEUED','PREVIEW_RENDERING','PREVIEW_READY','COMPLETED','FAILED','CANCELED'))"))
    connection.execute(text(
        "DROP INDEX IF EXISTS uq_video_repair_active_input_source_owner"))
    connection.execute(text(
        "CREATE UNIQUE INDEX uq_video_repair_active_input_source_owner ON video_repair_tasks "
        "(tenant_id, created_by, input_id, source_asset_id) WHERE status IN ("
        "'ANALYZING','NEEDS_CONFIRMATION','APPROVED','RENDERING','AWAITING_MANUAL_DECISION',"
        "'AWAITING_MATERIAL','CONFIGURING','PREVIEW_QUEUED','PREVIEW_RENDERING','PREVIEW_READY')"))


def migrate_video_repair_v3(bind: Engine, *, postgres_backup_confirmed: bool = False,
                            dry_run: bool = False) -> dict:
    dialect = bind.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError(f"不支持视频修复 V3 迁移的数据库类型: {dialect}")
    tables = set(inspect(bind).get_table_names())
    if not {"video_repair_inputs", "video_repair_tasks"}.issubset(tables):
        raise RuntimeError("请先执行视频修复 V2 迁移")
    task_columns = {row["name"] for row in inspect(bind).get_columns("video_repair_tasks")}
    new_tables = {"video_repair_attachments", "video_repair_previews", "video_repair_jobs"}
    already_current = (
        {"product_id", "schema_version", "draft_revision", "accepted_preview_id", "cancel_reason"}
        .issubset(task_columns)
        and "expected_result" in {row["name"] for row in inspect(bind).get_columns("video_repair_inputs")}
        and new_tables.issubset(tables)
    )
    with bind.connect() as connection:
        task_count = connection.execute(text("SELECT count(*) FROM video_repair_tasks")).scalar_one()
    if dry_run:
        return {"status": "dry_run", "task_count": task_count, "backup_path": None}
    if already_current:
        return {"status": "already_current", "task_count": task_count, "backup_path": None}
    if dialect == "postgresql" and not postgres_backup_confirmed:
        raise RuntimeError("执行视频修复 V3 迁移前必须备份 PostgreSQL，并显式确认")
    backup = _backup_sqlite(bind) if dialect == "sqlite" else None
    with bind.begin() as connection:
        _add_columns(connection)
        if dialect == "postgresql":
            _upgrade_postgres_constraints(connection)
    if dialect == "sqlite":
        with bind.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            try:
                with connection.begin():
                    _rebuild_sqlite_tasks(connection)
            finally:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
    Base.metadata.create_all(
        bind=bind,
        tables=[
            VideoRepairAttachment.__table__,
            VideoRepairPreview.__table__,
            VideoRepairJob.__table__,
        ],
        checkfirst=True,
    )
    return {"status": "migrated", "task_count": task_count,
            "backup_path": str(backup) if backup else None}
