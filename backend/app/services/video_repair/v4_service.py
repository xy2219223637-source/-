from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    EcommerceProduct, EcommerceVideoAsset, VideoAsset, VideoRepairAttachment,
    VideoRepairInput, VideoRepairJob, VideoRepairPreview, VideoRepairTask,
)
from app.services.video_analysis_queue import enqueue_video_analysis
from app.services.video_repair.protocol_v4 import InvalidRepairPlan, build_plan, plan_hash
from app.services.video_repair.state_machine_v4 import transition
from app.services.video_taxonomy import inherit_public_tags_to_final


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(409, detail={"code": code, "message": message})


def _validate_v4(task: VideoRepairTask) -> None:
    if task.schema_version != "video_repair_task.v2":
        raise _conflict("LEGACY_REPAIR_TASK", "旧版返修任务不能在 V4 工作台修改")
    if task.status in {"COMPLETED", "CANCELED"}:
        raise _conflict("REPAIR_TASK_FINAL", "已结束任务不能修改")


def _asset_ref(db: Session, task: VideoRepairTask, value: dict[str, Any]) -> VideoAsset:
    asset = db.query(VideoAsset).filter_by(
        id=value.get("ref_id"), tenant_id=task.tenant_id, product_id=task.product_id,
        excluded=False,
    ).first()
    if not asset:
        raise HTTPException(422, detail={
            "code": "MATERIAL_PRODUCT_MISMATCH", "message": "素材不存在或不属于当前商品"})
    if asset.asset_kind == "final_output" and asset.id == task.source_asset_id:
        raise HTTPException(422, detail={"code": "INVALID_MATERIAL", "message": "不能用源成片替换自身"})
    return asset


def _attachment_ref(db: Session, task: VideoRepairTask, value: dict[str, Any]) -> VideoRepairAttachment:
    row = db.query(VideoRepairAttachment).filter_by(
        id=value.get("attachment_id"), task_id=task.id, tenant_id=task.tenant_id,
        status="READY", kind="AUDIO",
    ).first()
    if not row:
        raise HTTPException(422, detail={"code": "AUDIO_REQUIRED", "message": "音频附件不存在或尚未就绪"})
    return row


