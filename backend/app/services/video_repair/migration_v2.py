from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from sqlalchemy import Engine, MetaData, inspect, select, text
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database import Base
from app.models import QianchuanReviewFeedback, VideoRepairInput, VideoRepairTask


def _backup_sqlite(bind: Engine) -> Path | None:
    database = bind.url.database
    if not database or database == ":memory:":
        return None
    source = Path(database).resolve()
    if not source.exists():
        return None
    target = source.with_name(source.name + f".video-repair-v2-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(source, target)
    return target


def _snapshot_inputs(connection) -> int:
    tasks = connection.execute(text(
        "SELECT id, tenant_id, created_by, source_asset_id, review_feedback_id, input_id "
        "FROM video_repair_tasks ORDER BY id"
    )).mappings().all()
    created = 0
    for task in tasks:
        if task["input_id"] is not None:
            continue
        feedback_id = task["review_feedback_id"]
        if feedback_id is None:
            raise RuntimeError(f"历史修复任务 {task['id']} 缺少审核反馈，无法迁移")
        external_id = str(feedback_id)
        existing = connection.execute(select(VideoRepairInput.id).where(
            VideoRepairInput.tenant_id == task["tenant_id"],
            VideoRepairInput.created_by == task["created_by"],
            VideoRepairInput.source_asset_id == task["source_asset_id"],
            VideoRepairInput.external_ref_type == "qianchuan_review_feedback",
            VideoRepairInput.external_ref_id == external_id,
        )).scalar_one_or_none()
        if existing is None:
            feedback = connection.execute(select(QianchuanReviewFeedback).where(
                QianchuanReviewFeedback.id == feedback_id,
                QianchuanReviewFeedback.tenant_id == task["tenant_id"],
                QianchuanReviewFeedback.created_by == task["created_by"],
                QianchuanReviewFeedback.video_asset_id == task["source_asset_id"],
            )).mappings().first()
            if not feedback:
                raise RuntimeError(f"历史修复任务 {task['id']} 找不到对应审核反馈")
            result = connection.execute(VideoRepairInput.__table__.insert().values(
                tenant_id=task["tenant_id"], created_by=task["created_by"],
                source_asset_id=task["source_asset_id"],
                source_kind="QIANCHUAN_MOCK" if feedback["source"] == "mock" else "QIANCHUAN_PLATFORM",
                external_ref_type="qianchuan_review_feedback", external_ref_id=external_id,
                audit_platform=feedback["audit_platform"], raw_reason=feedback["raw_reason"],
                suggestions_json=feedback["raw_suggestions_json"] or [],
                time_ranges_json=feedback["provider_time_ranges_json"] or [],
                raw_payload_json={"provider_payload": feedback["provider_payload_json"] or {}},
                occurred_at=feedback["occurred_at"], created_at=datetime.utcnow(),
            ))
            existing = result.inserted_primary_key[0]
            created += 1
        connection.execute(text(
            "UPDATE video_repair_tasks SET input_id = :input_id WHERE id = :task_id"
        ), {"input_id": existing, "task_id": task["id"]})
    missing = connection.execute(text(
        "SELECT count(*) FROM video_repair_tasks WHERE input_id IS NULL"
    )).scalar_one()
    if missing:
        raise RuntimeError(f"仍有 {missing} 条历史修复任务未映射通用输入")
    return created


def _rebuild_sqlite_task_table(connection) -> None:
    inspector = inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("video_repair_tasks")}
    indexes = {item["name"] for item in inspector.get_indexes("video_repair_tasks")}
    current = (columns.get("input_id", {}).get("nullable") is False
               and columns.get("review_feedback_id", {}).get("nullable") is True
               and "uq_video_repair_active_input_source_owner" in indexes)
    if current:
        return
    original = VideoRepairTask.__table__
    expected = [column.name for column in original.columns]
    if set(expected) != set(columns):
        raise RuntimeError("video_repair_tasks 字段状态未知，拒绝自动重建")
    temporary_name = "video_repair_tasks_v2_migration"
    if temporary_name in inspector.get_table_names():
        raise RuntimeError("发现未完成的视频修复 V2 迁移临时表")
    metadata = MetaData()
    metadata.reflect(bind=connection)
    temporary = original.to_metadata(metadata, name=temporary_name)
    names = ", ".join(expected)
    connection.execute(CreateTable(temporary))
    connection.execute(text(
        f"INSERT INTO {temporary_name} ({names}) SELECT {names} FROM video_repair_tasks"))
    before = connection.execute(text("SELECT count(*) FROM video_repair_tasks")).scalar_one()
    after = connection.execute(text(f"SELECT count(*) FROM {temporary_name}")).scalar_one()
    if before != after:
        raise RuntimeError("视频修复任务迁移行数不一致")
    connection.execute(text("DROP TABLE video_repair_tasks"))
    connection.execute(text(f"ALTER TABLE {temporary_name} RENAME TO video_repair_tasks"))
    for index in original.indexes:
        connection.execute(CreateIndex(index))


