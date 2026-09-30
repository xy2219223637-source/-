from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.config import get_settings
from app.database import get_db
from app.models import VideoAsset, VideoAssetAnalysis, VideoRepairAttachment, VideoRepairJob, VideoRepairPreview, VideoRepairTask
from app.schemas.video_repair import (
    CandidateSelect, DraftPlanUpdate, RepairApprove, RepairTaskPage, RepairTaskRead,
    PreviewAccept, TTSCreate, V4DraftUpdate, VideoRepairTaskCreate,
)
from app.services.storage import get_storage
from app.services.tts import synthesize_speech
from app.services.video_repair.inputs import create_input
from app.services.video_repair.service import (
    approve_task, create_task, create_v4_task, owned_task, select_candidate, serialize_task,
    update_draft_plan,
)
from app.services.video_repair.rendering import render_task
from app.services.video_repair.state_machine_v4 import transition
from app.services.video_repair.v4_service import (
    accept_preview, attachment_from_bytes, create_preview, update_v4_draft,
)
from app.services.video_repair.worker_v4 import wake_video_repair_worker


router = APIRouter(tags=["视频修复"])


@router.get("/video-repair-assets")
def list_repair_assets(db: Session = Depends(get_db),
                       auth: AuthContext = Depends(current_auth)):
    rows = db.query(VideoAsset).filter_by(
        tenant_id=auth.tenant_id, asset_kind="final_output").order_by(
            VideoAsset.created_at.desc(), VideoAsset.id.desc()).all()
    return [{
        "id": row.id, "asset_id": row.asset_id, "filename": row.filename,
        "object_url": row.object_url, "duration_seconds": row.duration_seconds,
        "status": row.status, "created_at": row.created_at,
    } for row in rows]