def update_v4_draft(db: Session, task: VideoRepairTask, *, revision: int,
                    action: dict[str, Any], override_reason: str = "") -> VideoRepairTask:
    task = db.query(VideoRepairTask).filter_by(id=task.id, tenant_id=task.tenant_id).with_for_update().one()
    _validate_v4(task)
    if revision != task.draft_revision:
        raise _conflict("STALE_REPAIR_TASK", "草稿已变化，请刷新后重试")
    if task.status not in {"CONFIGURING", "AWAITING_MANUAL_DECISION", "AWAITING_MATERIAL",
                           "PREVIEW_READY", "FAILED"}:
        raise _conflict("INVALID_REPAIR_STATE", "当前状态不能修改草稿")
    if task.diagnosis_json and action.get("type") != task.diagnosis_json.get("recommended_action") \
            and not override_reason.strip():
        raise HTTPException(422, detail={
            "code": "OVERRIDE_REASON_REQUIRED", "message": "覆盖 AI 建议时必须填写原因"})
    action = dict(action)
    action_type = action.get("type")
    settings = get_settings()
    if action_type == "AUDIO_REPLACE" and not settings.video_repair_audio_replace_enabled:
        raise HTTPException(422, detail={"code": "ACTION_DISABLED", "message": "音频替换功能尚未启用"})
    if action_type == "CONTENT_INSERT" and not settings.video_repair_content_insert_enabled:
        raise HTTPException(422, detail={"code": "ACTION_DISABLED", "message": "内容插入功能尚未启用"})
    source = db.get(VideoAsset, task.source_asset_id)
    if not source:
        raise HTTPException(409, detail={"code": "SOURCE_MISSING", "message": "源成片已不可用"})
    if action_type == "VISUAL_REPLACE":
        material = _asset_ref(db, task, action.get("material") or {})
        action["material"] = {**action["material"], "content_hash": material.content_hash}
    elif action_type == "AUDIO_REPLACE":
        attachment = _attachment_ref(db, task, action.get("audio") or {})
        action["audio"] = {**action["audio"], "content_hash": attachment.content_hash}
    elif action_type == "CONTENT_INSERT":
        visual = _asset_ref(db, task, action.get("visual") or {})
        action["visual"] = {**action["visual"], "content_hash": visual.content_hash}
        if (action.get("audio") or {}).get("source_type") == "VIDEO_ASSET":
            audio_asset = _asset_ref(db, task, action["audio"])
            if not audio_asset.has_audio:
                raise HTTPException(422, detail={"code": "AUDIO_REQUIRED", "message": "所选素材没有可用原声"})
            action["audio"] = {**action["audio"], "content_hash": audio_asset.content_hash}
        else:
            attachment = _attachment_ref(db, task, action.get("audio") or {})
            action["audio"] = {**action["audio"], "content_hash": attachment.content_hash}
    try:
        task.draft_revision += 1
        task.repair_plan_json = build_plan(
            task_id=task.id, draft_revision=task.draft_revision,
            source_asset_id=task.source_asset_id, product_id=int(task.product_id or 0),
            action=action, diagnosis_snapshot=task.diagnosis_json,
            warnings=["烧录字幕不会随音频替换自动更新"] if action_type == "AUDIO_REPLACE" else [],
            generated_by={"override_reason": override_reason.strip()}, updated_by="USER")
    except InvalidRepairPlan as exc:
        raise HTTPException(422, detail={"code": "INVALID_REPAIR_PLAN", "message": str(exc)}) from exc
    normalized = task.repair_plan_json["action"]
    if action_type in {"VISUAL_REPLACE", "AUDIO_REPLACE"}:
        target = normalized["target_range"]
        if target["end_ms"] > round(float(source.duration_seconds) * 1000):
            raise HTTPException(422, detail={"code": "RANGE_OUT_OF_BOUNDS", "message": "修复时间段超过源成片时长"})
    if action_type == "VISUAL_REPLACE":
        material_range = normalized["material"]["source_range"]
        if material_range["end_ms"] > round(float(material.duration_seconds) * 1000):
            raise HTTPException(422, detail={"code": "RANGE_OUT_OF_BOUNDS", "message": "视觉素材时间段超过素材时长"})
    if action_type == "CONTENT_INSERT":
        if normalized["insert_at_ms"] > round(float(source.duration_seconds) * 1000):
            raise HTTPException(422, detail={"code": "RANGE_OUT_OF_BOUNDS", "message": "插入位置超过源成片时长"})
        visual_range = normalized["visual"]["source_range"]
        if visual_range["start_ms"] >= round(float(visual.duration_seconds) * 1000):
            raise HTTPException(422, detail={"code": "RANGE_OUT_OF_BOUNDS", "message": "视觉素材起点超过素材时长"})
    db.query(VideoRepairPreview).filter(
        VideoRepairPreview.task_id == task.id,
        VideoRepairPreview.status.in_(["QUEUED", "RENDERING", "READY"]),
    ).update({VideoRepairPreview.status: "SUPERSEDED"}, synchronize_session=False)
    task.status = "CONFIGURING"
    task.accepted_preview_id = None
    task.error_code = ""; task.error_message = ""
    db.commit(); db.refresh(task)
    return task


def create_preview(db: Session, task: VideoRepairTask) -> VideoRepairPreview:
    task = db.query(VideoRepairTask).filter_by(id=task.id, tenant_id=task.tenant_id).with_for_update().one()
    _validate_v4(task)
    if task.status != "CONFIGURING" or not task.repair_plan_json:
        raise _conflict("INVALID_REPAIR_STATE", "请先完成修复方案配置")
    hashed = plan_hash(task.repair_plan_json)
    existing = db.query(VideoRepairPreview).filter_by(
        task_id=task.id, draft_revision=task.draft_revision, plan_hash=hashed,
    ).filter(VideoRepairPreview.status.in_(["QUEUED", "RENDERING", "READY"])).first()
    if existing:
        return existing
    source = db.get(VideoAsset, task.source_asset_id)
    if not source:
        raise _conflict("SOURCE_MISSING", "源成片已不可用")
    preview = VideoRepairPreview(
        tenant_id=task.tenant_id, task_id=task.id, draft_revision=task.draft_revision,
        plan_hash=hashed, status="QUEUED", created_by=task.created_by,
        source_snapshot_json={"asset_id": source.id, "content_hash": source.content_hash,
                              "object_url": source.object_url},
        plan_snapshot_json=task.repair_plan_json,
    )
    db.add(preview); db.flush()
    db.add(VideoRepairJob(
        tenant_id=task.tenant_id, task_id=task.id, job_type="RENDER_PREVIEW", status="QUEUED",
        idempotency_key=f"preview:{task.id}:{task.draft_revision}:{hashed}",
        payload_json={"preview_id": preview.id}, max_attempts=1))
    transition(task, "PREVIEW_QUEUED")
    db.commit(); db.refresh(preview)
    from app.services.video_repair.worker_v4 import wake_video_repair_worker
    wake_video_repair_worker()
    return preview


