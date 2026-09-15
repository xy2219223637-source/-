"""Director inspiration projects based on reference videos or product briefs."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import unquote, urlparse

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.config import get_settings
from app.database import get_db
from app.models import DirectorAsset, DirectorCaptureSession, DirectorProject, DirectorSection, DirectorShot, ShootingTask
from app.services.director_workflow import (
    SECTION_TEMPLATES,
    _primary_product_name,
    _probe_media,
    analyze_reference_project,
    mix_silent_preview,
    render_ai_material_video,
)
from app.services.image_generation import get_image_provider
from app.services.llm import LLMClient
from app.services.storage import get_storage
from app.services.video_capture import (
    VIDEO_SUFFIXES,
    capture_filename,
    capture_platform,
    capture_token_hash,
    capture_token_matches,
    new_capture_token,
)

router = APIRouter(prefix="/director-projects", tags=["director-projects"])
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class DirectorUrlCreate(BaseModel):
    url: HttpUrl
    name: str = Field(default="", max_length=256)


class DirectorCaptureCreate(BaseModel):
    url: HttpUrl
    name: str = Field(default="", max_length=256)
    project_id: Optional[int] = Field(default=None, ge=1)


class CaptureAttempt(BaseModel):
    source: str = Field(default="", max_length=64, pattern=r"^[a-zA-Z0-9_.-]*$")
    context: Literal["page", "extension"]
    resource_host: str = Field(default="", max_length=253, pattern=r"^[a-zA-Z0-9.:-]*$")
    url_key: str = Field(default="", max_length=16, pattern=r"^[a-f0-9]*$")
    code: str = Field(default="", max_length=64, pattern=r"^[a-z_]*$")
    status: int = Field(default=0, ge=0, le=599)
    mime: str = Field(default="", max_length=80)
    size: int = Field(default=0, ge=0)
    body_kind: str = Field(default="", max_length=16)


class CaptureDiagnostics(BaseModel):
    version: str = Field(default="", max_length=32)
    build: str = Field(default="", max_length=16, pattern=r"^[a-f0-9]*$")
    browser: str = Field(default="", max_length=256)
    target_video_id: str = Field(default="", max_length=24, pattern=r"^\d*$")
    candidate_count: int = Field(default=0, ge=0, le=100)
    last_code: str = Field(default="", max_length=64, pattern=r"^[a-z_]*$")
    attempts: list[CaptureAttempt] = Field(default_factory=list, max_length=24)


class DirectorCaptureStatusUpdate(BaseModel):
    stage: Literal["opening_source", "locating_video", "downloading", "uploading"]
    diagnostics: Optional[CaptureDiagnostics] = None


class DirectorCaptureFailure(BaseModel):
    message: str = Field(default="浏览器插件未能获取视频", max_length=500)
    diagnostics: Optional[CaptureDiagnostics] = None


class DirectorOriginalCreate(BaseModel):
    name: str = ""
    brief: str = Field(min_length=2, max_length=4000)
    language: str = "中文"


class DirectorRewriteRequest(BaseModel):
    product_service: str = Field(default="", max_length=256)
    selling_points: str = Field(default="", max_length=4000)
    language: str = Field(default="中文", max_length=32)


class DirectorAnalysisUpdate(BaseModel):
    product_name: str = Field(default="", max_length=256)
    selling_points: list[str] = Field(default_factory=list)
    target_audience: list[str] = Field(default_factory=list)
    target_pain_points: str = Field(default="", max_length=4000)


class DirectorSectionUpdate(BaseModel):
    id: int
    sort_order: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=128)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    script_lines: list[dict[str, Any]] = Field(default_factory=list)
    description: str = ""
    shooting_notes: str = ""


class DirectorSectionRewrite(BaseModel):
    instruction: str = Field(default="表达更自然, 更适合短视频", max_length=1000)


class DirectorGenerateAsset(BaseModel):
    prompt: str = Field(default="", max_length=2000)


class DirectorShotUpdate(BaseModel):
    id: int
    transcript: str = Field(default="", max_length=8000)
    subject: str = Field(default="", max_length=4000)
    action: str = Field(default="", max_length=4000)
    scene: str = Field(default="", max_length=4000)
    shot_size: str = Field(default="", max_length=64)
    camera: str = Field(default="", max_length=4000)
    product: str = Field(default="", max_length=1000)
    director_intent: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=8000)


class ShootingTaskCreate(BaseModel):
    channel_id: Literal["feishu"] = "feishu"
    department_id: Literal["shooting-department-1"] = "shooting-department-1"
    assignee_id: Literal["zhang-san"] = "zhang-san"
    note: str = Field(default="", max_length=2000)


SIMULATED_SHOOTING_DIRECTORY = [
    {
        "id": "feishu",
        "name": "飞书",
        "departments": [
            {
                "id": "shooting-department-1",
                "name": "拍摄部1",
                "members": [{"id": "zhang-san", "name": "张三"}],
            }
        ],
    }
]


def _project(db: Session, tenant_id: str, project_id: int) -> DirectorProject:
    row = db.query(DirectorProject).filter_by(id=project_id, tenant_id=tenant_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="编导项目不存在")
    return row


def _capture_session(db: Session, capture_id: str, token: str) -> DirectorCaptureSession:
    row = db.query(DirectorCaptureSession).filter_by(id=capture_id).first()
    if not row or not token or not capture_token_matches(token, row.token_hash):
        raise HTTPException(status_code=401, detail="采集凭证无效")
    # Match retry's lock order, and reload state after waiting for a concurrent request.
    project = db.query(DirectorProject).filter_by(id=row.project_id, tenant_id=row.tenant_id).populate_existing().with_for_update().first()
    row = db.query(DirectorCaptureSession).filter_by(id=capture_id).populate_existing().with_for_update().first()
    if row.status in {"superseded", "failed"}:
        raise HTTPException(status_code=409, detail="采集任务已结束, 请重新采集")
    if row.status == "completed":
        return row
    if row.expires_at < _utcnow():
        row.status = "expired"
        if project and project.status == "capturing":
            project.status = "failed"
            project.progress_stage = "capture_expired"
            project.error_message = "采集任务已过期, 请重新提交链接"
        db.commit()
        raise HTTPException(status_code=410, detail="采集任务已过期, 请重新提交链接")
    return row


def _set_capture_failed(db: Session, capture: DirectorCaptureSession, project: DirectorProject, message: str, diagnostics: Optional[CaptureDiagnostics] = None) -> None:
    db.refresh(project, with_for_update=True)
    db.refresh(capture, with_for_update=True)
    if capture.status in {"completed", "superseded", "expired"}:
        return
    clean_message = message.strip()[:500] or "浏览器插件未能获取视频"
    capture.status = "failed"
    capture.details = {**(capture.details or {}), "error": clean_message}
    if diagnostics:
        capture.details = {**capture.details, "diagnostics": diagnostics.model_dump()}
    project.status = "failed"
    project.progress_stage = "capture_failed"
    project.error_message = clean_message
    db.commit()
    logger.warning("Capture failed project=%s session=%s diagnostics=%s", project.id, capture.id, (capture.details or {}).get("diagnostics", {}))


def _asset_out(row: DirectorAsset) -> dict[str, Any]:
    return {
        "id": row.id,
        "section_id": row.section_id,
        "role": row.role,
        "media_kind": row.media_kind,
        "source": row.source,
        "url": row.url,
        "thumbnail_url": row.thumbnail_url,
        "description": row.description,
        "duration_seconds": row.duration_seconds,
        "sort_order": row.sort_order,
    }


def _shot_out(row: DirectorShot) -> dict[str, Any]:
    analysis = row.analysis if isinstance(row.analysis, dict) else {}
    return {
        "id": row.id,
        "section_id": row.section_id,
        "sort_order": row.sort_order,
        "start_ms": row.start_ms,
        "end_ms": row.end_ms,
        "transcript": row.transcript,
        "story_role": row.story_role,
        "subject": row.subject,
        "action": row.action,
        "scene": row.scene,
        "shot_size": row.shot_size,
        "camera": row.camera,
        "product": str(analysis.get("product") or ""),
        "props": str(analysis.get("props") or ""),
        "emotion": str(analysis.get("emotion") or ""),
        "director_intent": str(analysis.get("director_intent") or ""),
        "description": row.description,
        "clip_url": row.clip_url,
        "thumbnail_url": row.thumbnail_url,
        "confidence": row.confidence,
        "analysis": analysis,
    }


def _serialize(db: Session, row: DirectorProject) -> dict[str, Any]:
    if row.status == "capturing":
        db.refresh(row, with_for_update=True)
        latest = db.query(DirectorCaptureSession).filter_by(project_id=row.id).order_by(DirectorCaptureSession.created_at.desc()).with_for_update().first()
        if row.status == "capturing" and latest and latest.expires_at < _utcnow() and latest.status in {"waiting", "active", "uploading"}:
            latest.status = "expired"
            row.status = "failed"
            row.progress_stage = "capture_expired"
            row.error_message = "采集任务已超时, 请重新采集"
            db.commit()
    sections = db.query(DirectorSection).filter_by(project_id=row.id).order_by(DirectorSection.sort_order).all()
    section_ids = [section.id for section in sections]
    assets = (
        db.query(DirectorAsset)
        .filter(DirectorAsset.section_id.in_(section_ids))
        .order_by(DirectorAsset.sort_order)
        .all()
        if section_ids
        else []
    )
    by_section: dict[int, list[DirectorAsset]] = {}
    for asset in assets:
        by_section.setdefault(asset.section_id, []).append(asset)
    shots = (
        db.query(DirectorShot)
        .filter(DirectorShot.section_id.in_(section_ids))
        .order_by(DirectorShot.sort_order)
        .all()
        if section_ids
        else []
    )
    shots_by_section: dict[int, list[DirectorShot]] = {}
    for shot in shots:
        shots_by_section.setdefault(shot.section_id, []).append(shot)
    return {
        "id": row.id,
        "product_id": row.product_id,
        "name": row.name,
        "mode": row.mode,
        "source_kind": row.source_kind,
        "source_url": row.source_url,
        "source_filename": row.source_filename,
        "source_duration_seconds": row.source_duration_seconds,
        "language": row.language,
        "product_service": row.product_service,
        "selling_points": row.selling_points,
        "status": row.status,
        "progress_stage": row.progress_stage,
        "analysis": row.analysis or {},
        "output_url": row.output_url,
        "error_message": row.error_message,
        "product": None,
        "sections": [
            {
                "id": section.id,
                "sort_order": section.sort_order,
                "title": section.title,
                "start_ms": section.start_ms,
                "end_ms": section.end_ms,
                "source_text": section.source_text,
                "script_lines": section.script_lines or [],
                "description": section.description,
                "shooting_notes": section.shooting_notes,
                "assets": [_asset_out(asset) for asset in by_section.get(section.id, [])],
                "shots": [_shot_out(shot) for shot in shots_by_section.get(section.id, [])],
            }
            for section in sections
        ],
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _shooting_task_out(db: Session, row: ShootingTask) -> dict[str, Any]:
    project = db.query(DirectorProject).filter_by(id=row.project_id, tenant_id=row.tenant_id).first()
    return {
        "id": row.id,
        "project_id": row.project_id,
        "project_name": project.name if project else "已删除的脚本",
        "project_mode": project.mode if project else "original",
        "section_count": db.query(DirectorSection).filter_by(project_id=row.project_id).count() if project else 0,
        "channel_id": row.channel_id,
        "channel_name": row.channel_name,
        "department_id": row.department_id,
        "department_name": row.department_name,
        "assignee_id": row.assignee_id,
        "assignee_name": row.assignee_name,
        "note": row.note,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _fallback_line(title: str, product_name: str, selling_points: str) -> str:
    point = selling_points.strip().replace("\n", " ")[:80] or "使用方便, 信息清晰, 适合日常场景"
    templates = {
        "使用体验引入": f"最近在用{product_name}, 第一感觉就是很适合放进日常流程里.",
        "痛点与需求": f"如果你也在挑选更省心的同类产品, 可以重点看看这几个细节.",
        "产品解决方案": f"{product_name}把使用步骤和核心信息做得更直接, 不需要复杂操作.",
        "核心卖点介绍": f"{product_name}值得关注的地方是{point}.",
        "使用方法演示": f"按商品说明完成取用和使用, 画面重点展示实际操作和产品细节.",
        "体验与证据": f"体验是否适合自己, 建议结合商品信息和真实使用场景判断.",
        "行动引导": f"想进一步了解{product_name}, 可以先查看完整商品资料.",
    }
    return templates.get(title, f"围绕{product_name}说明{point}.")


def _new_line(text: str) -> dict[str, str]:
    return {"id": uuid.uuid4().hex, "text": text.strip()}


async def _generate_original_sections(brief: str, language: str) -> list[dict[str, Any]]:
    product_name = "用户描述的产品或服务"
    selling_points = brief
    result: dict[str, Any] = {}
    if not get_settings().llm_mock_mode:
        prompt = f"""为短视频创建编导脚本, 只返回 JSON.
