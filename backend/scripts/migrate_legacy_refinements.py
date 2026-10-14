"""Copy one tenant's legacy refinement history using explicit ecommerce asset mappings."""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    EcommerceProduct, EcommerceVideoAsset, EcommerceVideoLineageSegment,
    EcommerceVideoRefinementAnalysisJob, EcommerceVideoRefinementPlan,
    EcommerceVideoRefinementPreview, EcommerceVideoRefinementRequest,
    EcommerceVideoRefinementSnapshot, EcommerceVideoRefinementTask,
    EcommerceVideoRefinementVersion, EcommerceVideoRefinementVoiceover,
    VideoAsset, VideoLineageSegment, VideoRefinementAnalysisJob, VideoRefinementPlan,
    VideoRefinementPreview, VideoRefinementRequest, VideoRefinementSnapshot,
    VideoRefinementTask, VideoRefinementVersion, VideoRefinementVoiceover,
)
from app.services.refinement_media import used_material_ids
from app.services.refinement_plan import content_hash


MIGRATION = "legacy-refinement-to-ecommerce-v1"
MARKER = "legacy_refinement_migration"
MODELS = (
    (VideoRefinementSnapshot, EcommerceVideoRefinementSnapshot),
    (VideoRefinementRequest, EcommerceVideoRefinementRequest),
    (VideoRefinementVoiceover, EcommerceVideoRefinementVoiceover),
    (VideoRefinementPlan, EcommerceVideoRefinementPlan),
    (VideoRefinementPreview, EcommerceVideoRefinementPreview),
    (VideoRefinementVersion, EcommerceVideoRefinementVersion),
    (VideoRefinementAnalysisJob, EcommerceVideoRefinementAnalysisJob),
)
ASSET_KEYS = {
    "asset_id", "source_asset_id", "material_asset_id", "video_asset_id",
    "audio_asset_id", "parent_asset_id", "output_asset_id", "target_asset_id",
    "original_asset_id",
}
PAUSE_STATES = {"queued", "analyzing", "rendering", "queued_v3", "analyzing_v3", "rendering_v3"}
PAUSE_MESSAGE = "历史任务迁移后已暂停, 请核对方案后手动重试"


def row_data(row) -> dict:
    return {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns}


def clone(row, model, **changes):
    values = row_data(row)
    values.pop("id")
    return model(**{**values, **changes})