@router.post("/video-repair-tasks", response_model=RepairTaskRead, status_code=201)
def create_repair(payload: VideoRepairTaskCreate,
                  db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    repair_input = create_input(
        db,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        source_asset_id=payload.source_asset_id,
        source_kind=payload.input.source_kind,
        audit_platform=payload.input.audit_platform,
        raw_reason=payload.input.raw_reason,
        expected_result=payload.input.expected_result,
        suggestions=payload.input.suggestions,
        time_ranges=[row.model_dump() for row in payload.input.time_ranges],
        raw_payload={"category_hint": payload.input.category_hint},
    )
    creator = create_v4_task if get_settings().video_repair_v4_enabled else create_task
    row = creator(
        db, tenant_id=auth.tenant_id, user_id=auth.user_id,
        source_asset_id=payload.source_asset_id, input_id=repair_input.id)
    return serialize_task(db, row)


@router.get("/video-repair-tasks", response_model=RepairTaskPage)
def list_repairs(status: str | None = Query(default=None),
                 source_asset_id: int | None = Query(default=None, gt=0),
                 page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=20, ge=1, le=100),
                 db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    query = db.query(VideoRepairTask).filter_by(
        tenant_id=auth.tenant_id, created_by=auth.user_id)
    if status:
        query = query.filter(VideoRepairTask.status == status)
    if source_asset_id:
        query = query.filter(VideoRepairTask.source_asset_id == source_asset_id)
    total = query.count()
    rows = query.order_by(VideoRepairTask.created_at.desc(), VideoRepairTask.id.desc()).offset(
        (page - 1) * page_size).limit(page_size).all()
    return {"items": [serialize_task(db, row) for row in rows],
            "page": page, "page_size": page_size, "total": total}


@router.get("/video-repair-tasks/{task_id}", response_model=RepairTaskRead)
def get_repair(task_id: int, db: Session = Depends(get_db),
               auth: AuthContext = Depends(current_auth)):
    return serialize_task(db, owned_task(db, auth.tenant_id, auth.user_id, task_id))


@router.post("/video-repair-tasks/{task_id}/candidates/select", response_model=RepairTaskRead)
def choose_candidate(task_id: int, payload: CandidateSelect,
                     db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = owned_task(db, auth.tenant_id, auth.user_id, task_id)
    return serialize_task(db, select_candidate(
        db, row, payload.action_id, payload.candidate_id, payload.override_reason))


@router.patch("/video-repair-tasks/{task_id}/draft-plan", response_model=RepairTaskRead)
def update_draft(task_id: int, payload: DraftPlanUpdate,
                 db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = owned_task(db, auth.tenant_id, auth.user_id, task_id)
    return serialize_task(db, update_draft_plan(
        db, row, payload.updated_at, payload.action_type,
        payload.target_range.model_dump(), payload.selected_candidate_id))


@router.post("/video-repair-tasks/{task_id}/approve", response_model=RepairTaskRead)
def approve(task_id: int, payload: RepairApprove,
            db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = owned_task(db, auth.tenant_id, auth.user_id, task_id)
    return serialize_task(db, approve_task(db, row, payload.updated_at))


@router.post("/video-repair-tasks/{task_id}/render", response_model=RepairTaskRead)
async def render(task_id: int, db: Session = Depends(get_db),
                 auth: AuthContext = Depends(current_auth)):
    row = owned_task(db, auth.tenant_id, auth.user_id, task_id)
    return serialize_task(db, await render_task(db, row))


def _preview_json(row: VideoRepairPreview) -> dict:
    return {
        "id": row.id, "task_id": row.task_id, "draft_revision": row.draft_revision,
        "plan_hash": row.plan_hash, "status": row.status, "object_url": row.object_url,
        "content_hash": row.content_hash, "duration_seconds": row.duration_seconds,
        "width": row.width, "height": row.height, "error_code": row.error_code,
        "error_message": row.error_message, "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


def _v4_task(db: Session, auth: AuthContext, task_id: int) -> VideoRepairTask:
    row = owned_task(db, auth.tenant_id, auth.user_id, task_id)
    if row.schema_version != "video_repair_task.v2":
        raise HTTPException(409, detail={"code": "LEGACY_REPAIR_TASK", "message": "该接口仅用于 V4 返修任务"})
    return row


@router.patch("/video-repair-tasks/{task_id}/draft", response_model=RepairTaskRead)
def update_v4_task_draft(task_id: int, payload: V4DraftUpdate,
                         db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = _v4_task(db, auth, task_id)
    return serialize_task(db, update_v4_draft(
        db, row, revision=payload.draft_revision, action=payload.action,
        override_reason=payload.override_reason))


@router.get("/video-repair-tasks/{task_id}/materials")
def list_v4_materials(task_id: int, db: Session = Depends(get_db),
                      auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    rows = db.query(VideoAsset).filter_by(
        tenant_id=task.tenant_id, product_id=task.product_id, excluded=False).filter(
        VideoAsset.id != task.source_asset_id).order_by(VideoAsset.created_at.desc()).all()
    candidate_by_asset = {}
    for candidate in (task.diagnosis_json or {}).get("material_candidates", []):
        candidate_by_asset.setdefault(candidate.get("source_asset_id"), candidate)
    summaries = {}
    if rows:
        analyses = db.query(VideoAssetAnalysis).filter(
            VideoAssetAnalysis.tenant_id == task.tenant_id,
            VideoAssetAnalysis.asset_id.in_([row.id for row in rows]),
            VideoAssetAnalysis.status == "completed",
        ).order_by(VideoAssetAnalysis.completed_at.desc(), VideoAssetAnalysis.id.desc()).all()
        for analysis in analyses:
            summaries.setdefault(analysis.asset_id, analysis.summary)
    return [{
        "id": row.id, "asset_id": row.asset_id, "filename": row.filename,
        "object_url": row.object_url, "content_hash": row.content_hash,
        "duration_seconds": row.duration_seconds, "width": row.width, "height": row.height,
        "has_audio": row.has_audio, "asset_kind": row.asset_kind,
        "summary": summaries.get(row.id, ""),
        "recommended_range": (candidate_by_asset.get(row.id) or {}).get("source_range"),
        "description": (candidate_by_asset.get(row.id) or {}).get("description", ""),
        "content_role": (candidate_by_asset.get(row.id) or {}).get("content_role", ""),
        "score": (candidate_by_asset.get(row.id) or {}).get("score"),
        "recommendation_level": (candidate_by_asset.get(row.id) or {}).get("recommendation_level", ""),
        "reasons": (candidate_by_asset.get(row.id) or {}).get("reasons", []),
        "warnings": (candidate_by_asset.get(row.id) or {}).get("warnings", []),
    } for row in rows]


@router.post("/video-repair-tasks/{task_id}/attachments", status_code=201)
async def upload_v4_attachment(task_id: int, file: UploadFile = File(...),
                               db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    data = await file.read()
    limit = get_settings().director_video_max_upload_mb * 1024 * 1024
    if not data:
        raise HTTPException(422, "附件不能为空")
    if len(data) > limit:
        raise HTTPException(413, f"附件不能超过 {get_settings().director_video_max_upload_mb}MB")
    content_type = (file.content_type or "").lower()
    kind = "AUDIO" if content_type.startswith("audio/") else "VIDEO" if content_type.startswith("video/") else "IMAGE" if content_type.startswith("image/") else ""
    if not kind:
        raise HTTPException(415, "仅支持音频、视频或图片附件")
    filename = Path(file.filename or f"attachment-{task.id}").name
    url = await get_storage().upload(data, filename, f"video-repair/{task.id}/attachments")
    row = attachment_from_bytes(
        db, task, data=data, filename=filename, kind=kind, source="UPLOAD", object_url=url)
    return {"id": row.id, "kind": row.kind, "source": row.source, "filename": row.filename,
            "object_url": row.object_url, "content_hash": row.content_hash,
            "duration_ms": row.duration_ms, "status": row.status}


@router.post("/video-repair-tasks/{task_id}/tts", status_code=201)
async def create_v4_tts(task_id: int, payload: TTSCreate,
                        db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    settings = get_settings()
    if not settings.doubao_tts_api_key:
        raise HTTPException(status_code=503, detail={
            "code": "TTS_NOT_CONFIGURED",
            "message": "语音生成服务尚未配置，请先上传音频，或由管理员配置豆包 TTS API Key",
        })
    with tempfile.TemporaryDirectory(prefix=f"repair-tts-{task.id}-") as name:
        output = Path(name) / "speech.mp3"
        try:
            await asyncio.to_thread(synthesize_speech, payload.text, output, payload.voice or None)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={
                "code": "TTS_PROVIDER_FAILED",
                "message": f"语音生成服务调用失败：{str(exc)[:500]}",
            }) from exc
        data = output.read_bytes()
        url = await get_storage().upload(data, output.name, f"video-repair/{task.id}/attachments")
    row = attachment_from_bytes(
        db, task, data=data, filename="speech.mp3", kind="AUDIO", source="TTS",
        object_url=url)
    row.metadata_json = {"text": payload.text, "voice": payload.voice}
    db.commit(); db.refresh(row)
    return {"id": row.id, "kind": row.kind, "source": row.source,
            "object_url": row.object_url, "content_hash": row.content_hash, "status": row.status}


@router.post("/video-repair-tasks/{task_id}/previews", status_code=202)
def create_v4_preview(task_id: int, db: Session = Depends(get_db),
                      auth: AuthContext = Depends(current_auth)):
    return _preview_json(create_preview(db, _v4_task(db, auth, task_id)))


@router.get("/video-repair-tasks/{task_id}/previews")
def list_v4_previews(task_id: int, db: Session = Depends(get_db),
                     auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    rows = db.query(VideoRepairPreview).filter_by(
        tenant_id=task.tenant_id, task_id=task.id).order_by(
        VideoRepairPreview.created_at.desc(), VideoRepairPreview.id.desc()).all()
    return [_preview_json(row) for row in rows]


@router.get("/video-repair-tasks/{task_id}/previews/{preview_id}")
def get_v4_preview(task_id: int, preview_id: int, db: Session = Depends(get_db),
                   auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    row = db.query(VideoRepairPreview).filter_by(
        tenant_id=task.tenant_id, task_id=task.id, id=preview_id).first()
    if not row:
        raise HTTPException(404, "预览不存在")
    return _preview_json(row)


@router.post("/video-repair-tasks/{task_id}/previews/{preview_id}/accept", response_model=RepairTaskRead)
def accept_v4_preview(task_id: int, preview_id: int, payload: PreviewAccept,
                      db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    return serialize_task(db, accept_preview(
        db, task, preview_id=preview_id, revision=payload.draft_revision))


@router.post("/video-repair-tasks/{task_id}/previews/{preview_id}/retry", status_code=202)
def retry_v4_preview(task_id: int, preview_id: int, db: Session = Depends(get_db),
                     auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    preview = db.query(VideoRepairPreview).filter_by(
        tenant_id=task.tenant_id, task_id=task.id, id=preview_id, status="FAILED").first()
    if not preview or preview.draft_revision != task.draft_revision:
        raise HTTPException(409, "只有当前草稿的失败预览可以重试")
    preview.status = "QUEUED"; preview.error_code = preview.error_message = ""
    task.status = "PREVIEW_QUEUED"
    count = db.query(VideoRepairJob).filter_by(task_id=task.id, job_type="RENDER_PREVIEW").count()
    db.add(VideoRepairJob(
        tenant_id=task.tenant_id, task_id=task.id, job_type="RENDER_PREVIEW", status="QUEUED",
        idempotency_key=f"preview-retry:{task.id}:{preview.id}:{count + 1}",
        payload_json={"preview_id": preview.id}, max_attempts=1))
    db.commit(); wake_video_repair_worker()
    return _preview_json(preview)


@router.post("/video-repair-tasks/{task_id}/retry-analysis", response_model=RepairTaskRead)
def retry_v4_analysis(task_id: int, db: Session = Depends(get_db),
                      auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    if task.status not in {"FAILED", "AWAITING_MANUAL_DECISION"}:
        raise HTTPException(409, "当前任务不能重试分析")
    if task.status == "FAILED":
        transition(task, "ANALYZING")
    else:
        task.status = "ANALYZING"
    task.error_code = task.error_message = ""
    db.add(VideoRepairJob(
        tenant_id=task.tenant_id, task_id=task.id, job_type="ANALYZE", status="QUEUED",
        idempotency_key=f"analyze-retry:{task.id}:{datetime.utcnow().timestamp()}",
        payload_json={"input_id": task.input_id, "source_asset_id": task.source_asset_id},
        max_attempts=2))
    db.commit(); db.refresh(task); wake_video_repair_worker()
    return serialize_task(db, task)


@router.post("/video-repair-tasks/{task_id}/cancel", response_model=RepairTaskRead)
def cancel_v4_task(task_id: int, reason: str = Query(default="用户取消", max_length=1000),
                   db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    transition(task, "CANCELED")
    task.cancel_reason = reason
    db.query(VideoRepairJob).filter_by(task_id=task.id, status="QUEUED").update(
        {VideoRepairJob.status: "CANCELED", VideoRepairJob.finished_at: datetime.utcnow()},
        synchronize_session=False)
    db.commit(); db.refresh(task)
    return serialize_task(db, task)