创作需求: {brief}; 输出语言: {language}.
sections 为 4 到 10 项, 每项包含 title, lines, description, shooting_notes, duration_seconds.
lines 是台词字符串数组. 不生成配音或音频字段, 不虚构商品功效."""
        result = await LLMClient().chat_json([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=5000)
    raw = result.get("sections") if isinstance(result, dict) else None
    if not isinstance(raw, list) or len(raw) < 2:
        return [
            {
                "title": title,
                "lines": [_fallback_line(title, product_name, selling_points)],
                "description": description,
                "shooting_notes": description,
                "duration_seconds": 4,
            }
            for title, description in SECTION_TEMPLATES
        ]
    return [item for item in raw[:16] if isinstance(item, dict)]


@router.get("")
def list_projects(db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    rows = db.query(DirectorProject).filter_by(tenant_id=auth.tenant_id).order_by(DirectorProject.updated_at.desc()).all()
    return [_serialize(db, row) for row in rows]


@router.get("/shooting-directory")
def shooting_directory(auth: AuthContext = Depends(current_auth)):
    return SIMULATED_SHOOTING_DIRECTORY


@router.get("/shooting-tasks")
def list_shooting_tasks(db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    rows = (
        db.query(ShootingTask)
        .filter_by(tenant_id=auth.tenant_id)
        .order_by(ShootingTask.created_at.desc())
        .all()
    )
    return [_shooting_task_out(db, row) for row in rows]


@router.post("/{project_id}/shooting-tasks")
def create_shooting_task(
    project_id: int,
    payload: ShootingTaskCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    project = _project(db, auth.tenant_id, project_id)
    if not db.query(DirectorSection).filter_by(project_id=project.id).first():
        raise HTTPException(status_code=409, detail="脚本暂无可推送的内容")
    existing = (
        db.query(ShootingTask)
        .filter_by(
            tenant_id=auth.tenant_id,
            project_id=project.id,
            assignee_id=payload.assignee_id,
            status="pending",
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="该脚本已推送给张三, 请勿重复推送")
    row = ShootingTask(
        tenant_id=auth.tenant_id,
        project_id=project.id,
        channel_id="feishu",
        channel_name="飞书",
        department_id="shooting-department-1",
        department_name="拍摄部1",
        assignee_id="zhang-san",
        assignee_name="张三",
        note=payload.note.strip(),
        status="pending",
        created_by=auth.user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _shooting_task_out(db, row)


@router.post("/upload")
async def upload_reference(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    name: str = Form(""),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in VIDEO_SUFFIXES or not (file.content_type or "").startswith("video/"):
        raise HTTPException(status_code=415, detail="仅支持 MP4, MOV, M4V 或 WEBM 视频")
    max_bytes = get_settings().director_video_max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"视频不能超过 {get_settings().director_video_max_upload_mb}MB")
    if not data:
        raise HTTPException(status_code=400, detail="视频文件为空")
    url = await get_storage().upload(data, file.filename or f"reference{suffix}", "director/sources")
    row = DirectorProject(
        tenant_id=auth.tenant_id,
        name=name.strip() or Path(file.filename or "参考视频").stem,
        mode="reference",
        source_kind="upload",
        source_url=url,
        source_filename=file.filename or "reference.mp4",
        status="queued",
        progress_stage="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    background_tasks.add_task(analyze_reference_project, row.id)
    return _serialize(db, row)


@router.post("/from-url")
def create_from_url(
    payload: DirectorUrlCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    source_url = str(payload.url)
    suffix = Path(urlparse(source_url).path).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail="当前只支持公开 MP4, MOV, M4V 或 WEBM 视频直链",
        )
    row = DirectorProject(
        tenant_id=auth.tenant_id,
        name=payload.name.strip() or "链接视频",
        mode="reference",
        source_kind="url",
        source_url=source_url,
        source_filename=f"reference{suffix}",
        status="queued",
        progress_stage="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    background_tasks.add_task(analyze_reference_project, row.id)
    return _serialize(db, row)


@router.post("/capture-sessions")
def create_capture_session(
    payload: DirectorCaptureCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    source_page_url = str(payload.url)
    platform = capture_platform(source_page_url)
    if payload.project_id:
        project = db.query(DirectorProject).filter_by(id=payload.project_id, tenant_id=auth.tenant_id).with_for_update().first()
        if not project:
            raise HTTPException(status_code=404, detail="编导项目不存在")
        if project.source_kind != "browser_extension" or project.source_url:
            raise HTTPException(status_code=409, detail="该项目无需重新采集")
        if (project.analysis or {}).get("capture", {}).get("source_page_url") != source_page_url:
            raise HTTPException(status_code=409, detail="重新采集的视频链接与原项目不一致")
        previous = db.query(DirectorCaptureSession).filter_by(project_id=project.id).with_for_update().all()
        if any(row.status in {"waiting", "active", "uploading"} and row.expires_at > _utcnow() for row in previous):
            raise HTTPException(status_code=409, detail="视频仍在采集中, 请等待当前任务结束")
        for row in previous:
            row.status = "superseded"
        project.status = "capturing"
        project.progress_stage = "waiting_extension"
        project.error_message = ""
    else:
        project = DirectorProject(
            tenant_id=auth.tenant_id,
            name=payload.name.strip() or "链接视频",
            mode="reference",
            source_kind="browser_extension",
            source_url="",
            source_filename="",
            status="capturing",
            progress_stage="waiting_extension",
            analysis={"capture": {"source_page_url": source_page_url, "source_platform": platform}},
        )
        db.add(project)
    db.flush()
    token = new_capture_token()
    capture = DirectorCaptureSession(
        id=uuid.uuid4().hex,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        project_id=project.id,
        token_hash=capture_token_hash(token),
        source_page_url=source_page_url,
        source_platform=platform,
        status="waiting",
        expires_at=_utcnow() + timedelta(minutes=get_settings().director_capture_session_minutes),
    )
    db.add(capture)
    db.commit()
    db.refresh(project)
    return {
        "project": _serialize(db, project),
        "capture": {
            "id": capture.id,
            "token": token,
            "expires_at": capture.expires_at.isoformat(),
            "source_page_url": capture.source_page_url,
            "source_platform": capture.source_platform,
            "max_bytes": get_settings().director_video_max_upload_mb * 1024 * 1024,
        },
    }


@router.patch("/capture-sessions/{capture_id}")
def update_capture_session(
    capture_id: str,
    payload: DirectorCaptureStatusUpdate,
    x_capture_token: str = Header(default="", alias="X-Capture-Token"),
    db: Session = Depends(get_db),
):
    capture = _capture_session(db, capture_id, x_capture_token)
    if capture.status == "completed":
        return {"status": "completed"}
    if capture.status == "uploading":
        raise HTTPException(status_code=409, detail="采集视频正在上传或验证")
    project = db.query(DirectorProject).filter_by(id=capture.project_id, tenant_id=capture.tenant_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="编导项目不存在")
    capture.status = "active"
    capture.details = {**(capture.details or {}), "stage": payload.stage}
    if payload.diagnostics:
        capture.details = {**capture.details, "diagnostics": payload.diagnostics.model_dump()}
    project.status = "capturing"
    project.progress_stage = payload.stage
    project.error_message = ""
    db.commit()
    return {"status": capture.status}


@router.post("/capture-sessions/{capture_id}/fail")
def fail_capture_session(
    capture_id: str,
    payload: DirectorCaptureFailure,
    x_capture_token: str = Header(default="", alias="X-Capture-Token"),
    db: Session = Depends(get_db),
):
    capture = _capture_session(db, capture_id, x_capture_token)
    if capture.status == "completed":
        return {"status": "completed"}
    if capture.status == "uploading":
        raise HTTPException(status_code=409, detail="采集视频正在上传或验证")
    project = db.query(DirectorProject).filter_by(id=capture.project_id, tenant_id=capture.tenant_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="编导项目不存在")
    _set_capture_failed(db, capture, project, payload.message, payload.diagnostics)
    return {"status": capture.status}


@router.post("/capture-sessions/{capture_id}/upload")
async def upload_captured_video(
    capture_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_capture_token: str = Header(default="", alias="X-Capture-Token"),
    x_video_filename: str = Header(default="", alias="X-Video-Filename"),
    x_source_title: str = Header(default="", alias="X-Source-Title"),
    db: Session = Depends(get_db),
):
    capture = _capture_session(db, capture_id, x_capture_token)
    if capture.status == "completed":
        raise HTTPException(status_code=409, detail="采集任务已经完成")
    if capture.status == "uploading":
        raise HTTPException(status_code=409, detail="采集视频正在上传")
    project = db.query(DirectorProject).filter_by(id=capture.project_id, tenant_id=capture.tenant_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="编导项目不存在")

    max_bytes = get_settings().director_video_max_upload_mb * 1024 * 1024
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > max_bytes:
        _set_capture_failed(db, capture, project, f"视频不能超过 {get_settings().director_video_max_upload_mb}MB")
        raise HTTPException(status_code=413, detail=f"视频不能超过 {get_settings().director_video_max_upload_mb}MB")

    content_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
    if content_type != "application/octet-stream" and not content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="采集结果不是受支持的视频文件")
    filename = capture_filename(x_video_filename, content_type, capture.source_platform)
    suffix = Path(filename).suffix.lower()
    capture.status = "uploading"
    project.status = "capturing"
    project.progress_stage = "uploading"
    db.commit()
    file_descriptor, temporary_name = tempfile.mkstemp(prefix="enrui-capture-", suffix=suffix)
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    size = 0
    try:
        async with aiofiles.open(temporary_path, "wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"视频不能超过 {get_settings().director_video_max_upload_mb}MB")
                await output.write(chunk)
        if not size:
            raise HTTPException(status_code=400, detail="采集到的视频文件为空")
        capture = _capture_session(db, capture_id, x_capture_token)
        project.progress_stage = "validating_media"
        db.commit()
        try:
            media_info = await asyncio.to_thread(_probe_media, temporary_path)
            if not media_info.get("width") or not media_info.get("height") or media_info.get("duration_seconds", 0) <= 0:
                raise ValueError("No video track")
        except Exception as exc:
            raise HTTPException(status_code=415, detail="采集文件不是可解析的视频, 请重新采集") from exc
        capture = _capture_session(db, capture_id, x_capture_token)
        db.commit()
        source_url = await get_storage().upload_path(temporary_path, filename, "director/sources")
        capture = _capture_session(db, capture_id, x_capture_token)
    except HTTPException as exc:
        _set_capture_failed(db, capture, project, str(exc.detail))
        raise
    except Exception as exc:
        logger.exception("Browser capture upload failed for project %s", project.id)
        _set_capture_failed(db, capture, project, "采集视频上传失败, 请重新提交链接")
        raise HTTPException(status_code=502, detail="采集视频上传失败, 请重新提交链接") from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    project.source_url = source_url
    project.source_filename = filename
    source_title = unquote(x_source_title).strip()[:256]
    if source_title and project.name == "链接视频":
        project.name = source_title
    project.status = "queued"
    project.progress_stage = "queued"
    project.error_message = ""
    project.analysis = {
        **(project.analysis or {}),
        "capture": {
            **((project.analysis or {}).get("capture") or {}),
            "content_type": content_type,
            "filename": filename,
            "size_bytes": size,
            "source_title": source_title,
        },
    }
    capture.status = "completed"
    capture.details = {**(capture.details or {}), "content_type": content_type, "filename": filename, "size_bytes": size, "source_title": source_title}
    db.commit()
    db.refresh(project)
    background_tasks.add_task(analyze_reference_project, project.id)
    return _serialize(db, project)


@router.post("/original")
async def create_original(
    payload: DirectorOriginalCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    row = DirectorProject(
        tenant_id=auth.tenant_id,
        product_id=None,
        name=payload.name.strip() or "AI 编导脚本",
        mode="original",
        source_kind="prompt",
        language=payload.language,
        product_service="",
        selling_points=payload.brief,
        status="generating",
        progress_stage="writing_script",
    )
    db.add(row)
    db.flush()
    sections = await _generate_original_sections(payload.brief, payload.language)
    cursor = 0
    for index, item in enumerate(sections):
        duration = max(2, int(item.get("duration_seconds") or 4))
        lines = item.get("lines") if isinstance(item.get("lines"), list) else []
        db.add(
            DirectorSection(
                tenant_id=auth.tenant_id,
                project_id=row.id,
                sort_order=index,
                title=str(item.get("title") or f"镜头段落 {index + 1}")[:128],
                start_ms=cursor * 1000,
                end_ms=(cursor + duration) * 1000,
                source_text="",
                script_lines=[_new_line(str(line)) for line in lines if str(line).strip()],
                description=str(item.get("description") or ""),
                shooting_notes=str(item.get("shooting_notes") or ""),
            )
        )
        cursor += duration
    row.source_duration_seconds = cursor
    row.analysis = {"summary": payload.brief, "tags": ["AI 原创脚本"], "audio_analyzed": False}
    row.status = "ready"
    row.progress_stage = "completed"
    db.commit()
    db.refresh(row)
    return _serialize(db, row)


@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    return _serialize(db, _project(db, auth.tenant_id, project_id))


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    project = _project(db, auth.tenant_id, project_id)
    db.query(DirectorCaptureSession).filter_by(project_id=project.id).delete(synchronize_session=False)
    db.query(ShootingTask).filter_by(project_id=project.id).delete(synchronize_session=False)
    db.delete(project)
    db.commit()
    return {"ok": True}


@router.post("/{project_id}/reanalyze")
def reanalyze_project(
    project_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    row = _project(db, auth.tenant_id, project_id)
    if row.mode != "reference":
        raise HTTPException(status_code=409, detail="只有参考视频项目可以重新解析")
    if not row.source_url:
        raise HTTPException(status_code=409, detail="视频尚未采集成功, 请先重新采集")
    if row.status in {"capturing", "queued", "analyzing"}:
        raise HTTPException(status_code=409, detail="视频正在处理中, 请等待当前任务结束")
    row.status = "queued"
    row.progress_stage = "queued"
    row.error_message = ""
    db.commit()
    background_tasks.add_task(analyze_reference_project, row.id)
    return _serialize(db, row)


@router.put("/{project_id}/analysis")
def update_analysis(
    project_id: int,
    payload: DirectorAnalysisUpdate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    row = _project(db, auth.tenant_id, project_id)

    def clean_tags(values: list[str], max_items: int) -> list[str]:
        unique: list[str] = []
        for value in values:
            item = value.strip()[:24]
            if item and item not in unique:
                unique.append(item)
            if len(unique) >= max_items:
                break
        return unique

    row.analysis = {
        **(row.analysis if isinstance(row.analysis, dict) else {}),
        "product_name": _primary_product_name(payload.product_name, []),
        "selling_points": clean_tags(payload.selling_points, 5),
        "target_audience": clean_tags(payload.target_audience, 4),
        "target_pain_points": payload.target_pain_points.strip(),
    }
    db.commit()
    db.refresh(row)
    return _serialize(db, row)


@router.post("/{project_id}/rewrite")
async def rewrite_project(
    project_id: int,
    payload: DirectorRewriteRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    row = _project(db, auth.tenant_id, project_id)
    sections = db.query(DirectorSection).filter_by(project_id=row.id).order_by(DirectorSection.sort_order).all()
    if not sections:
        raise HTTPException(status_code=409, detail="请先完成参考视频解析")
    product_name = payload.product_service.strip() or "该产品或服务"
    selling_points = payload.selling_points.strip()
    generated: dict[str, Any] = {}
    if not get_settings().llm_mock_mode:
        structure = [
            {"section_id": section.id, "title": section.title, "source_text": section.source_text, "description": section.description}
            for section in sections
        ]
        prompt = f"""参考给定短视频结构, 为新产品仿写原创脚本, 只返回 JSON.