class HistoryCopy:
    def __init__(self, db: Session, tenant_id: str):
        self.db, self.tenant_id = db, tenant_id
        required = {VideoRefinementTask.__tablename__, EcommerceVideoRefinementTask.__tablename__,
                    *(old.__tablename__ for old, _ in MODELS), *(new.__tablename__ for _, new in MODELS),
                    VideoLineageSegment.__tablename__, EcommerceVideoLineageSegment.__tablename__}
        if not required.issubset(inspect(db.connection()).get_table_names()):
            raise ValueError("精修表不完整, 请先初始化旧精修和电商精修数据表")
        self.tasks = db.query(VideoRefinementTask).filter_by(tenant_id=tenant_id).order_by(VideoRefinementTask.id).all()
        self.old_tasks = {task.id: task for task in self.tasks}
        self.rows = {}
        task_ids = list(self.old_tasks)
        for old, _ in MODELS:
            rows = db.query(old).filter(old.task_id.in_(task_ids)).order_by(old.id).all() if task_ids else []
            if any(hasattr(row, "tenant_id") and row.tenant_id != tenant_id for row in rows):
                raise ValueError("历史任务的关联记录存在跨租户引用")
            self.rows[old] = rows
        self.by_task = {}
        for model, rows in self.rows.items():
            groups = defaultdict(list)
            for row in rows:
                groups[row.task_id].append(row)
            self.by_task[model] = groups
        self.assets = {}
        for asset in db.query(EcommerceVideoAsset).filter_by(tenant_id=tenant_id).all():
            match = re.fullmatch(r"legacy_video:(\d+)", asset.source)
            if not match:
                continue
            old_id = int(match[1])
            if old_id in self.assets:
                raise ValueError(f"旧视频 {old_id} 对应多个电商视频, 拒绝猜测映射")
            self.assets[old_id] = asset
        self.products = {}
        self.old_assets = {asset.id: asset for asset in db.query(VideoAsset).filter_by(tenant_id=tenant_id).all()}
        for old_id, asset in self.assets.items():
            old = self.old_assets.get(old_id)
            if old:
                prior = self.products.setdefault(old.product_id, asset.product_id)
                if prior != asset.product_id:
                    raise ValueError(f"旧商品 {old.product_id} 的视频映射到了多个电商商品")
        self.existing = {}
        for task in db.query(EcommerceVideoRefinementTask).filter_by(tenant_id=tenant_id).all():
            marker = task.source_snapshot.get(MARKER, {})
            if marker.get("version") == MIGRATION:
                old_id = marker["task_id"]
                if old_id in self.existing:
                    raise ValueError(f"旧精修任务 {old_id} 已被重复迁移")
                self.existing[old_id] = task.id
        self.pending = [task for task in self.tasks if task.id not in self.existing]
        output_ids = [row.output_asset_id for row in self.rows[VideoRefinementVersion]]
        self.lineages = db.query(VideoLineageSegment).filter(
            VideoLineageSegment.tenant_id == tenant_id, VideoLineageSegment.target_asset_id.in_(output_ids),
        ).order_by(VideoLineageSegment.id).all() if output_ids else []
        self.maps = defaultdict(dict)
        self.hashes = {}
        self.unavailable = set()
        self.urls = {}
        self.public_ids = {}
        for old_id, asset in self.assets.items():
            old = self.old_assets.get(old_id)
            if old:
                self.urls[old.object_url] = asset.object_url
                self.public_ids[old.asset_id] = asset.asset_id

    def asset(self, old_id: int, *, product_id: int, kind: str | None = None):
        asset = self.assets.get(old_id)
        if not asset or asset.product_id != product_id or asset.library_type != "finished" or kind and asset.asset_kind != kind:
            raise ValueError(f"旧视频 {old_id} 缺少同商品, 同租户的电商{kind or '视频'}映射")
        return asset

    def preflight(self) -> dict:
        for task in self.pending:
            product_id = self.products.get(task.product_id)
            product = self.db.get(EcommerceProduct, product_id) if product_id else None
            if not product or product.tenant_id != self.tenant_id:
                raise ValueError(f"任务 {task.id} 缺少电商商品映射")
            source = self.asset(task.source_asset_id, product_id=product_id, kind="final_output")
            if source.content_hash != task.source_hash:
                raise ValueError(f"任务 {task.id} 的源成片哈希已变化")
            snapshots = self.by_task[VideoRefinementSnapshot][task.id]
            if len(snapshots) != 1:
                raise ValueError(f"任务 {task.id} 必须有且仅有一个来源快照")
            frozen = task.source_snapshot
            if any(frozen.get(key) != getattr(source, key) for key in ("width", "height", "fps")) or frozen.get("duration_ms") != round(source.duration_seconds * 1000):
                raise ValueError(f"任务 {task.id} 的源成片媒体信息已变化")
            for snapshot in [snapshots[0], *self.by_task[VideoRefinementRequest][task.id]]:
                for material in snapshot.snapshot_json.get("material_catalog", []):
                    asset = self.assets.get(material["asset_id"])
                    if asset and asset.product_id != product_id:
                        raise ValueError(f"任务 {task.id} 的素材存在跨商品引用")
                    if not asset:
                        if material["asset_id"] in self.old_assets:
                            raise ValueError(f"素材 {material['asset_id']} 尚未复制到商品素材库")
                        self.unavailable.add(material["asset_id"])
                    elif material.get("object_url"):
                        self.urls[material["object_url"]] = asset.object_url
            for plan in self.by_task[VideoRefinementPlan][task.id]:
                for old_id in used_material_ids(plan.plan_json):
                    asset = self.assets.get(old_id)
                    if not asset and old_id not in self.unavailable:
                        raise ValueError(f"方案 {plan.id} 存在未映射的素材 {old_id}")
                    if asset and (asset.product_id != product_id or asset.asset_kind != "material"):
                        raise ValueError(f"方案 {plan.id} 引用了非同商品素材")
            plans = self.by_task[VideoRefinementPlan][task.id]
            if task.status not in {"CREATED", "CANCELLED"} and not any(p.revision == task.current_revision for p in plans):
                raise ValueError(f"任务 {task.id} 缺少当前版本方案")
            for version in self.by_task[VideoRefinementVersion][task.id]:
                output = self.asset(version.output_asset_id, product_id=product_id, kind="final_output")
                if self.db.query(EcommerceVideoRefinementVersion).filter_by(output_asset_id=output.id).first():
                    raise ValueError(f"成片 {output.id} 已关联其他电商精修版本")
                preview = next((p for p in self.by_task[VideoRefinementPreview][task.id] if p.id == version.preview_id), None)
                if not preview or preview.status != "accepted" or output.content_hash != preview.output_hash:
                    raise ValueError(f"版本 {version.id} 缺少有效的已接受预览")
        pending_ids = {task.id for task in self.pending}
        counts = {new.__tablename__: sum(row.task_id in pending_ids for row in self.rows[old]) for old, new in MODELS}
        counts[EcommerceVideoRefinementTask.__tablename__] = len(self.pending)
        outputs = {v.output_asset_id for v in self.rows[VideoRefinementVersion] if v.task_id in pending_ids}
        counts[EcommerceVideoLineageSegment.__tablename__] = sum(l.target_asset_id in outputs for l in self.lineages)
        return {"tenant_id": self.tenant_id, "legacy_tasks": len(self.tasks), "tasks_to_copy": len(self.pending),
                "already_migrated": len(self.tasks) - len(self.pending), "records_to_copy": counts,
                "deleted_material_ids": sorted(self.unavailable)}

    def backup(self) -> dict:
        return {"migration": MIGRATION, "tenant_id": self.tenant_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "legacy_tables": {VideoRefinementTask.__tablename__: [row_data(t) for t in self.tasks],
                                  **{model.__tablename__: [row_data(r) for r in rows] for model, rows in self.rows.items()},
                                  VideoLineageSegment.__tablename__: [row_data(r) for r in self.lineages]},
                "asset_map": {old: asset.id for old, asset in self.assets.items()},
                "already_migrated": self.existing}

    def rewrite(self, value, parent: str = ""):
        if isinstance(value, list):
            rows = [self.rewrite(row, parent) for row in value]
            return [row for row in rows if not (isinstance(row, dict) and row.get("asset_id", 1) is None)]
        if isinstance(value, str):
            if value in self.urls:
                return self.urls[value]
            if value in self.public_ids:
                return self.public_ids[value]
            material = re.fullmatch(r"material-(\d+)", value)
            if material:
                old_id = int(material[1])
                return f"material-{self.assets[old_id].id}" if old_id in self.assets else f"legacy-unavailable-material-{old_id}"
            cue = re.fullmatch(r"tts-(\d+)-(\d+)", value)
            if cue and int(cue[1]) in self.maps[VideoRefinementVoiceover]:
                return f"tts-{self.maps[VideoRefinementVoiceover][int(cue[1])]}-{cue[2]}"
            return value
        if not isinstance(value, dict):
            return deepcopy(value)
        result = {}
        for key, item in value.items():
            if key in ASSET_KEYS and isinstance(item, int):
                if key == "source_asset_id" and value.get("source_kind") == "tts":
                    result[key] = self.maps[VideoRefinementVoiceover][item]
                else:
                    result[key] = self.assets[item].id if item in self.assets else None
            elif key == "product_id" and isinstance(item, int):
                result[key] = self.products[item]
            elif key == "voiceover_id" and isinstance(item, int) or key == "id" and parent == "voiceover_assets":
                result[key] = self.maps[VideoRefinementVoiceover][item]
            elif key == "request_snapshot_id" and isinstance(item, int):
                result[key] = self.maps[VideoRefinementRequest][item]
            else:
                result[key] = self.rewrite(item, key)
        if "material_catalog" in value:
            unavailable = [deepcopy(row) for row in value["material_catalog"] if row["asset_id"] not in self.assets]
            if unavailable:
                result["legacy_unavailable_materials"] = unavailable
            result["material_catalog"] = [row for row in result["material_catalog"]
                                          if self.assets_by_id(row["asset_id"]).asset_kind == "material"]
        if isinstance(value.get("schema_version"), str) and value["schema_version"].startswith("video_refinement_snapshot."):
            result["asset_domain"] = "ecommerce"
            if result.get("engineering_plan") is not None:
                result["legacy_engineering_plan"] = deepcopy(value["engineering_plan"])
                result["engineering_plan"] = None
            self.hashes[content_hash(value)] = content_hash(result)
        if "item_id" in value and "type" in value:
            missing = {id for id in [value.get("material_asset_id"), *(s.get("asset_id") for s in value.get("material_segments", []))]
                       if id is not None and id not in self.assets}
            if missing:
                result["enabled"] = False
                result["legacy_unavailable_binding"] = {"asset_ids": sorted(missing), "original_item": deepcopy(value)}
        return result

    def assets_by_id(self, id):
        return self.target_assets[id]

    def apply(self) -> dict:
        self.target_assets = {asset.id: asset for asset in self.assets.values()}
        new_tasks, new_rows = {}, {}
        for task in self.pending:
            source = self.assets[task.source_asset_id]
            frozen = self.rewrite(task.source_snapshot)
            frozen["object_url"] = source.object_url
            frozen[MARKER] = {"version": MIGRATION, "task_id": task.id, "product_id": task.product_id,
                              "source_asset_id": task.source_asset_id}
            copied = clone(task, EcommerceVideoRefinementTask, source_asset_id=source.id,
                           product_id=source.product_id, source_snapshot=frozen)
            self.db.add(copied)
            new_tasks[task.id] = copied
        self.db.flush()
        self.maps[VideoRefinementTask] = {old: row.id for old, row in new_tasks.items()}
        for old_model, new_model in MODELS:
            copies = {}
            for row in self.rows[old_model]:
                if row.task_id not in new_tasks:
                    continue
                changes = {"task_id": new_tasks[row.task_id].id}
                if old_model is VideoRefinementPlan:
                    changes["source_snapshot_id"] = self.maps[VideoRefinementSnapshot][row.source_snapshot_id]
                elif old_model is VideoRefinementPreview:
                    changes["plan_id"] = self.maps[VideoRefinementPlan][row.plan_id]
                elif old_model is VideoRefinementVersion:
                    changes.update(preview_id=self.maps[VideoRefinementPreview][row.preview_id],
                                   plan_id=self.maps[VideoRefinementPlan][row.plan_id],
                                   source_snapshot_id=self.maps[VideoRefinementSnapshot][row.source_snapshot_id],
                                   parent_asset_id=self.assets[row.parent_asset_id].id,
                                   output_asset_id=self.assets[row.output_asset_id].id)
                if hasattr(row, "status") and row.status in PAUSE_STATES:
                    changes.update(status="failed", error_message=PAUSE_MESSAGE)
                copied = clone(row, new_model, **changes)
                self.db.add(copied)
                copies[row.id] = copied
            self.db.flush()
            self.maps[old_model] = {old: row.id for old, row in copies.items()}
            new_rows[old_model] = copies

        for row in self.rows[VideoRefinementSnapshot]:
            if row.id in new_rows[VideoRefinementSnapshot]:
                copied = new_rows[VideoRefinementSnapshot][row.id]
                copied.snapshot_json = self.rewrite(row.snapshot_json)
                copied.content_hash = content_hash(copied.snapshot_json)
                self.hashes[row.content_hash] = copied.content_hash
        for row in self.rows[VideoRefinementRequest]:
            if row.id in new_rows[VideoRefinementRequest]:
                copied = new_rows[VideoRefinementRequest][row.id]
                copied.snapshot_json = self.rewrite(row.snapshot_json)
                copied.snapshot_hash = content_hash(copied.snapshot_json)
                copied.options_json = self.rewrite(row.options_json)
                self.hashes[row.snapshot_hash] = copied.snapshot_hash
        for row in self.rows[VideoRefinementPlan]:
            if row.id not in new_rows[VideoRefinementPlan]:
                continue
            copied = new_rows[VideoRefinementPlan][row.id]
            plan = self.rewrite(row.plan_json)
            if plan.get("snapshot_hash"):
                if plan["snapshot_hash"] not in self.hashes:
                    raise ValueError(f"方案 {row.id} 缺少来源快照哈希映射")
                plan["snapshot_hash"] = self.hashes[plan["snapshot_hash"]]
            missing = sorted({id for item in plan.get("items", []) for id in item.get("legacy_unavailable_binding", {}).get("asset_ids", [])})
            if missing:
                plan["execution_blockers"] = [*plan.get("execution_blockers", []), {
                    "item_id": "", "code": "MATERIAL_REQUIRED",
                    "message": f"历史素材 {missing} 已删除, 请重新选择商品素材",
                }]
                task = new_tasks[row.task_id]
                if task.current_revision == row.revision and task.status not in {"COMPLETED", "CANCELLED"}:
                    task.status, task.confirmed_revision = "MATERIAL_REQUIRED", None
            copied.plan_json, copied.plan_hash = plan, content_hash(plan)
        for row in self.rows[VideoRefinementPreview]:
            if row.id in new_rows[VideoRefinementPreview]:
                copied = new_rows[VideoRefinementPreview][row.id]
                copied.plan_hash = new_rows[VideoRefinementPlan][row.plan_id].plan_hash
                copied.recipe_json = self.rewrite(row.recipe_json)
                copied.validation_json = self.rewrite(row.validation_json)
        for row in self.rows[VideoRefinementVersion]:
            if row.id in new_rows[VideoRefinementVersion]:
                new_rows[VideoRefinementPreview][row.preview_id].output_url = self.assets[row.output_asset_id].object_url
        for row in self.rows[VideoRefinementVoiceover]:
            if row.id in new_rows[VideoRefinementVoiceover]:
                copied = new_rows[VideoRefinementVoiceover][row.id]
                copied.parameters_json, copied.timing_json = self.rewrite(row.parameters_json), self.rewrite(row.timing_json)
        for row in self.rows[VideoRefinementAnalysisJob]:
            if row.id in new_rows[VideoRefinementAnalysisJob]:
                copied = new_rows[VideoRefinementAnalysisJob][row.id]
                copied.snapshot_json, copied.options_json = self.rewrite(row.snapshot_json), self.rewrite(row.options_json)
                copied.result_json = self.rewrite(row.result_json)
                # Checkpoint keys contain old asset fingerprints; keep them as history, never reuse them for a new run.
                copied.batches_json = {}
                if row.batches_json:
                    copied.result_json = {**copied.result_json, "legacy_analysis_checkpoints": deepcopy(row.batches_json)}
                copied.fingerprint = content_hash({"snapshot": content_hash(copied.snapshot_json),
                    "brief": new_tasks[row.task_id].user_brief, "options": copied.options_json, "method": "v2"})
        output_task = {v.output_asset_id: v.task_id for v in self.rows[VideoRefinementVersion] if v.task_id in new_tasks}
        for row in self.lineages:
            if row.target_asset_id not in output_task:
                continue
            task = self.old_tasks[output_task[row.target_asset_id]]
            product_id = new_tasks[task.id].product_id
            target_id = self.asset(row.target_asset_id, product_id=product_id).id
            if self.db.query(EcommerceVideoLineageSegment).filter_by(target_asset_id=target_id, segment_index=row.segment_index).first():
                raise ValueError(f"成片 {target_id} 已有片段来源记录, 拒绝覆盖")
            copied = clone(row, EcommerceVideoLineageSegment,
                source_asset_id=self.asset(row.source_asset_id, product_id=product_id).id,
                original_asset_id=self.asset(row.original_asset_id, product_id=product_id).id if row.original_asset_id else None,
                target_asset_id=target_id, plan_version_id=self.maps[VideoRefinementPlan][row.plan_version_id],
                variant_id=f"refinement-{new_tasks[task.id].id}-r{row.variant_id.rsplit('-r', 1)[-1]}")
            self.db.add(copied)
        self.db.flush()
        for old_id, task in new_tasks.items():
            task.updated_at = self.old_tasks[old_id].updated_at
        self.db.flush()
        return {"task_map": self.maps[VideoRefinementTask],
                "record_maps": {model.__tablename__: ids for model, ids in self.maps.items()}}


