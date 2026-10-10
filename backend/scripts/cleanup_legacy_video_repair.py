"""Archive and remove V1 repair task records without touching source/output media."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import bindparam, inspect, text

from app.database import engine


LEGACY_VERSION = "video_repair_task.v1"
RELATED = ("video_repair_jobs", "video_repair_attachments", "video_repair_previews")
WHERE_LEGACY_TASKS = (
    "task_id IN (SELECT id FROM video_repair_tasks "
    "WHERE schema_version = 'video_repair_task.v1')"
)


def _rows(connection, table: str, where: str) -> list[dict]:
    return [dict(row) for row in connection.execute(text(
        f"SELECT * FROM {table} WHERE {where}"  # table and where are local constants
    )).mappings()]


def _snapshot(connection) -> tuple[dict, list[dict], list[dict], dict[str, list[dict]], set[int]]:
    required = {"video_repair_tasks", "video_repair_inputs", *RELATED}
    if not required.issubset(set(inspect(connection).get_table_names())):
        raise RuntimeError("返修数据表不完整，拒绝清理")
    legacy = _rows(connection, "video_repair_tasks",
                   "schema_version = 'video_repair_task.v1'")
    ids = {row["id"] for row in legacy}
    input_ids = {row["input_id"] for row in legacy}
    related = {table: _rows(connection, table, WHERE_LEGACY_TASKS) for table in RELATED}
    inputs = _rows(connection, "video_repair_inputs",
                   "id IN (SELECT input_id FROM video_repair_tasks WHERE schema_version = 'video_repair_task.v1')")
    if {row["id"] for row in inputs} != input_ids:
        raise RuntimeError("V1 输入快照不完整，拒绝清理")
    shared = connection.execute(text("""
        SELECT count(*) FROM video_repair_tasks
        WHERE schema_version <> 'video_repair_task.v1'
          AND input_id IN (SELECT input_id FROM video_repair_tasks
                           WHERE schema_version = 'video_repair_task.v1')
    """)).scalar_one()
    if shared:
        raise RuntimeError("V1 输入仍被其他版本引用，拒绝清理")
    result = {"legacy_tasks": len(ids), "legacy_inputs": len(inputs),
              "related": {table: len(rows) for table, rows in related.items()},
              "media_deleted": 0}
    return result, legacy, inputs, related, input_ids


def preview() -> dict:
    """Run SELECT statements only; never acquire a write transaction."""
    with engine.connect() as connection:
        result, *_ = _snapshot(connection)
        return result


def apply_cleanup() -> dict:
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("LOCK TABLE video_repair_tasks IN SHARE ROW EXCLUSIVE MODE"))
        result, legacy, inputs, related, input_ids = _snapshot(connection)
        if legacy:
            backup = Path(__file__).resolve().parents[2] / "backups" / (
                f"video_repair_v1_cleanup_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                raise RuntimeError("备份文件已存在，拒绝覆盖")
            snapshot = {"created_at": datetime.now(timezone.utc).isoformat(),
                        "schema_version": LEGACY_VERSION,
                        "video_repair_tasks": legacy, "video_repair_inputs": inputs,
                        **related}
            backup.write_text(json.dumps(snapshot, ensure_ascii=False, default=str, indent=2),
                              encoding="utf-8")
            result["backup_path"] = str(backup)
            for table in RELATED:
                connection.execute(text(f"DELETE FROM {table} WHERE {WHERE_LEGACY_TASKS}"))
            connection.execute(text("DELETE FROM video_repair_tasks WHERE schema_version = 'video_repair_task.v1'"))
            connection.execute(text("""
                DELETE FROM video_repair_inputs
                WHERE id IN :input_ids
                  AND NOT EXISTS (SELECT 1 FROM video_repair_tasks t
                                  WHERE t.input_id = video_repair_inputs.id)
            """).bindparams(bindparam("input_ids", expanding=True)),
                               {"input_ids": sorted(input_ids)})
        if engine.dialect.name == "postgresql":
            connection.execute(text("""
                ALTER TABLE video_repair_tasks
                ALTER COLUMN schema_version SET DEFAULT 'video_repair_task.v2'
            """))
        remaining = connection.execute(text("""
            SELECT count(*) FROM video_repair_tasks WHERE schema_version = 'video_repair_task.v1'
        """)).scalar_one()
        if remaining:
            raise RuntimeError("仍有 V1 任务，回滚清理")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="备份后在事务内删除 V1 返修记录")
    args = parser.parse_args()
    print(json.dumps(apply_cleanup() if args.apply else preview(), ensure_ascii=False, indent=2))