产品或服务: {product_name}; 卖点: {selling_points}; 语言: {payload.language}.
参考结构: {structure}.
输出 sections, 每项包含 section_id, lines, shooting_notes. lines 是 1 到 4 条短视频台词.
保留传播结构但不得复制原句, 不生成音频字段, 不虚构功效."""
        generated = await LLMClient().chat_json([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=6000)
    generated_items = generated.get("sections") if isinstance(generated, dict) else None
    by_id = {
        int(item.get("section_id")): item
        for item in generated_items or []
        if isinstance(item, dict) and str(item.get("section_id", "")).isdigit()
    }
    for section in sections:
        item = by_id.get(section.id, {})
        lines = item.get("lines") if isinstance(item.get("lines"), list) else []
        if not lines:
            lines = [_fallback_line(section.title, product_name, selling_points)]
        section.script_lines = [_new_line(str(line)) for line in lines if str(line).strip()]
        if item.get("shooting_notes"):
            section.shooting_notes = str(item["shooting_notes"])
    row.product_id = None
    row.product_service = product_name
    row.selling_points = selling_points
    row.language = payload.language
    row.status = "ready"
    row.progress_stage = "completed"
    db.commit()
    db.refresh(row)
    return _serialize(db, row)


@router.put("/{project_id}/sections")
def update_sections(
    project_id: int,
    payload: list[DirectorSectionUpdate],
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    row = _project(db, auth.tenant_id, project_id)
    existing = {section.id: section for section in db.query(DirectorSection).filter_by(project_id=row.id).all()}
    if any(item.id not in existing for item in payload):
        raise HTTPException(status_code=404, detail="镜头段落不存在")
    for item in payload:
        section = existing[item.id]
        section.sort_order = item.sort_order
        section.title = item.title
        section.start_ms = item.start_ms
        section.end_ms = item.end_ms
        section.script_lines = [
            {"id": str(line.get("id") or uuid.uuid4().hex), "text": str(line.get("text") or "")[:4000]}
            for line in item.script_lines[:20]
        ]
        section.description = item.description[:4000]
        section.shooting_notes = item.shooting_notes[:4000]
    row.status = "ready"
    db.commit()
    db.refresh(row)
    return _serialize(db, row)


@router.put("/{project_id}/shots")
def update_shots(
    project_id: int,
    payload: list[DirectorShotUpdate],
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    project = _project(db, auth.tenant_id, project_id)
    existing = {
        shot.id: shot
        for shot in db.query(DirectorShot).filter_by(project_id=project.id).all()
    }
    if any(item.id not in existing for item in payload):
        raise HTTPException(status_code=404, detail="子分镜不存在")
    for item in payload:
        shot = existing[item.id]
        shot.transcript = item.transcript
        shot.subject = item.subject
        shot.action = item.action
        shot.scene = item.scene
        shot.shot_size = item.shot_size
        shot.camera = item.camera
        shot.analysis = {
            **(shot.analysis if isinstance(shot.analysis, dict) else {}),
            "product": item.product,
            "director_intent": item.director_intent,
        }
        shot.description = item.description
    db.commit()
    db.refresh(project)
    return _serialize(db, project)


@router.post("/{project_id}/sections/{section_id}/rewrite")
async def rewrite_section(
    project_id: int,
    section_id: int,
    payload: DirectorSectionRewrite,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    row = _project(db, auth.tenant_id, project_id)
    section = db.query(DirectorSection).filter_by(id=section_id, project_id=row.id).first()
    if not section:
        raise HTTPException(status_code=404, detail="镜头段落不存在")
    current = "\n".join(str(line.get("text", "")) for line in section.script_lines)
    text = ""
    if not get_settings().llm_mock_mode:
        result = await LLMClient().chat_json(
            [{"role": "user", "content": f"改写短视频台词, 只返回 JSON {{\"text\": \"...\"}}. 产品: {row.product_service}. 当前台词: {current}. 要求: {payload.instruction}. 不虚构功效."}],
            temperature=0.5,
        )
        text = str(result.get("text") or "")
    text = text.strip() or _fallback_line(section.title, row.product_service or "该产品", row.selling_points)
    section.script_lines = [_new_line(text)]
    db.commit()
    db.refresh(row)
    return _serialize(db, row)


@router.post("/{project_id}/sections/{section_id}/assets")
async def upload_asset(
    project_id: int,
    section_id: int,
    file: UploadFile = File(...),
    role: Literal["reference", "material"] = Form("material"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    project = _project(db, auth.tenant_id, project_id)
    section = db.query(DirectorSection).filter_by(id=section_id, project_id=project.id).first()
    if not section:
        raise HTTPException(status_code=404, detail="镜头段落不存在")
    content_type = (file.content_type or "").lower()
    media_kind = "image" if content_type.startswith("image/") else "video" if content_type.startswith("video/") else ""
    if not media_kind:
        raise HTTPException(status_code=415, detail="仅支持图片或视频素材")
    max_bytes = get_settings().director_video_max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"素材不能超过 {get_settings().director_video_max_upload_mb}MB")
    url = await get_storage().upload(data, file.filename or f"material-{uuid.uuid4().hex}", f"director/{project.id}/materials")
    count = db.query(DirectorAsset).filter_by(section_id=section.id, role=role).count()
    asset = DirectorAsset(
        tenant_id=auth.tenant_id,
        project_id=project.id,
        section_id=section.id,
        role=role,
        media_kind=media_kind,
        source="uploaded",
        url=url,
        thumbnail_url=url if media_kind == "image" else "",
        description=file.filename or "上传素材",
        sort_order=count,
    )
    db.add(asset)
    db.commit()
    db.refresh(project)
    return _serialize(db, project)


@router.delete("/{project_id}/assets/{asset_id}")
async def remove_asset(
    project_id: int,
    asset_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    project = _project(db, auth.tenant_id, project_id)
    asset = db.query(DirectorAsset).filter_by(id=asset_id, project_id=project.id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    if asset.source in {"uploaded", "generated"}:
        await get_storage().delete(asset.url)
        if asset.thumbnail_url and asset.thumbnail_url != asset.url:
            await get_storage().delete(asset.thumbnail_url)
    db.delete(asset)
    db.commit()
    return {"ok": True}


@router.post("/{project_id}/sections/{section_id}/assets/generate")
async def generate_asset(
    project_id: int,
    section_id: int,
    payload: DirectorGenerateAsset,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    project = _project(db, auth.tenant_id, project_id)
    section = db.query(DirectorSection).filter_by(id=section_id, project_id=project.id).first()
    if not section:
        raise HTTPException(status_code=404, detail="镜头段落不存在")
    references = db.query(DirectorAsset).filter_by(section_id=section.id, role="reference").all()
    source_urls = [asset.thumbnail_url or asset.url for asset in references]
    line_text = " ".join(str(line.get("text", "")) for line in section.script_lines)
    prompt = payload.prompt.strip() or section.shooting_notes or section.description or line_text
    provider = get_image_provider("director_storyboard")
    urls = await asyncio.to_thread(
        provider.generate,
        source_url=source_urls[0] if source_urls else "",
        source_urls=source_urls,
        prompt=f"短视频竖屏拍摄参考图. 段落: {section.title}. 台词: {line_text}. 画面要求: {prompt}",
        action="编导素材草图",
        count=1,
        width=720,
        height=1280,
        project_id=project.id,
    )
    if not urls:
        raise HTTPException(status_code=502, detail="素材生成服务未返回结果")
    video_url = await asyncio.to_thread(render_ai_material_video, urls[0], project.id)
    asset = DirectorAsset(
        tenant_id=auth.tenant_id,
        project_id=project.id,
        section_id=section.id,
        role="material",
        media_kind="video" if video_url else "image",
        source="generated",
        url=video_url or urls[0],
        thumbnail_url=urls[0],
        description=f"AI 复刻视频素材: {prompt}" if video_url else prompt,
        duration_seconds=3.6 if video_url else 0,
        sort_order=db.query(DirectorAsset).filter_by(section_id=section.id, role="material").count(),
    )
    db.add(asset)
    db.commit()
    db.refresh(project)
    return _serialize(db, project)


@router.post("/{project_id}/collect-materials")
def collect_materials(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    project = _project(db, auth.tenant_id, project_id)
    sections = db.query(DirectorSection).filter_by(project_id=project.id).order_by(DirectorSection.sort_order).all()
    added = 0
    for section in sections:
        exists = db.query(DirectorAsset).filter_by(section_id=section.id, role="material").first()
        if exists:
            continue
        reference = (
            db.query(DirectorAsset)
            .filter_by(section_id=section.id, role="reference")
            .order_by(DirectorAsset.sort_order)
            .first()
        )
        if not reference:
            continue
        db.add(
            DirectorAsset(
                tenant_id=auth.tenant_id,
                project_id=project.id,
                section_id=section.id,
                role="material",
                media_kind=reference.media_kind,
                source="collected",
                url=reference.url,
                thumbnail_url=reference.thumbnail_url,
                description="从源视频收集的参考片段",
                duration_seconds=reference.duration_seconds,
            )
        )
        added += 1
    db.commit()
    db.refresh(project)
    result = _serialize(db, project)
    result["collected_count"] = added
    return result


@router.get("/{project_id}/compliance")
def compliance(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    project = _project(db, auth.tenant_id, project_id)
    sections = db.query(DirectorSection).filter_by(project_id=project.id).order_by(DirectorSection.sort_order).all()
    rules = {
        "绝对安全": "避免绝对化安全承诺",
        "百分百": "避免无法证明的绝对承诺",
        "根治": "美妆内容不得使用医疗化治疗表述",
        "药到病除": "美妆内容不得使用医疗化治疗表述",
        "最有效": "避免最高级和无法证明的效果表述",
        "永久": "避免永久性功效承诺",
    }
    issues = []
    for section in sections:
        for line in section.script_lines:
            text = str(line.get("text", ""))
            for phrase, message in rules.items():
                if phrase in text:
                    issues.append({"section_id": section.id, "section_title": section.title, "phrase": phrase, "message": message})
    return {"status": "review" if issues else "passed", "issue_count": len(issues), "issues": issues}


@router.post("/{project_id}/mix")
def mix_project(
    project_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    project = _project(db, auth.tenant_id, project_id)
    project.status = "queued"
    project.progress_stage = "queued_for_mix"
    project.error_message = ""
    db.commit()
    background_tasks.add_task(mix_silent_preview, project.id)
    return _serialize(db, project)