def migrate_video_repair_v2(bind: Engine, *, postgres_backup_confirmed: bool = False,
                            dry_run: bool = False) -> dict:
    dialect = bind.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError(f"不支持视频修复 V2 迁移的数据库类型: {dialect}")
    tables = set(inspect(bind).get_table_names())
    if "video_repair_tasks" not in tables:
        raise RuntimeError("请先执行视频修复 V1 迁移")
    task_count = 0
    inspector = inspect(bind)
    task_columns = {column["name"]: column for column in inspector.get_columns("video_repair_tasks")}
    task_indexes = {item["name"] for item in inspector.get_indexes("video_repair_tasks")}
    already_current = (
        "video_repair_inputs" in tables
        and task_columns.get("input_id", {}).get("nullable") is False
        and task_columns.get("review_feedback_id", {}).get("nullable") is True
        and "uq_video_repair_active_input_source_owner" in task_indexes
    )
    if "video_repair_tasks" in tables:
        with bind.connect() as connection:
            task_count = connection.execute(text("SELECT count(*) FROM video_repair_tasks")).scalar_one()
    if dry_run:
        return {"status": "dry_run", "task_count": task_count, "created_inputs": 0,
                "backup_path": None}
    if already_current:
        return {"status": "already_current", "task_count": task_count,
                "created_inputs": 0, "backup_path": None}
    if dialect == "postgresql" and not postgres_backup_confirmed:
        raise RuntimeError("执行视频修复 V2 迁移前必须备份 PostgreSQL，并显式确认")
    backup = _backup_sqlite(bind) if dialect == "sqlite" else None
    Base.metadata.create_all(bind=bind, tables=[VideoRepairInput.__table__], checkfirst=True)
    with bind.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("video_repair_tasks")}
        if "input_id" not in columns:
            connection.execute(text(
                "ALTER TABLE video_repair_tasks ADD COLUMN input_id INTEGER NULL "
                "REFERENCES video_repair_inputs(id)"))
        created = _snapshot_inputs(connection)
        if dialect == "postgresql":
            connection.execute(text("DROP INDEX IF EXISTS uq_video_repair_active_feedback_source_owner"))
            connection.execute(text("ALTER TABLE video_repair_tasks ALTER COLUMN review_feedback_id DROP NOT NULL"))
            connection.execute(text("ALTER TABLE video_repair_tasks ALTER COLUMN input_id SET NOT NULL"))
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_video_repair_active_input_source_owner "
                "ON video_repair_tasks (tenant_id, created_by, input_id, source_asset_id) "
                "WHERE status IN ('ANALYZING','NEEDS_CONFIRMATION','APPROVED','RENDERING')"))
    if dialect == "sqlite":
        with bind.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            try:
                with connection.begin():
                    _rebuild_sqlite_task_table(connection)
                    if connection.execute(text("PRAGMA foreign_key_check")).first():
                        raise RuntimeError("视频修复 V2 迁移后外键校验失败")
            finally:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
    return {"status": "already_current" if already_current and created == 0 else "migrated",
            "task_count": task_count, "created_inputs": created,
            "backup_path": str(backup) if backup else None}
