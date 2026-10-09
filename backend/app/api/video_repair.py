from __future__ import annotations

import asyncio
import hashlib
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.config import get_settings
from app.database import get_db
from app.models import Tenant, VideoAsset, VideoAssetAnalysis, VideoRepairAttachment, VideoRepairJob, VideoRepairPreview, VideoRepairTask
from app.schemas.video_repair import (
    RepairTaskPage, RepairTaskRead,
    PreviewAccept, TTSCreate, V4DraftUpdate, VideoRepairTaskCreate,
)
from app.services.storage import get_storage, get_video_storage
from app.services.director_workflow import _ffmpeg_executable, _probe_media
from app.services.video_analysis_queue import enqueue_video_analysis
from app.services.tts import synthesize_speech
from app.services.video_repair.inputs import create_input
from app.services.video_repair.service import (
    create_v4_task, owned_task, serialize_task,
)
from app.services.video_repair.state_machine_v4 import transition
from app.services.video_repair.v4_service import (
    accept_preview, attachment_from_bytes, create_preview, update_v4_draft,
)
from app.services.video_repair.worker_v4 import wake_video_repair_worker


router = APIRouter(tags=["视频修复"])


def _probe_repair_video(path: Path) -> dict:
    """Read upload metadata even when the host has FFmpeg but no FFprobe."""
    media = _probe_media(path)
    has_audio = media["has_audio"]
    if has_audio is None:
        result = subprocess.run(
            [_ffmpeg_executable(), "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        has_audio = any(
            "Stream #" in line and "Audio:" in line
            for line in result.stderr.splitlines()
        )
    return {
        "duration": media["duration_seconds"], "width": media["width"],
        "height": media["height"], "fps": media["fps"],
        "has_audio": bool(has_audio),
    }


@router.post("/video-repair-assets/upload", status_code=201)
async def upload_repair_source(
    material_library_product_id: Annotated[int, Form()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth),
):
    settings = get_settings()
    if not settings.video_repair_v4_enabled or not settings.video_repair_async_worker_enabled:
        raise HTTPException(503, "素材库返修需要启用 V4 分析工作台及后台任务")
    from app.services.material_library_repair import ensure_repair_product
    product = ensure_repair_product(
        db, tenant_id=auth.tenant_id, library_product_id=material_library_product_id)
    if not product:
        raise HTTPException(404, "商品不存在或无权访问")
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise HTTPException(415, "请上传 MP4、MOV、MKV 或 WebM 视频")
    raw = await file.read()
    limit_mb = get_settings().director_video_max_upload_mb
    if not raw or len(raw) > limit_mb * 1024 * 1024:
        raise HTTPException(413 if raw else 422, f"视频不能为空且不能超过 {limit_mb} MB")
    digest = hashlib.sha256(raw).hexdigest()
    existing = db.query(VideoAsset).filter_by(
        tenant_id=auth.tenant_id, product_id=product.id,
        asset_kind="final_output", source="video_repair_upload", content_hash=digest,
    ).first()
    if existing:
        return {"id": existing.id, "filename": existing.filename,
                "duration_seconds": existing.duration_seconds, "product_id": existing.product_id}
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"source{suffix}"
            path.write_bytes(raw)
            media = _probe_repair_video(path)
    except Exception as exc:
        raise HTTPException(422, "无法读取视频，请检查文件是否完整") from exc
    duration = float(media["duration"])
    if duration <= 0 or duration > get_settings().video_library_max_duration_seconds + .05:
        raise HTTPException(422, "视频时长无效或超出上传限制")
    tenant = db.query(Tenant).filter_by(id=auth.tenant_id).first()
    storage = get_video_storage(auth.tenant_id, tenant.code if tenant else "")
    url = await storage.upload(raw, filename, f"video-repair/{product.id}/source")
    asset = VideoAsset(
        tenant_id=auth.tenant_id, project_id=None, product_id=product.id,
        asset_id=f"asset-{uuid.uuid4().hex[:12]}", filename=filename,
        source="video_repair_upload", library_type="finished", asset_kind="final_output",
        reusable_status="review_required", object_url=url, storage_provider=storage.name,
        content_hash=digest, duration_seconds=duration, width=media["width"],
        height=media["height"], fps=media["fps"], has_audio=media["has_audio"],
        status="queued_for_analysis", excluded=False,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    enqueue_video_analysis()
    return {"id": asset.id, "filename": asset.filename,
            "duration_seconds": asset.duration_seconds, "product_id": asset.product_id}


@router.post("/video-repair-tasks", response_model=RepairTaskRead, status_code=201)
def create_repair(payload: VideoRepairTaskCreate,
                  db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    settings = get_settings()
    if not settings.video_repair_v4_enabled or not settings.video_repair_async_worker_enabled:
        raise HTTPException(503, "返修需要启用 V4 分析工作台及后台任务")
    from app.services.material_library_repair import ensure_repair_product
    linked_product = ensure_repair_product(
        db, tenant_id=auth.tenant_id, library_product_id=payload.material_library_product_id)
    source = db.query(VideoAsset).filter_by(
        id=payload.source_asset_id, tenant_id=auth.tenant_id,
        product_id=linked_product.id, asset_kind="final_output").first()
    if not source:
        raise HTTPException(422, "原视频与所选素材库商品不匹配")
    repair_input = create_input(
        db,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        source_asset_id=payload.source_asset_id,
        source_kind="MANUAL",
        audit_platform="INTERNAL",
        raw_reason=payload.input.raw_reason,
        suggestions=payload.input.suggestions,
        time_ranges=[],
        raw_payload={"material_library_product_id": payload.material_library_product_id},
    )
    row = create_v4_task(
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


def _validate_ai_draft(diagnosis: dict, action: dict, override_reason: str) -> None:
    proposal = (diagnosis or {}).get("ai_proposal") or {}
    template = proposal.get("action") or proposal.get("action_template") or {}
    if override_reason.strip() or not template.get("type"):
        raise HTTPException(422, detail={
            "code": "AI_PROPOSAL_REQUIRED", "message": "当前仅支持接受 AI 方案或要求重新分析"})
    allowed = set()
    required = set(proposal.get("requires_input") or [])
    if "insert_position" in required and template["type"] == "CONTENT_INSERT":
        allowed.add("insert_at_ms")
    if "audio" in required and template["type"] in {"AUDIO_REPLACE", "CONTENT_INSERT"}:
        allowed.add("audio")
        if template["type"] == "AUDIO_REPLACE":
            allowed.update({"fit_policy", "fade_in_ms", "fade_out_ms", "subtitle_policy"})
    if "visual_material" in required:
        allowed.add("material" if template["type"] == "VISUAL_REPLACE" else "visual")
    if any(action.get(key) != value for key, value in template.items() if key not in allowed) \
            or any(key not in template and key not in allowed for key in action):
        raise HTTPException(422, detail={
            "code": "AI_PROPOSAL_MISMATCH", "message": "不能修改 AI 方案；请要求 AI 重新分析"})


@router.patch("/video-repair-tasks/{task_id}/draft", response_model=RepairTaskRead)
def update_v4_task_draft(task_id: int, payload: V4DraftUpdate,
                         db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = _v4_task(db, auth, task_id)
    _validate_ai_draft(row.diagnosis_json or {}, payload.action, payload.override_reason)
    return serialize_task(db, update_v4_draft(
        db, row, revision=payload.draft_revision, action=payload.action,
        override_reason=payload.override_reason))


@router.get("/video-repair-tasks/{task_id}/materials")
def list_v4_materials(task_id: int, db: Session = Depends(get_db),
                      auth: AuthContext = Depends(current_auth)):
    task = _v4_task(db, auth, task_id)
    from app.services.material_library_repair import library_product_for_repair, sync_repair_videos
    library_mode = bool(library_product_for_repair(
        db, tenant_id=task.tenant_id, repair_product_id=task.product_id))
    if library_mode:
        sync_repair_videos(db, tenant_id=task.tenant_id, repair_product_id=task.product_id)
    query = db.query(VideoAsset).filter_by(
        tenant_id=task.tenant_id, product_id=task.product_id, excluded=False).filter(
        VideoAsset.id != task.source_asset_id)
    if library_mode:
        query = query.filter(VideoAsset.library_type == "repair_only", VideoAsset.asset_kind == "material")
    rows = query.order_by(VideoAsset.created_at.desc()).all()
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
        "has_audio": row.has_audio, "asset_kind": row.asset_kind, "analysis_status": row.status,
        "summary": summaries.get(row.id, ""),
        "recommended_range": (candidate_by_asset.get(row.id) or {}).get("source_range"),
        "description": (candidate_by_asset.get(row.id) or {}).get("description", row.filename if library_mode else ""),
        "content_role": (candidate_by_asset.get(row.id) or {}).get("content_role", ""),
        "score": (candidate_by_asset.get(row.id) or {}).get("score"),
        "recommendation_level": (candidate_by_asset.get(row.id) or {}).get("recommendation_level", ""),
        "reasons": (candidate_by_asset.get(row.id) or {}).get("reasons", []),
        "audio_mode": (candidate_by_asset.get(row.id) or {}).get("audio_mode", "needs_review"),
        "warnings": (candidate_by_asset.get(row.id) or {}).get("warnings", ["分析尚未完成或没有匹配本次修改的片段，请人工核对"] if library_mode else []),
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
    task.error_code = task.error_message = ""
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
    if task.status not in {"FAILED", "AWAITING_MANUAL_DECISION", "CONFIGURING"}:
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