def accept_preview(db: Session, task: VideoRepairTask, preview_id: int,
                   revision: int) -> VideoRepairTask:
    task = db.query(VideoRepairTask).filter_by(id=task.id, tenant_id=task.tenant_id).with_for_update().one()
    if task.status == "COMPLETED" and task.output_asset_id:
        return task
    _validate_v4(task)
    if revision != task.draft_revision:
        raise _conflict("STALE_REPAIR_TASK", "任务草稿已变化")
    preview = db.query(VideoRepairPreview).filter_by(
        id=preview_id, task_id=task.id, tenant_id=task.tenant_id).first()
    if not preview or preview.status != "READY" or preview.draft_revision != task.draft_revision:
        raise _conflict("PREVIEW_NOT_READY", "预览不存在、已过期或尚未完成")
    if plan_hash(task.repair_plan_json) != preview.plan_hash:
        raise _conflict("STALE_PREVIEW", "预览对应的方案已经变化")
    source = db.get(VideoAsset, task.source_asset_id)
    repair_input = db.get(VideoRepairInput, task.input_id)
    ecommerce_product_id = (repair_input.raw_payload_json or {}).get("ecommerce_product_id") if repair_input else None
    ecommerce_product = None
    if ecommerce_product_id is not None:
        ecommerce_product = db.query(EcommerceProduct).filter_by(
            id=ecommerce_product_id, tenant_id=task.tenant_id).first()
        if not ecommerce_product:
            raise _conflict("ECOMMERCE_PRODUCT_MISSING", "目标电商商品已不存在")
    output = VideoAsset(
        tenant_id=task.tenant_id, project_id=None, product_id=task.product_id,
        asset_id=f"asset-{uuid.uuid4().hex[:12]}",
        filename=f"{Path(source.filename).stem}-审核修复V2.mp4",
        source="review_repair_v4_output", library_type="repair_only" if ecommerce_product else "finished",
        asset_kind="final_output",
        generation_depth=source.generation_depth + 1,
        origin_asset_ids=list(dict.fromkeys((source.origin_asset_ids or []) + [source.asset_id])),
        reusable_status="review_required", burned_subtitles=source.burned_subtitles,
        has_background_music=source.has_background_music, object_url=preview.object_url,
        content_hash=preview.content_hash, duration_seconds=preview.duration_seconds,
        width=preview.width, height=preview.height, fps=source.fps or 30,
        has_audio=True, status="queued_for_analysis", excluded=False,
    )
    db.add(output); db.flush()
    inherit_public_tags_to_final(db, output, [source], task.created_by)
    if ecommerce_product:
        from app.services.storage import get_storage
        db.add(EcommerceVideoAsset(
            tenant_id=task.tenant_id, product_id=ecommerce_product.id,
            asset_id=f"asset-{uuid.uuid4().hex[:12]}", filename=output.filename,
            source=f"video_repair_task:{task.id}", library_type="finished",
            asset_kind="final_output", reusable_status="blocked",
            object_url=preview.object_url, storage_provider=get_storage().name,
            content_hash=preview.content_hash, duration_seconds=preview.duration_seconds,
            width=preview.width, height=preview.height, fps=source.fps or 30,
            has_audio=True, status="queued_for_analysis", excluded=False,
        ))
    task.accepted_preview_id = preview.id
    task.output_asset_id = output.id
    task.completed_at = datetime.utcnow()
    transition(task, "COMPLETED")
    db.commit(); db.refresh(task)
    enqueue_video_analysis()
    return task


def attachment_from_bytes(db: Session, task: VideoRepairTask, *, data: bytes, filename: str,
                          kind: str, source: str, object_url: str,
                          duration_ms: int = 0) -> VideoRepairAttachment:
    row = VideoRepairAttachment(
        tenant_id=task.tenant_id, task_id=task.id, kind=kind, source=source,
        object_url=object_url, filename=filename, content_hash=hashlib.sha256(data).hexdigest(),
        duration_ms=duration_ms, has_audio=kind in {"AUDIO", "VIDEO"},
        status="READY", created_by=task.created_by,
    )
    db.add(row); db.commit(); db.refresh(row)
    return row