def migrate_legacy_refinements(db: Session, tenant_id: str, *, apply: bool = False,
                               expected_tasks: int | None = None, backup_dir: Path | None = None) -> dict:
    if not tenant_id.strip():
        raise ValueError("必须明确指定租户")
    if db.bind.dialect.name == "postgresql":
        if not db.in_transaction():
            db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        if apply:
            lock = int.from_bytes(hashlib.sha256((MIGRATION + tenant_id).encode()).digest()[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
        else:
            db.execute(text("SET TRANSACTION READ ONLY"))
    history = HistoryCopy(db, tenant_id)
    summary = history.preflight()
    if expected_tasks is not None and expected_tasks != len(history.tasks):
        raise ValueError(f"旧任务数量为 {len(history.tasks)}, 与预期 {expected_tasks} 不符")
    if not apply:
        return {**summary, "applied": False}
    if expected_tasks is None:
        raise ValueError("执行迁移必须提供预期旧任务数量")
    if not history.pending:
        return {**summary, "applied": True, "task_map": history.existing}
    backup_dir = backup_dir or Path(tempfile.mkdtemp(prefix="ecommerce-refinement-migration-"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "legacy-refinement-backup.json"
    with backup_path.open("x", encoding="utf-8") as backup:
        json.dump(history.backup(), backup, ensure_ascii=False, indent=2, default=str)
    result = history.apply()
    return {**summary, "applied": True, "backup_path": str(backup_path), **result}


def main() -> None:
    parser = argparse.ArgumentParser(description="复制当前租户旧精修历史, 保留旧库和现有电商任务; 默认只读预检查")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--expected-tasks", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            result = migrate_legacy_refinements(db, args.tenant_id, apply=args.apply,
                expected_tasks=args.expected_tasks, backup_dir=args.backup_dir)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError) as exc:
        raise SystemExit(f"迁移已中止, 数据未提交: {exc}") from exc


if __name__ == "__main__":
    main()
