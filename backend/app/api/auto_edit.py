"""Material-first automatic video editing PoC."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import tempfile
import uuid
import re
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.config import get_settings
from app.database import get_db
from app.models import (EditingPlanVersion, Product, VideoAsset,
                        VideoAssetAnalysis, VideoEditProject, VideoEditProjectAsset, VideoHighlight,
                        VideoRenderJob)
from app.services.storage import get_storage
from app.services.tts import synthesize_speech
from app.services.llm import LLMClient
from app.services.video_analysis_queue import enqueue_video_analysis
from app.services.video_taxonomy import inherit_public_tags_to_final

router = APIRouter(prefix="/auto-edit-projects", tags=["auto-edit"])
logger = logging.getLogger(__name__)

ROLE_OBJECTIVES = {
    "hook": "原声 Hook 建立注意力", "pain_point": "集中表达需求或痛点",
    "selling_point": "连续讲解直播利益或核心卖点", "brand_trust": "用代言人、品牌身份或权威信息建立信任",
    "product_demo": "商品、质地或效果展示",
    "usage": "使用过程和体验", "proof": "效果或场景证明", "cta": "明确行动引导",
}
OBJECTIVE_ROLE_ORDERS = {
    # 参考成熟直播引流成片：先把活动/商品价值讲清，再集中解决痛点并展示使用结果。
    "live_stream": ["hook", "selling_point", "brand_trust", "pain_point", "product_demo", "usage", "proof", "cta"],
    "conversion": ["hook", "selling_point", "brand_trust", "pain_point", "product_demo", "usage", "proof", "cta"],
    "cart_conversion": ["hook", "pain_point", "selling_point", "brand_trust", "product_demo", "usage", "proof", "cta"],
    "seeding": ["hook", "brand_trust", "usage", "selling_point", "product_demo", "proof", "cta"],
}
DEFAULT_STRUCTURE = [{"role": role, "objective": ROLE_OBJECTIVES[role], "required": True}
                     for role in OBJECTIVE_ROLE_ORDERS["live_stream"]]

OBJECTIVE_PROFILES = {
    "live_stream": {
        "label": "直播间引流",
        "guidance": "默认目标是引导进入直播间。参考成熟广告结构按 Hook→直播利益/核心卖点→品牌信任→痛点→商品展示→使用→效果证明→直播间 CTA 单向推进；60～120 秒成片存在合格代言人、品牌身份或权威背书段时应优先保留；原声必须保留其原画面，不可用无关人物或 B-roll 覆盖；禁止小黄车、商品链接、点击购买和直接下单话术；不得虚构直播价格、赠品或优惠。",
    },
    "conversion": {
        "label": "直播间引流（旧项目）",
        "guidance": "按直播间引流处理，结尾明确邀请进入直播间；禁止小黄车、商品链接、点击购买和直接下单话术。",
    },
    "cart_conversion": {
        "label": "小黄车成交",
        "guidance": "快速进入痛点和利益点，强化商品、证明与直接购买 CTA；仅此模式允许小黄车、点击购买或直接下单话术。",
    },
    "seeding": {
        "label": "内容种草",
        "guidance": "以真实体验和使用场景建立信任，保留自然分享感，逐步带出卖点，结尾使用柔和 CTA。",
    },
}


DANGLING_ANAPHORS = ("所以", "它", "这个", "这种", "然后", "但是")


def library_type_for_edit_mode(edit_mode: str) -> str:
    return "finished" if edit_mode == "finished_remix" else "semi_finished"


def asset_eligible_for_project(asset: Any, tenant_id: str, product_id: int, edit_mode: str) -> bool:
    if getattr(asset, "tenant_id", None) != tenant_id:
        return False
    if getattr(asset, "product_id", None) != product_id:
        return False
    if getattr(asset, "status", None) != "analyzed" or getattr(asset, "excluded", False):
        return False
    if getattr(asset, "library_type", None) != library_type_for_edit_mode(edit_mode):
        return False
    if getattr(asset, "asset_kind", "material") != "material":
        return False
    if edit_mode != "finished_remix":
        return True
    return getattr(asset, "reusable_status", None) == "reusable" and int(getattr(asset, "generation_depth", 0) or 0) <= 1


def origin_ids_from_variant(variant: dict[str, Any]) -> list[str]:
    origin_ids: list[str] = []
    for clip in list(variant.get("narrative_segments") or []) + list(variant.get("video_track") or []):
        asset_id = clip.get("asset_id")
        if asset_id and asset_id not in origin_ids:
            origin_ids.append(str(asset_id))
    return origin_ids


def finished_output_metadata(source_assets: list[Any], origin_ids: list[str]) -> dict[str, Any]:
    depths = [int(getattr(asset, "generation_depth", 0) or 0) for asset in source_assets]
    depth = (max(depths) if depths else 0) + 1
    return {
        "library_type": "finished",
        "generation_depth": depth,
        "origin_asset_ids": origin_ids,
        "reusable_status": "blocked" if depth > 1 else "review_required",
        "has_background_music": any(bool(getattr(asset, "has_background_music", False)) for asset in source_assets),
        "burned_subtitles": any(bool(getattr(asset, "burned_subtitles", False)) for asset in source_assets),
    }


def _eligible_assets_query(db: Session, tenant_id: str, edit_mode: str, product_id: Optional[int] = None,
                           project_id: Optional[int] = None):
    query = db.query(VideoAsset).filter(
        VideoAsset.tenant_id == tenant_id,
        VideoAsset.status == "analyzed",
        VideoAsset.excluded.is_(False),
        VideoAsset.library_type == library_type_for_edit_mode(edit_mode),
        VideoAsset.asset_kind == "material",
    )
    if product_id is not None:
        query = query.filter(VideoAsset.product_id == product_id)
    if project_id is not None:
        query = query.join(VideoEditProjectAsset, VideoEditProjectAsset.asset_id == VideoAsset.id).filter(
            VideoEditProjectAsset.project_id == project_id,
            VideoEditProjectAsset.included.is_(True),
            VideoEditProjectAsset.tenant_id == tenant_id,
        )
    if edit_mode == "finished_remix":
        query = query.filter(VideoAsset.reusable_status == "reusable", VideoAsset.generation_depth <= 1)
    return query


def _starts_with_dangling_anaphor(text: str) -> bool:
    cleaned = re.sub(r"^[\s，。！？、]+", "", text or "")
    return any(cleaned.startswith(token) for token in DANGLING_ANAPHORS)


def _text_tokens(value: str) -> set[str]:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", (value or "").lower())
    return {cleaned[index:index + 2] for index in range(max(0, len(cleaned) - 1))}


def _usable_duration(highlight: VideoHighlight, min_seconds: float = 3.5, max_seconds: float = 15.0) -> float:
    duration = float(highlight.end_seconds) - float(highlight.start_seconds)
    if duration < min_seconds:
        return 0.0
    return min(max_seconds, duration)


class ProjectCreate(BaseModel):
    product_id: int
    name: str = ""
    target_duration_seconds: int = Field(default=90, ge=60, le=120)
    variant_count: int = Field(default=3, ge=1, le=5)
    aspect_ratio: str = Field(default="9:16", pattern="^(9:16|16:9|1:1)$")
    objective: str = "live_stream"
    edit_mode: str = Field(default="smart_compose", pattern="^(smart_compose|finished_remix)$")
    structure: Optional[list[dict[str, Any]]] = None
    constraints: list[str] = Field(default_factory=list)


class ProjectDurationUpdate(BaseModel):
    target_duration_seconds: int = Field(ge=60, le=120)


def _project(db: Session, auth: AuthContext, project_id: int) -> VideoEditProject:
    row = db.query(VideoEditProject).filter(VideoEditProject.id == project_id, VideoEditProject.tenant_id == auth.tenant_id).first()
    if not row:
        raise HTTPException(404, "自动剪辑项目不存在")
    return row


def _probe(path: Union[Path, str]) -> dict[str, Any]:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
        ], capture_output=True, text=True, timeout=60, check=True)
        data = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        label = path.name if isinstance(path, Path) else "OSS 视频"
        raise HTTPException(422, f"无法读取视频：{label}，请确认 FFmpeg 已安装且文件有效") from exc
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        label = path.name if isinstance(path, Path) else "OSS 视频"
        raise HTTPException(422, f"{label} 不包含视频流")
    rate = video.get("avg_frame_rate", "0/1").split("/")
    fps = float(rate[0]) / max(float(rate[1]), 1) if len(rate) == 2 else 0
    duration = float(video.get("duration") or data.get("format", {}).get("duration") or 0)
    return {"duration": duration, "width": int(video.get("width", 0)), "height": int(video.get("height", 0)), "fps": fps,
            "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def _media_duration(path: Union[Path, str]) -> float:
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                                capture_output=True, text=True, timeout=60, check=True)
        duration = float(result.stdout.strip())
        if duration <= 0: raise ValueError("媒体时长无效")
        return duration
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        raise RuntimeError(f"无法读取音频时长：{Path(str(path)).name}") from exc


def _asset_json(row: VideoAsset, analysis: Optional[VideoAssetAnalysis], highlights: list[VideoHighlight]) -> dict[str, Any]:
    return {"id": row.id, "asset_id": row.asset_id, "filename": row.filename, "object_url": row.object_url,
            "duration_seconds": row.duration_seconds, "width": row.width, "height": row.height, "fps": row.fps,
            "has_audio": row.has_audio, "status": row.status, "analysis": analysis.analysis_json if analysis else None,
            "highlights": [{"highlight_id": h.highlight_id, "start": h.start_seconds, "end": h.end_seconds,
                            "description": h.description, "reason": h.reason, "recommended_roles": h.recommended_roles,
                            "content_role": h.content_role, "track_role": h.track_role, "speech_text": h.speech_text,
                            "speaker_id": h.speaker_id, "audio_strategy": h.audio_strategy, "shot_type": h.shot_type,
                            "score": h.score} for h in highlights]}


def _serialize(db: Session, row: VideoEditProject) -> dict[str, Any]:
    product = db.query(Product).filter(Product.id == row.product_id, Product.tenant_id == row.tenant_id).first()
    assets = _eligible_assets_query(db, row.tenant_id, row.edit_mode, product_id=row.product_id, project_id=row.id).order_by(VideoAsset.id).all()
    payload_assets = []
    for asset in assets:
        analysis = db.query(VideoAssetAnalysis).filter(
            VideoAssetAnalysis.asset_id == asset.id, VideoAssetAnalysis.tenant_id == row.tenant_id
        ).order_by(VideoAssetAnalysis.id.desc()).first()
        highlights = db.query(VideoHighlight).filter(
            VideoHighlight.asset_id == asset.id, VideoHighlight.tenant_id == row.tenant_id
        ).order_by(VideoHighlight.start_seconds).all()
        payload_assets.append(_asset_json(asset, analysis, highlights))
    all_highlights = [h for item in payload_assets for h in item["highlights"]]
    original = [h for h in all_highlights if h.get("track_role") == "a_roll"]
    supporting = [h for h in all_highlights if h.get("track_role") == "b_roll"]
    original_seconds = sum(h["end"] - h["start"] for h in original)
    speaker_count = len({h.get("speaker_id") for h in original if h.get("speaker_id")})
    finished_remix = row.edit_mode == "finished_remix"
    smart_mix = get_settings().auto_edit_audio_mode == "smart_mix" and bool(supporting)
    ready = original_seconds >= 15 if finished_remix else ((original_seconds >= row.target_duration_seconds - 5 or smart_mix) and bool(supporting))
    readiness = {"original_speech_segments": len(original), "supporting_visual_segments": len(supporting),
        "original_speech_seconds": round(original_seconds, 1), "speaker_count": speaker_count,
        "audio_mode": get_settings().auto_edit_audio_mode,
        "ready": ready,
        "missing": ([] if ready else (["至少 15 秒可复用成品语义段"] if finished_remix else ["完整原声讲解", "产品、使用或效果辅助画面"]))}
    plan = db.query(EditingPlanVersion).filter(EditingPlanVersion.project_id == row.id).order_by(EditingPlanVersion.version.desc()).first()
    jobs = db.query(VideoRenderJob).filter(VideoRenderJob.project_id == row.id).order_by(VideoRenderJob.id).all()
    return {"id": row.id, "product_id": row.product_id, "name": row.name, "target_duration_seconds": row.target_duration_seconds,
            "variant_count": row.variant_count, "aspect_ratio": row.aspect_ratio, "objective": row.objective,
            "edit_mode": row.edit_mode,
            "structure": row.structure, "constraints": row.constraints, "status": row.status, "error_message": row.error_message,
            "product": {"name": product.name if product else "", "image_urls": product.image_urls if product else []},
            "assets": payload_assets, "material_readiness": readiness, "plan": plan.plan_json if plan else None, "plan_version_id": plan.id if plan else None,
            "outputs": [{"variant_id": j.variant_id, "status": j.status, "output_url": j.output_url,
                         "duration_seconds": j.duration_seconds, "error_message": j.error_message} for j in jobs]}


@router.get("")
def list_projects(edit_mode: Optional[str] = None, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    query = db.query(VideoEditProject).filter(VideoEditProject.tenant_id == auth.tenant_id)
    if edit_mode is not None:
        query = query.filter(VideoEditProject.edit_mode == edit_mode)
    rows = query.order_by(VideoEditProject.updated_at.desc()).all()
    return [_serialize(db, row) for row in rows]


@router.post("")
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    product = db.query(Product).filter(Product.id == payload.product_id, Product.tenant_id == auth.tenant_id).first()
    if not product:
        raise HTTPException(404, "商品不存在")
    row = VideoEditProject(tenant_id=auth.tenant_id, product_id=product.id,
        name=payload.name.strip() or f"{product.name} · AI 自动剪辑", target_duration_seconds=payload.target_duration_seconds,
        variant_count=payload.variant_count, aspect_ratio=payload.aspect_ratio, objective=payload.objective,
        edit_mode=payload.edit_mode,
        structure=payload.structure or [{"role": role, "objective": ROLE_OBJECTIVES[role], "required": True}
                                        for role in OBJECTIVE_ROLE_ORDERS.get(payload.objective, OBJECTIVE_ROLE_ORDERS["live_stream"])],
        constraints=payload.constraints)
    db.add(row); db.commit(); db.refresh(row)
    reusable = _eligible_assets_query(db, auth.tenant_id, payload.edit_mode, product_id=product.id).all()
    for asset in reusable:
        db.add(VideoEditProjectAsset(tenant_id=auth.tenant_id, project_id=row.id, asset_id=asset.id, included=True))
    if reusable:
        row.status = "ready_to_plan"
    db.commit(); db.refresh(row)
    return _serialize(db, row)


@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    return _serialize(db, _project(db, auth, project_id))


@router.patch("/{project_id}/duration")
def update_project_duration(project_id: int, payload: ProjectDurationUpdate,
                            db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    project = _project(db, auth, project_id)
    if payload.target_duration_seconds not in {60, 90, 120}:
        raise HTTPException(422, "目标时长仅支持 60、90 或 120 秒")
    if project.target_duration_seconds != payload.target_duration_seconds:
        project.target_duration_seconds = payload.target_duration_seconds
        project.status = "ready_to_plan"
        project.error_message = ""
        db.commit(); db.refresh(project)
    return _serialize(db, project)


@router.post("/{project_id}/assets")
async def upload_assets(project_id: int, files: list[UploadFile] = File(...), db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    project = _project(db, auth, project_id)
    settings = get_settings()
    for file in files:
        if not (file.content_type or "").startswith("video/") and Path(file.filename or "").suffix.lower() != ".mp4":
            raise HTTPException(415, "只支持视频文件")
        raw = await file.read()
        if len(raw) > max(settings.max_upload_mb, 500) * 1024 * 1024:
            raise HTTPException(413, f"{file.filename} 文件过大")
        digest = hashlib.sha256(raw).hexdigest()
        duplicate = db.query(VideoAsset).filter(
            VideoAsset.tenant_id == auth.tenant_id, VideoAsset.project_id == project.id, VideoAsset.content_hash == digest
        ).first()
        if duplicate:
            continue
        with tempfile.NamedTemporaryFile(suffix=Path(file.filename or "video.mp4").suffix or ".mp4") as temp:
            temp.write(raw); temp.flush(); media = _probe(Path(temp.name))
        url = await get_storage().upload(raw, file.filename or "video.mp4", f"auto-edit/{project.id}/source")
        public_id = f"asset-{uuid.uuid4().hex[:12]}"
        library_type = library_type_for_edit_mode(project.edit_mode)
        asset = VideoAsset(tenant_id=auth.tenant_id, project_id=project.id, product_id=project.product_id,
            asset_id=public_id, filename=file.filename or "video.mp4", object_url=url, content_hash=digest,
            library_type=library_type, asset_kind="material",
            reusable_status="review_required" if library_type == "finished" else "review_required",
            duration_seconds=media["duration"], width=media["width"], height=media["height"], fps=media["fps"],
            has_audio=media["has_audio"], status="queued_for_analysis")
        db.add(asset); db.flush()
        if library_type == "semi_finished" or (library_type == "finished" and asset.reusable_status == "reusable"):
            db.add(VideoEditProjectAsset(tenant_id=auth.tenant_id, project_id=project.id, asset_id=asset.id, included=True))
        enqueue_video_analysis()
    project.status = "ready_to_plan"; db.commit(); db.refresh(project)
    return _serialize(db, project)


def score_transition(previous: Optional[VideoHighlight], current: VideoHighlight,
                     nxt: Optional[VideoHighlight], expected_role: str, role_rank: dict[str, int]) -> dict[str, Any]:
    quality = min(1.0, max(0.0, float(current.score or 0)))
    current_rank = role_rank.get(current.content_role, len(role_rank))
    expected_rank = role_rank.get(expected_role, current_rank)
    if current.content_role == expected_role:
        role_match = 1.0
    elif abs(current_rank - expected_rank) == 1:
        role_match = 0.55
    else:
        role_match = 0.2
    reasons: list[str] = []
    prev_score = 1.0
    if previous is None:
        if _starts_with_dangling_anaphor(current.speech_text):
            prev_score = 0.05
            reasons.append("开场使用了缺少上文的指代词")
        else:
            reasons.append("适合作为开场")
    else:
        prev_rank = role_rank.get(previous.content_role, len(role_rank))
        if current_rank < prev_rank:
            prev_score = 0.0
            reasons.append("营销阶段回退")
        elif current_rank == prev_rank:
            prev_score = 0.9
            reasons.append("同一营销模块连续")
        elif current_rank == prev_rank + 1:
            prev_score = 0.85
            reasons.append("营销阶段顺序推进")
        else:
            prev_score = 0.45
            reasons.append("跳过了中间营销阶段")
        if (previous.speech_text or "").strip() == (current.speech_text or "").strip():
            prev_score = 0.0
            reasons.append("相邻口播完全重复")
        if _starts_with_dangling_anaphor(current.speech_text):
            if previous.speaker_id and previous.speaker_id == current.speaker_id:
                prev_score *= 0.7
                reasons.append("指代词依赖同一说话人上文")
            else:
                prev_score *= 0.2
                reasons.append("缺少上文指代")
        left, right = _text_tokens(previous.speech_text), _text_tokens(current.speech_text)
        overlap = len(left & right) / max(len(left | right), 1)
        selling_overlap = set(previous.selling_points or []) & set(current.selling_points or [])
        if overlap >= 0.08 or selling_overlap:
            prev_score = min(1.0, prev_score + 0.15)
            reasons.append("卖点或语义关键词承接")
        else:
            prev_score *= 0.85
    voice = 0.55
    if previous is None:
        voice = 1.0
    elif previous.speaker_id and current.speaker_id and previous.speaker_id == current.speaker_id:
        voice = 1.0
        reasons.append("说话人连续")
    elif previous.speaker_id and current.speaker_id:
        voice = 0.4
        reasons.append("说话人切换")
    next_score = 1.0
    if nxt is not None:
        next_rank = role_rank.get(nxt.content_role, len(role_rank))
        if next_rank < current_rank:
            next_score = 0.0
        elif next_rank > current_rank + 1:
            next_score = 0.5
        if (nxt.speech_text or "").strip() == (current.speech_text or "").strip():
            next_score = 0.0
        if _starts_with_dangling_anaphor(nxt.speech_text) and not (current.speaker_id and current.speaker_id == nxt.speaker_id):
            next_score = min(next_score, 0.25)
    total = quality * 0.32 + role_match * 0.2 + prev_score * 0.22 + next_score * 0.1 + voice * 0.16
    return {
        "quality_score": round(quality, 3),
        "role_match_score": round(role_match, 3),
        "previous_transition_score": round(prev_score, 3),
        "next_transition_score": round(next_score, 3),
        "voice_continuity_score": round(voice, 3),
        "transition_reason": "；".join(list(dict.fromkeys(reasons))) or "语义承接正常",
        "total": total,
    }


def _finished_remix_plan(project: VideoEditProject, assets: list[VideoAsset], highlights: list[VideoHighlight],
                         target_duration: float) -> dict[str, Any]:
    lookup = {asset.id: asset for asset in assets}
    role_order = OBJECTIVE_ROLE_ORDERS.get(project.objective, OBJECTIVE_ROLE_ORDERS["live_stream"])
    role_rank = {role: index for index, role in enumerate(role_order)}
    candidates = [h for h in highlights if h.track_role == "a_roll" and _usable_duration(h) > 0 and str(h.speech_text or "").strip()]
    if not candidates:
        raise HTTPException(409, "成品素材中缺少可复用的完整语义段")
    used_across: set[str] = set()
    variants = []
    for variant_index in range(project.variant_count):
        selected: list[VideoHighlight] = []
        used: set[str] = set()
        previous: Optional[VideoHighlight] = None
        cursor = 0.0
        for role in role_order:
            role_picks = 0
            while cursor < target_duration - 2:
                best_highlight: Optional[VideoHighlight] = None
                best_scores: Optional[dict[str, Any]] = None
                best_total = -1.0
                for highlight in candidates:
                    if highlight.highlight_id in used or highlight.content_role != role:
                        continue
                    duration = _usable_duration(highlight)
                    if duration < 3.5 or cursor + duration > target_duration + 5:
                        continue
                    if previous is None and _starts_with_dangling_anaphor(highlight.speech_text):
                        continue
                    scores = score_transition(previous, highlight, None, role, role_rank)
                    if scores["previous_transition_score"] <= 0.05:
                        continue
                    bonus = 0.18 if highlight.highlight_id not in used_across else 0.0
                    speaker_bonus = 0.08 if variant_index and previous and previous.speaker_id != highlight.speaker_id else 0.0
                    total = scores["total"] + bonus + speaker_bonus - variant_index * 0.01 * float(highlight.score or 0)
                    if total > best_total:
                        best_highlight, best_scores, best_total = highlight, scores, total
                if best_highlight is None or best_scores is None:
                    break
                selected.append(best_highlight)
                used.add(best_highlight.highlight_id)
                cursor += _usable_duration(best_highlight)
                previous = best_highlight
                role_picks += 1
                progress_target = (role_order.index(role) + 1) / len(role_order) * target_duration
                if role_picks >= 2 and cursor >= progress_target:
                    break
        if len(selected) < 2:
            continue
        signature = tuple(item.highlight_id for item in selected)
        if any(tuple(clip["highlight_id"] for clip in variant["narrative_segments"]) == signature for variant in variants):
            continue
        narrative = []
        timeline = 0.0
        for order, highlight in enumerate(selected, 1):
            nxt = selected[order] if order < len(selected) else None
            prev = selected[order - 2] if order > 1 else None
            scores = score_transition(prev, highlight, nxt, highlight.content_role, role_rank)
            duration = _usable_duration(highlight)
            asset = lookup[highlight.asset_id]
            narrative.append({
                "segment_id": f"v{variant_index + 1}-n{order}", "order": order,
                "asset_id": asset.asset_id, "highlight_id": highlight.highlight_id, "speaker_id": highlight.speaker_id,
                "source_range": {"start": highlight.start_seconds, "end": round(highlight.start_seconds + duration, 3)},
                "timeline_range": {"start": round(timeline, 3), "end": round(timeline + duration, 3)},
                "duration_seconds": round(duration, 3), "content_role": highlight.content_role,
                "speech_text": highlight.speech_text, "reason": highlight.reason,
                "audio_strategy": "keep_original", "quality_score": scores["quality_score"],
                "role_match_score": scores["role_match_score"], "previous_transition_score": scores["previous_transition_score"],
                "next_transition_score": scores["next_transition_score"], "voice_continuity_score": scores["voice_continuity_score"],
                "transition_reason": scores["transition_reason"],
            })
            timeline += duration
            used_across.add(highlight.highlight_id)
        video_track = [{
            "clip_id": f"{segment['segment_id']}-source", "asset_id": segment["asset_id"],
            "highlight_id": segment["highlight_id"], "source_range": segment["source_range"],
            "timeline_range": segment["timeline_range"], "duration_seconds": segment["duration_seconds"],
            "visual_type": "source_sync", "audio_strategy": "keep_original", "content_role": segment["content_role"],
        } for segment in narrative]
        variants.append({
            "variant_id": f"V{variant_index + 1}", "name": f"成品混剪版本 {variant_index + 1}",
            "edit_mode": "finished_remix",
            "strategy": {
                "hook_approach": "保留成品原声开场",
                "selection_principle": "完整语义段 + 相邻衔接评分 + 营销模块连续",
                "pace": ["medium", "measured", "tight"][variant_index % 3],
                "average_shot_duration_seconds": round(timeline / max(len(video_track), 1), 2),
                "difference_from_other_versions": "跨版本尽量使用不同 highlight，并变化说话人接力顺序",
            },
            "target_duration_seconds": round(target_duration, 3), "actual_duration_seconds": round(timeline, 3),
            "speaker_count": len({segment["speaker_id"] for segment in narrative if segment.get("speaker_id")}),
            "narrative_segments": narrative, "video_track": video_track,
        })
    if not variants:
        raise HTTPException(409, "成品语义段无法组成有效混剪方案，请补充可复用成品或放宽排除")
    return {
        "schema_version": "1.2", "project_id": str(project.id),
        "brief": {
            "target_duration_seconds": round(target_duration, 3), "aspect_ratio": project.aspect_ratio,
            "variant_count": project.variant_count, "objective": project.objective,
            "edit_mode": "finished_remix", "constraints": project.constraints, "audio_mode": "keep_original",
        },
        "structure": [{"role": role, "objective": ROLE_OBJECTIVES[role], "required": True} for role in role_order],
        "variants": variants,
    }


def _fallback_plan(project: VideoEditProject, assets: list[VideoAsset], highlights: list[VideoHighlight],
                   requested_duration: Optional[float] = None) -> dict[str, Any]:
    target_duration = requested_duration or float(project.target_duration_seconds)
    lookup = {a.id: a for a in assets}
    a_roll = [h for h in highlights if h.track_role == "a_roll" and h.speech_text.strip() and h.end_seconds > h.start_seconds]
    b_roll = [h for h in highlights if h.track_role == "b_roll" and h.end_seconds > h.start_seconds]
    available_seconds = sum(min(12.0, h.end_seconds - h.start_seconds) for h in a_roll)
    if not a_roll or available_seconds < target_duration - 5:
        raise HTTPException(409, f"原声讲解素材不足：可用约 {available_seconds:.0f} 秒，生成 {target_duration:.0f} 秒成片至少需要 {target_duration - 5:.0f} 秒完整原声")
    variants = []
    for variant_index in range(project.variant_count):
        role_order = OBJECTIVE_ROLE_ORDERS.get(project.objective, OBJECTIVE_ROLE_ORDERS["live_stream"])
        rank = {role: index for index, role in enumerate(role_order)}
        candidates = sorted(a_roll, key=lambda h: (rank.get(h.content_role, len(rank)), -h.score))
        narrative, cursor, used, speakers = [], 0.0, set(), []
        for h in candidates:
            if h.highlight_id in used or cursor >= target_duration - 2: continue
            if h.speaker_id and h.speaker_id not in speakers and len(speakers) >= 8: continue
            duration = min(12.0, h.end_seconds - h.start_seconds)
            if duration < 1.0: continue
            if cursor + duration > target_duration + 5: continue
            asset = lookup[h.asset_id]; start, end = h.start_seconds, h.start_seconds + duration
            narrative.append({"segment_id": f"v{variant_index+1}-n{len(narrative)+1}", "order": len(narrative)+1,
                "asset_id": asset.asset_id, "highlight_id": h.highlight_id, "speaker_id": h.speaker_id,
                "source_range": {"start": start, "end": round(end, 3)}, "timeline_range": {"start": round(cursor, 3), "end": round(cursor+duration, 3)},
                "duration_seconds": round(duration, 3), "content_role": h.content_role, "speech_text": h.speech_text,
                "reason": h.reason, "audio_strategy": "keep_original"})
            cursor += duration; used.add(h.highlight_id)
            if h.speaker_id and h.speaker_id not in speakers: speakers.append(h.speaker_id)
        if cursor < target_duration - 5:
            raise HTTPException(409, "完整原声句子无法组成目标时长，请补充原声讲解素材")
        video_track = []
        for n in narrative:
            seg_start, duration = n["timeline_range"]["start"], n["duration_seconds"]
            asset_start = n["source_range"]["start"]
            # 原声方案保持源视频声画绑定，这是参考成片自然、不突兀的核心。
            video_track.append({"clip_id": f"{n['segment_id']}-source", "asset_id": n["asset_id"],
                "highlight_id": n["highlight_id"],
                "source_range": {"start": asset_start, "end": round(asset_start+duration,3)},
                "timeline_range": {"start": seg_start, "end": round(seg_start+duration,3)},
                "duration_seconds": duration, "visual_type": "source_sync", "audio_strategy": "mute",
                "content_role": n["content_role"]})
        variants.append({"variant_id": f"V{variant_index+1}", "name": f"策略版本 {variant_index+1}",
            "edit_mode": project.edit_mode,
            "strategy": {"hook_approach": "多达人原声 Hook", "selection_principle": "完整原声句子接力，辅助画面语义覆盖",
                         "pace": ["fast", "medium", "accelerating"][variant_index % 3],
                         "average_shot_duration_seconds": round(cursor / max(len(video_track), 1), 2),
                         "difference_from_other_versions": "轮换达人顺序、辅助画面和节奏"},
            "target_duration_seconds": round(target_duration, 3), "actual_duration_seconds": round(cursor, 3),
            "speaker_count": len(speakers), "narrative_segments": narrative, "video_track": video_track})
    return {"schema_version": "1.0", "project_id": str(project.id), "brief": {"target_duration_seconds": round(target_duration, 3),
            "aspect_ratio": project.aspect_ratio, "variant_count": project.variant_count, "objective": project.objective,
            "edit_mode": project.edit_mode, "constraints": project.constraints}, "structure": project.structure, "variants": variants}


def _validate_plan(plan: dict[str, Any], assets: list[VideoAsset]) -> list[str]:
    errors, asset_map = [], {a.asset_id: a for a in assets}
    for variant in plan.get("variants", []):
        narrative = variant.get("narrative_segments", []); video = variant.get("video_track", [])
        if not narrative: errors.append(f"{variant.get('variant_id')} 缺少原声叙事轨")
        if not video: errors.append(f"{variant.get('variant_id')} 缺少画面轨")
        for track_name, clips in (("原声", narrative), ("画面", video)):
          cursor = 0.0
          for index, clip in enumerate(clips, 1):
            if track_name == "原声" and clip.get("audio_strategy") == "ai_voiceover":
                timeline = clip.get("timeline_range", {})
                if not clip.get("audio_url") or abs(timeline.get("start", -1)-cursor) > .05:
                    errors.append(f"{variant.get('variant_id')} AI 配音片段 {index} 无效")
                cursor = timeline.get("end", cursor)
                continue
            asset = asset_map.get(clip.get("asset_id"))
            source = clip.get("source_range", {})
            if not asset: errors.append(f"{variant.get('variant_id')} {track_name}片段 {index} 引用了不存在的素材"); continue
            if source.get("start", -1) < 0 or source.get("end", 0) <= source.get("start", 0) or source.get("end", 0) > asset.duration_seconds + .05:
                errors.append(f"{variant.get('variant_id')} {track_name}片段 {index} 时间码越界")
            timeline = clip.get("timeline_range", {})
            if abs(timeline.get("start", -1) - cursor) > .05: errors.append(f"{variant.get('variant_id')} {track_name}时间线不连续")
            cursor = timeline.get("end", cursor)
        if variant.get("speaker_count", 0) > 8: errors.append(f"{variant.get('variant_id')} 达人数量超过 8 人")
        objective = (plan.get("brief") or {}).get("objective", "live_stream")
        role_order = OBJECTIVE_ROLE_ORDERS.get(objective, OBJECTIVE_ROLE_ORDERS["live_stream"])
        rank = {role: index for index, role in enumerate(role_order)}
        role_positions = [rank.get(clip.get("content_role"), len(rank)) for clip in narrative]
        if role_positions != sorted(role_positions):
            errors.append(f"{variant.get('variant_id')} 原声叙事阶段发生回退或穿插")
        for index, clip in enumerate(narrative, 1):
            if clip.get("audio_strategy") == "ai_voiceover":
                continue
            timeline = clip.get("timeline_range") or {}
            matching_picture = any(
                picture.get("asset_id") == clip.get("asset_id")
                and float((picture.get("timeline_range") or {}).get("start", -1)) <= float(timeline.get("start", 0)) + .05
                and float((picture.get("timeline_range") or {}).get("end", -1)) >= float(timeline.get("end", 0)) - .05
                for picture in video
            )
            if not matching_picture:
                errors.append(f"{variant.get('variant_id')} 原声片段 {index} 未使用对应原画面")
        actual = float(variant.get("actual_duration_seconds", 0))
        target = float(variant.get("target_duration_seconds", 0))
        edit_mode = variant.get("edit_mode") or (plan.get("brief") or {}).get("edit_mode")
        if target and actual > target + 5:
            errors.append(f"{variant.get('variant_id')} 实际时长 {actual:.1f} 秒超过目标 {target:.0f} 秒超过 5 秒")
        if target and actual < target - 5 and edit_mode != "finished_remix":
            errors.append(f"{variant.get('variant_id')} 实际时长 {actual:.1f} 秒与目标 {target:.0f} 秒偏差超过 5 秒")
        used_highlights: set[str] = set()
        previous_speech = ""
        previous_role_rank = -1
        for index, clip in enumerate(narrative, 1):
            speech = str(clip.get("speech_text") or "").strip()
            highlight_id = str(clip.get("highlight_id") or "")
            if edit_mode == "finished_remix" and clip.get("audio_strategy") == "ai_voiceover":
                errors.append(f"{variant.get('variant_id')} 成品混剪禁止生成 TTS")
            if highlight_id:
                if highlight_id in used_highlights:
                    errors.append(f"{variant.get('variant_id')} 重复使用 highlight {highlight_id}")
                used_highlights.add(highlight_id)
            if index == 1 and _starts_with_dangling_anaphor(speech):
                errors.append(f"{variant.get('variant_id')} 开场片段使用了缺少上文的指代词")
            if previous_speech and speech and previous_speech == speech:
                errors.append(f"{variant.get('variant_id')} 相邻片段口播完全重复")
            current_rank = rank.get(clip.get("content_role"), len(rank))
            if previous_role_rank >= 0 and current_rank < previous_role_rank:
                errors.append(f"{variant.get('variant_id')} 相邻片段营销阶段回退")
            previous_speech = speech
            previous_role_rank = current_rank
            if edit_mode == "finished_remix":
                required_scores = ("quality_score", "role_match_score", "previous_transition_score",
                                   "next_transition_score", "voice_continuity_score", "transition_reason")
                missing = [key for key in required_scores if key not in clip]
                if missing:
                    errors.append(f"{variant.get('variant_id')} 片段 {index} 缺少衔接评分字段")
    return errors


async def _create_ai_voiceovers(project: VideoEditProject, missing_seconds: float) -> list[dict[str, Any]]:
    target_chars = max(18, round(missing_seconds * 3.6))
    profile = OBJECTIVE_PROFILES.get(project.objective, OBJECTIVE_PROFILES["live_stream"])
    prompt = f"""为“{project.name}”生成{profile['label']}短视频补充旁白，只返回 JSON：{{"segments":[{{"role":"pain_point|selling_point|product_demo|usage|proof|cta","text":"..."}}]}}。
总字数约 {target_chars} 个汉字，分成 2～6 个口语短句，每句 10～22 字。像真人分享体验，避免“轻松打造、焕发光彩、赶快入手”等模板化机器文案。围绕素颜霜/精华霜的真实痛点、使用体验、妆效和行动引导，不得虚构未提供的功效、价格或赠品；不要谈腮红、眼影、睫毛、口红和唇蜜。"""
    if project.objective in {"live_stream", "conversion"}:
        prompt += "\n结尾 CTA 只能自然引导进入直播间；禁止小黄车、商品链接、点击购买、直接下单等话术。"
    rows: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    for attempt in range(2):
        request_prompt = prompt if attempt == 0 else prompt + "\n上次格式不合格。本次顶层只能有 segments；每项只能有 role 和 text，禁止返回 script 字符串或 Markdown。"
        result = await LLMClient().chat_json([{"role": "user", "content": request_prompt}], temperature=.2, max_tokens=2200)
        values = result.get("segments") or result.get("voiceovers") or result.get("narration_segments") or result.get("script") or []
        if isinstance(values, str):
            values = [{"role": "selling_point", "text": text.strip()} for text in re.split(r"[。！？\n]+", values) if text.strip()]
        if isinstance(values, dict):
            values = values.get("segments") or list(values.values())
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str):
                    row = {"role": "selling_point", "text": value}
                elif isinstance(value, dict):
                    row = {"role": value.get("role") or value.get("content_role") or "selling_point",
                           "text": value.get("text") or value.get("content") or value.get("voiceover") or value.get("narration") or ""}
                else:
                    continue
                role_aliases = {"痛点": "pain_point", "卖点": "selling_point", "商品展示": "product_demo",
                                "使用": "usage", "效果": "proof", "证明": "proof", "行动引导": "cta"}
                row["role"] = role_aliases.get(str(row["role"]), str(row["role"]))
                if row["role"] not in ROLE_OBJECTIVES: row["role"] = "selling_point"
                if str(row["text"]).strip(): rows.append(row)
        if rows: break
    if not rows:
        raise HTTPException(502, f"AI 未生成有效补充旁白；返回字段：{', '.join(result.keys()) or '空'}")
    role_order = OBJECTIVE_ROLE_ORDERS.get(project.objective, OBJECTIVE_ROLE_ORDERS["live_stream"])
    role_rank = {role: index for index, role in enumerate(role_order)}
    rows.sort(key=lambda row: role_rank.get(str(row.get("role")), len(role_rank)))
    generated = []
    for index, row in enumerate(rows):
        with tempfile.TemporaryDirectory(prefix="smart-mix-tts-") as directory:
            local = Path(directory) / f"voice-{index:02d}.mp3"
            await asyncio.to_thread(synthesize_speech, str(row["text"]).strip(), local)
            duration = _media_duration(local)
            url = await get_storage().upload(local.read_bytes(), local.name, f"auto-edit/{project.id}/voiceovers")
        generated.append({"role": str(row.get("role") or "selling_point"), "text": str(row["text"]).strip(),
                          "duration": round(duration, 3), "audio_url": url})
    return generated


def _text_match_score(speech: str, highlight: VideoHighlight, role: str) -> float:
    """Cheap, deterministic Chinese semantic guard after the model selection stage."""
    def grams(value: str) -> set[str]:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", value.lower())
        return {cleaned[i:i + 2] for i in range(max(0, len(cleaned) - 1))}
    visual = " ".join([highlight.description or "", *(highlight.selling_points or [])])
    left, right = grams(speech), grams(visual)
    overlap = len(left & right) / max(len(left), 1)
    return overlap * 10 + (3 if highlight.content_role == role else 0) + float(highlight.score or 0)


def _smart_mix_plan(project: VideoEditProject, assets: list[VideoAsset], selected: list[VideoHighlight],
                    voiceovers: list[dict[str, Any]], target_duration: float) -> dict[str, Any]:
    lookup = {asset.id: asset for asset in assets}
    originals = sorted((h for h in selected if h.track_role == "a_roll"),
                       key=lambda h: (0 if h.content_role == "hook" else 2 if h.content_role == "cta" else 1, -h.score))
    b_roll = sorted((h for h in selected if h.track_role == "b_roll"), key=lambda h: -h.score)
    role_order = OBJECTIVE_ROLE_ORDERS.get(project.objective, OBJECTIVE_ROLE_ORDERS["live_stream"])
    role_rank = {role: index for index, role in enumerate(role_order)}
    originals.sort(key=lambda h: (role_rank.get(h.content_role, len(role_rank)), -h.score))
    voiceovers = sorted(voiceovers, key=lambda row: role_rank.get(str(row.get("role")), len(role_rank)))
    variants = []
    for variant_index in range(project.variant_count):
        narrative, cursor = [], 0.0
        # 每个阶段形成连续区块，只能向后推进；阶段内始终真人原声优先。
        for role in role_order:
            for h in (item for item in originals if item.content_role == role):
                duration = min(8.0, h.end_seconds-h.start_seconds)
                if duration < 1: continue
                duration = min(duration, max(0.0, target_duration - sum(row["duration"] for row in voiceovers) - cursor))
                if duration < 1: break
                asset = lookup[h.asset_id]
                narrative.append({"segment_id": f"v{variant_index+1}-original-{len(narrative)+1}", "order": len(narrative)+1,
                    "asset_id": asset.asset_id, "highlight_id": h.highlight_id, "speaker_id": h.speaker_id,
                    "source_range": {"start": h.start_seconds, "end": round(h.start_seconds+duration,3)},
                    "timeline_range": {"start": round(cursor,3), "end": round(cursor+duration,3)}, "duration_seconds": duration,
                    "content_role": h.content_role, "speech_text": h.speech_text, "reason": h.reason, "audio_strategy": "keep_original"})
                cursor += duration
            for row in (item for item in voiceovers if item.get("role") == role):
                duration = row["duration"]
                narrative.append({"segment_id": f"v{variant_index+1}-ai-{len(narrative)+1}", "order": len(narrative)+1,
                    "asset_id": "", "highlight_id": "", "speaker_id": "ai-female",
                    "source_range": {"start": 0, "end": duration}, "timeline_range": {"start": round(cursor,3), "end": round(cursor+duration,3)},
                    "duration_seconds": duration, "content_role": row["role"], "speech_text": row["text"],
                    "reason": "真人原声不足，使用 AI 补充配音", "audio_strategy": "ai_voiceover", "audio_url": row["audio_url"]})
                cursor += duration
        video_track, visual_cursor, b_index = [], 0.0, variant_index
        while visual_cursor < cursor-.05:
            active = next((n for n in narrative if n["timeline_range"]["start"] <= visual_cursor < n["timeline_range"]["end"]), narrative[-1])
            if active.get("audio_strategy") == "keep_original":
                # 原声与原画面天然同步。参考成片也以完整原片段为基本叙事单元，禁止随机 B-roll 覆盖口型。
                offset = visual_cursor - float(active["timeline_range"]["start"])
                piece = float(active["timeline_range"]["end"]) - visual_cursor
                source_start = float(active["source_range"]["start"]) + offset
                video_track.append({"clip_id": f"v{variant_index+1}-source-{len(video_track)+1}",
                    "asset_id": active["asset_id"], "highlight_id": active["highlight_id"],
                    "source_range": {"start": round(source_start,3), "end": round(source_start+piece,3)},
                    "timeline_range": {"start": round(visual_cursor,3), "end": round(visual_cursor+piece,3)},
                    "duration_seconds": round(piece,3), "visual_type": "source_sync", "audio_strategy": "mute",
                    "content_role": active["content_role"]})
                visual_cursor += piece
                continue
            compatible = sorted(b_roll, key=lambda h: _text_match_score(
                active.get("speech_text", ""), h, active.get("content_role", "")), reverse=True)
            if not compatible: raise HTTPException(409, "缺少可用于 AI 配音的辅助画面")
            h = compatible[b_index % len(compatible)]; b_index += 1; asset = lookup[h.asset_id]
            remaining = min(active["timeline_range"]["end"], cursor)-visual_cursor
            piece = min(6.0, h.end_seconds-h.start_seconds, remaining)
            if piece < .04: visual_cursor = active["timeline_range"]["end"]; continue
            video_track.append({"clip_id": f"v{variant_index+1}-b{len(video_track)+1}", "asset_id": asset.asset_id,
                "highlight_id": h.highlight_id, "source_range": {"start": h.start_seconds, "end": round(h.start_seconds+piece,3)},
                "timeline_range": {"start": round(visual_cursor,3), "end": round(visual_cursor+piece,3)}, "duration_seconds": round(piece,3),
                "visual_type": "b_roll", "audio_strategy": "mute", "content_role": active["content_role"]})
            visual_cursor += piece
        variants.append({"variant_id": f"V{variant_index+1}", "name": f"智能混合版本 {variant_index+1}",
            "edit_mode": project.edit_mode,
            "strategy": {"hook_approach":"相关真人原声开场","selection_principle":"语义筛选后由 AI 配音补足叙事","pace":"medium",
                         "average_shot_duration_seconds":round(cursor/max(len(video_track),1),2),"difference_from_other_versions":"辅助画面轮换"},
            "target_duration_seconds":round(target_duration,3),"actual_duration_seconds":round(cursor,3),
            "speaker_count":len({n['speaker_id'] for n in narrative}),"narrative_segments":narrative,"video_track":video_track})
    structure = [{"role": role, "objective": ROLE_OBJECTIVES[role], "required": True} for role in role_order]
    return {"schema_version":"1.1","project_id":str(project.id),
            "brief":{"audio_mode":"smart_mix", "objective": project.objective},"structure":structure,"variants":variants}


def _smart_mix_original_duration(selected: list[VideoHighlight], max_seconds: float) -> float:
    """Mirror the original-voice selection in _smart_mix_plan."""
    originals = sorted((h for h in selected if h.track_role == "a_roll"), key=lambda h: -h.score)
    total = 0.0
    for highlight in originals:
        duration = min(8.0, highlight.end_seconds - highlight.start_seconds, max_seconds - total)
        if duration >= 1:
            total += duration
        if total >= max_seconds - .05:
            break
    return total


def _fit_voiceovers(voiceovers: list[dict[str, Any]], target_seconds: float) -> list[dict[str, Any]]:
    """Keep an ordered prefix whose measured TTS duration is closest to the required gap."""
    if target_seconds <= 0:
        return []
    best: list[dict[str, Any]] = []
    best_delta = target_seconds
    running = 0.0
    for index, row in enumerate(voiceovers):
        running += float(row["duration"])
        delta = abs(target_seconds - running)
        if delta < best_delta:
            best, best_delta = voiceovers[:index + 1], delta
        if running >= target_seconds:
            break
    return best


def _attach_subtitle_cues(db: Session, plan: dict[str, Any], assets: list[VideoAsset]) -> None:
    words_by_asset: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        analysis = db.query(VideoAssetAnalysis).filter(
            VideoAssetAnalysis.asset_id == asset.id, VideoAssetAnalysis.status == "completed"
        ).order_by(VideoAssetAnalysis.id.desc()).first()
        transcript = (analysis.analysis_json or {}).get("transcript") if analysis else {}
        words_by_asset[asset.asset_id] = (transcript or {}).get("words", [])
    for variant in plan.get("variants", []):
        for segment in variant.get("narrative_segments", []):
            source = segment.get("source_range") or {}; timeline = segment.get("timeline_range") or {}
            source_start, source_end = float(source.get("start", 0)), float(source.get("end", 0))
            timeline_start = float(timeline.get("start", 0)); cues = []
            for word in words_by_asset.get(str(segment.get("asset_id")), []):
                word_start, word_end = float(word.get("start", 0)), float(word.get("end", 0))
                if word_end <= source_start or word_start >= source_end: continue
                cues.append({"text": str(word.get("text") or ""),
                    "start": round(timeline_start + max(word_start, source_start) - source_start, 3),
                    "end": round(timeline_start + min(word_end, source_end) - source_start, 3)})
            if cues: segment["subtitle_words"] = cues


def _planning_prompt(project: VideoEditProject, assets: list[VideoAsset], highlights: list[VideoHighlight]) -> str:
    asset_ids = {a.id: a.asset_id for a in assets}
    a_roll = sorted((h for h in highlights if h.track_role == "a_roll"), key=lambda h: -h.score)[:24]
    b_roll = sorted((h for h in highlights if h.track_role == "b_roll"), key=lambda h: -h.score)[:24]
    candidates = [{"asset_id": asset_ids[h.asset_id], "highlight_id": h.highlight_id,
                   "start": round(h.start_seconds, 2), "end": round(h.end_seconds, 2),
                   "description": h.description[:180], "content_role": h.content_role, "track_role": h.track_role,
                   "speaker_id": h.speaker_id, "speech_text": h.speech_text[:320],
                   "selling_points": (h.selling_points or [])[:5], "score": h.score} for h in [*a_roll, *b_roll]]
    profile = OBJECTIVE_PROFILES.get(project.objective, OBJECTIVE_PROFILES["conversion"])
    role_order = OBJECTIVE_ROLE_ORDERS.get(project.objective, OBJECTIVE_ROLE_ORDERS["live_stream"])
    return f"""你是短视频剪辑师。根据候选片段生成严格可执行的 Editing Plan，只返回 JSON。
目标时长：{project.target_duration_seconds} 秒；版本数：{project.variant_count}；结构：{json.dumps(project.structure, ensure_ascii=False)}；目标：{project.objective}；约束：{json.dumps(project.constraints, ensure_ascii=False)}。
成片类型：{profile['label']}。策略要求：{profile['guidance']}
原声叙事阶段必须严格按 {'→'.join(role_order)} 单向排列；同一阶段必须连续，禁止阶段回退、交叉穿插。缺少某阶段时直接跳过。
候选片段：{json.dumps(candidates, ensure_ascii=False)}。
顶层包含 schema_version,project_id,brief,structure,variants。每个版本包含 narrative_segments 原声叙事轨和 video_track 画面轨。
原声轨只能使用 a_roll 的完整句子，保留对应原声，严格按上面给出的阶段顺序形成连贯叙事；品牌信任是独立阶段，不得降级成普通卖点，存在合格品牌/代言人背书且时长允许时必须优先保留；相邻句子必须有承接关系，不能只按分数堆砌。
项目商品是“{project.name}”。先判断每段口播是否直接服务于该商品；腮红、眼影、睫毛、唇蜜、口红等无关彩妆教学不得进入素颜霜促单主线，即使它们分数较高。
画面轨可使用 a_roll 本人画面或静音 b_roll 覆盖，必须全程连续且与原声等长。卖点、使用、证明段优先选择语义匹配的商品/动作/效果 B-roll，不要每句都切回人物；常规镜头建议 3.5～6 秒，只有 Hook 强调点才允许更快切换。最多 8 位达人，不得重复 highlight，不得把其他声音覆盖到明显说话口型。"""


def _selection_prompt(project: VideoEditProject, highlights: list[VideoHighlight]) -> str:
    rows = [{"id": h.highlight_id, "track": h.track_role, "role": h.content_role,
             "start": round(h.start_seconds, 2), "end": round(h.end_seconds, 2),
             "speech": h.speech_text[:360], "visual": h.description[:220],
             "selling_points": (h.selling_points or [])[:5], "score": h.score}
            for h in highlights if h.track_role in {"a_roll", "b_roll"}]
    profile = OBJECTIVE_PROFILES.get(project.objective, OBJECTIVE_PROFILES["conversion"])
    role_order = OBJECTIVE_ROLE_ORDERS.get(project.objective, OBJECTIVE_ROLE_ORDERS["live_stream"])
    return f"""你是短视频总编。为“{project.name}”制作{profile['label']}视频，先做语义选材，只返回 JSON。
目标：{profile['guidance']}
后续叙事顺序：{'→'.join(role_order)}，选材需支持该顺序，不得为了凑时长选入偏题内容。
候选：{json.dumps(rows, ensure_ascii=False)}
返回 selected_a_roll_ids、selected_b_roll_ids、rejected_ids、narrative_outline、selection_notes。
严格规则：
1. 原声必须直接讲当前商品的痛点、素颜霜/精华霜卖点、使用、妆效、优惠或 CTA。
2. 腮红、眼影、睫毛、眉毛、唇蜜、口红等独立彩妆教学一律拒绝，不能因画面好看而保留。
3. 每个保留的 B-roll 必须能支持至少一段保留口播；优先商品、上脸、前后效果、质地和优惠画面。
4. 原声总时长应覆盖约 {project.target_duration_seconds} 秒；不足时如实返回，不得选入偏题内容凑时长。"""


def _filter_selected(highlights: list[VideoHighlight], selection: dict[str, Any]) -> list[VideoHighlight]:
    allowed: set[str] = set()
    for key in ("selected_a_roll_ids", "selected_b_roll_ids", "selected_a_roll", "selected_b_roll"):
        values = selection.get(key) or []
        if isinstance(values, (str, dict)): values = [values]
        for value in values:
            if isinstance(value, dict):
                identifier = value.get("id") or value.get("highlight_id") or value.get("highlightId")
            else:
                identifier = value
            if identifier: allowed.add(str(identifier).strip())
    selected = [highlight for highlight in highlights if highlight.highlight_id in allowed]
    if not any(highlight.track_role == "b_roll" for highlight in selected):
        selected.extend(sorted((highlight for highlight in highlights if highlight.track_role == "b_roll"),
                               key=lambda highlight: -highlight.score)[:24])
    return list({highlight.id: highlight for highlight in selected}.values())


def _subtitle_lines(text: str, max_chars: int = 16) -> list[str]:
    cleaned = re.sub(r"\s+", "", text or "").replace("{", "（").replace("}", "）")
    if not cleaned: return []
    return [cleaned[i:i + max_chars] for i in range(0, len(cleaned), max_chars)][:2]


def _subtitle_cues(text: str, start: float, end: float, max_chars: int = 13) -> list[tuple[str, float, float]]:
    """Build readable phrase-level cues without pretending analysis text is word-aligned ASR."""
    cleaned = re.sub(r"\s+", "", text or "").strip("，。！？；、 ")
    if not cleaned or end <= start: return []
    phrases: list[str] = []
    for clause in re.split(r"(?<=[，。！？；])", cleaned):
        clause = clause.strip()
        while len(clause) > max_chars:
            phrases.append(clause[:max_chars]); clause = clause[max_chars:]
        if clause: phrases.append(clause)
    weights = [max(2.0, len(re.sub(r"[，。！？；、]", "", phrase))) for phrase in phrases]
    total = sum(weights); available = end - start; cursor = start
    cues = []
    for index, (phrase, weight) in enumerate(zip(phrases, weights)):
        cue_end = end if index == len(phrases)-1 else min(end, cursor + available * weight / total)
        cues.append((phrase, cursor, cue_end)); cursor = cue_end
    return cues


def _caption_font(size: int) -> Union[ImageFont.FreeTypeFont, ImageFont.ImageFont]:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _write_caption_images(narrative: list[dict[str, Any]], directory: Path, width: int, height: int) -> list[tuple[Path, float, float]]:
    captions: list[tuple[Path, float, float]] = []
    font_size = max(34, round(height * .029)); font = _caption_font(font_size)
    cue_index = 0
    for segment in narrative:
        timeline = segment.get("timeline_range") or {}
        start, end = float(timeline.get("start", 0)), float(timeline.get("end", 0))
        timed_phrases: list[tuple[str, float, float]] = []
        buffer: list[dict[str, Any]] = []
        for word in segment.get("subtitle_words") or []:
            buffer.append(word)
            combined = "".join(str(item.get("text") or "") for item in buffer)
            if combined.endswith(tuple("，。！？；")) or len(combined) >= 13:
                timed_phrases.append((combined, float(buffer[0]["start"]), float(buffer[-1]["end"])))
                buffer = []
        if buffer:
            timed_phrases.append(("".join(str(item.get("text") or "") for item in buffer),
                                  float(buffer[0]["start"]), float(buffer[-1]["end"])))
        if not timed_phrases:
            timed_phrases = _subtitle_cues(str(segment.get("speech_text") or ""), start, end)
        for phrase, cue_start, cue_end in timed_phrases:
            lines = [phrase]
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0)); draw = ImageDraw.Draw(image)
            spacing = round(font_size * .2)
            boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=2) for line in lines]
            text_width = max(box[2] - box[0] for box in boxes)
            text_height = sum(box[3] - box[1] for box in boxes) + spacing * (len(lines) - 1)
            pad_x, pad_y = round(font_size * .42), round(font_size * .22)
            left = (width - text_width) // 2 - pad_x; top = height - round(height * .105) - text_height - pad_y
            draw.rounded_rectangle((left, top, left + text_width + pad_x * 2, top + text_height + pad_y * 2), radius=round(font_size*.28), fill=(12, 12, 12, 118))
            y = top + pad_y
            for line, box in zip(lines, boxes):
                line_width, line_height = box[2] - box[0], box[3] - box[1]
                draw.text(((width-line_width)//2, y), line, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 175))
                y += line_height + spacing
            path = directory / f"caption-{cue_index:03d}.png"; image.save(path)
            captions.append((path, cue_start, cue_end)); cue_index += 1
    return captions


@router.post("/{project_id}/plan")
async def generate_plan(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    project = _project(db, auth, project_id)
    role_order = OBJECTIVE_ROLE_ORDERS.get(project.objective, OBJECTIVE_ROLE_ORDERS["live_stream"])
    project.structure = [{"role": role, "objective": ROLE_OBJECTIVES[role], "required": True} for role in role_order]
    assets = _eligible_assets_query(
        db, auth.tenant_id, project.edit_mode, product_id=project.product_id, project_id=project.id
    ).all()
    if not assets: raise HTTPException(409, "请先上传视频素材")
    highlights = db.query(VideoHighlight).filter(
        VideoHighlight.tenant_id == auth.tenant_id,
        VideoHighlight.asset_id.in_([a.id for a in assets]),
    ).all()
    if not highlights: raise HTTPException(409, "素材没有可用时间段")
    if get_settings().llm_mock_mode:
        selected = highlights
        selection = {"selection_notes": "mock mode"}
    else:
        try:
            selection = await LLMClient().chat_json(
                [{"role": "user", "content": _selection_prompt(project, highlights)}], temperature=.1, max_tokens=3000)
            selected = _filter_selected(highlights, selection)
        except Exception as exc:
            logger.exception("AI material selection failed: %s", exc)
            raise HTTPException(502, f"AI 语义选材失败：{str(exc)[:300]}") from exc
    selected_a_roll = [h for h in selected if h.track_role == "a_roll"]
    selected_b_roll = [h for h in selected if h.track_role == "b_roll"]
    selected_seconds = sum(h.end_seconds-h.start_seconds for h in selected_a_roll)
    if project.edit_mode == "finished_remix":
        if any(h.track_role == "a_roll" and not str(h.speech_text or "").strip() for h in selected):
            selected = [h for h in selected if not (h.track_role == "a_roll" and not str(h.speech_text or "").strip())]
        effective_duration = min(float(project.target_duration_seconds), sum(_usable_duration(h) for h in selected if h.track_role == "a_roll"))
        if effective_duration < 15:
            raise HTTPException(409, "成品素材中缺少至少 15 秒与当前商品相关的完整语义段")
        plan = _finished_remix_plan(project, assets, selected, effective_duration)
        model_name = "finished-segment-framework-v1"
    elif selected_seconds < project.target_duration_seconds - 5:
        if get_settings().auto_edit_audio_mode != "smart_mix":
            raise HTTPException(409, f"与当前商品相关的原声仅约 {selected_seconds:.0f} 秒，不足以生成 {project.target_duration_seconds} 秒成片")
        voiceover_pool: list[dict[str, Any]] = []
        # AI 配音最多约 24 秒。相关真人原声不足时缩短成片，避免为凑 90 秒灌水。
        expected_original = _smart_mix_original_duration(selected, max(0.0, project.target_duration_seconds - 12.0))
        # 参考片以真人/素材原声为主，AI 只承担短转场或结尾补充。
        target_voiceover = min(10.0, max(0.0, project.target_duration_seconds - expected_original))
        voiceovers: list[dict[str, Any]] = []
        for _ in range(6):
            voiceovers = _fit_voiceovers(voiceover_pool, target_voiceover)
            missing = target_voiceover-sum(row["duration"] for row in voiceovers)
            if missing <= 5: break
            voiceover_pool.extend(await _create_ai_voiceovers(project, missing))
        voiceovers = _fit_voiceovers(voiceover_pool, target_voiceover)
        generated_duration = expected_original + sum(row["duration"] for row in voiceovers)
        if generated_duration < 15:
            raise HTTPException(502, f"相关真人原声和 AI 配音合计仅约 {generated_duration:.1f} 秒，无法形成连贯成片")
        plan, model_name = _smart_mix_plan(project, assets, selected, voiceovers, generated_duration), f"{get_settings().llm_model}+doubao-tts"
    else:
        try:
            fallback = _fallback_plan(project, assets, selected)
            candidate = await LLMClient().chat_json(
                [{"role": "user", "content": _planning_prompt(project, assets, selected)}], temperature=.2, max_tokens=8000)
            candidate_errors = _validate_plan(candidate, assets)
            if len(candidate.get("variants", [])) == project.variant_count and not candidate_errors:
                plan, model_name = candidate, get_settings().llm_model
            else:
                logger.warning("AI editing plan rejected: %s", candidate_errors[:10])
                plan, model_name = fallback, "semantic-selection+deterministic-timeline"
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("AI timeline planning failed after semantic selection: %s", exc)
            plan, model_name = _fallback_plan(project, assets, selected), "semantic-selection+deterministic-timeline"
    plan["material_selection"] = {"a_roll_count": len(selected_a_roll), "b_roll_count": len(selected_b_roll),
                                  "a_roll_seconds": round(selected_seconds, 1), "notes": selection.get("selection_notes", "")}
    _attach_subtitle_cues(db, plan, assets)
    if project.edit_mode == "finished_remix":
        for variant in plan.get("variants", []):
            for segment in variant.get("narrative_segments", []):
                segment.pop("subtitle_words", None)
    errors = _validate_plan(plan, assets)
    version = (db.query(func.max(EditingPlanVersion.version)).filter(EditingPlanVersion.project_id == project.id).scalar() or 0) + 1
    record = EditingPlanVersion(tenant_id=auth.tenant_id, project_id=project.id, version=version, plan_json=plan,
        model=model_name, validation_status="invalid" if errors else "valid", validation_errors=errors)
    db.add(record); project.status = "planned" if not errors else "plan_invalid"; db.commit(); db.refresh(project)
    return _serialize(db, project)


def finished_clip_ffmpeg_command(source: Union[Path, str], start: float, duration: float,
                                 output: Union[Path, str], width: int, height: int) -> list[str]:
    return [
        "ffmpeg", "-y", "-ss", str(start), "-t", str(duration), "-i", str(source),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-af", "loudnorm=I=-16:LRA=7:TP=-1.5,aresample=async=1:first_pts=0",
        "-movflags", "+faststart", str(output),
    ]


def remix_render_uses_system_captions(edit_mode: str) -> bool:
    return edit_mode != "finished_remix"


def remix_render_adds_public_bgm(edit_mode: str) -> bool:
    return edit_mode != "finished_remix"


def _render_variant(plan: dict[str, Any], assets: list[VideoAsset], output: Path, aspect_ratio: str) -> float:
    asset_map = {a.asset_id: a for a in assets}
    storage = get_storage()
    width, height = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}[aspect_ratio]
    edit_mode = str(plan.get("edit_mode") or "")
    if edit_mode == "finished_remix":
        av_segments: list[Path] = []
        for index, clip in enumerate(plan["narrative_segments"]):
            asset = asset_map[clip["asset_id"]]
            source = storage.local_path(asset.object_url)
            if not source:
                raise RuntimeError(f"无法下载素材 {asset.filename}")
            if not asset.has_audio:
                raise RuntimeError(f"成品片段缺少完整音轨：{asset.filename}")
            start, end = clip["source_range"]["start"], clip["source_range"]["end"]
            segment = output.parent / f"av-{index:03d}.mp4"
            av_segments.append(segment)
            subprocess.run(
                finished_clip_ffmpeg_command(source, start, end - start, segment, width, height),
                capture_output=True, text=True, timeout=300, check=True,
            )
        concat_list = output.parent / "av-concat.txt"
        concat_list.write_text("\n".join(f"file '{path.as_posix()}'" for path in av_segments), encoding="utf-8")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", "-movflags", "+faststart", str(output)],
            capture_output=True, text=True, timeout=600, check=True,
        )
        return float(plan["actual_duration_seconds"])
    video_segments: list[Path] = []; audio_segments: list[Path] = []
    for index, clip in enumerate(plan["video_track"]):
        asset = asset_map[clip["asset_id"]]
        source = storage.local_path(asset.object_url)
        if not source: raise RuntimeError(f"无法下载素材 {asset.filename}")
        start, end = clip["source_range"]["start"], clip["source_range"]["end"]
        duration = end - start
        segment = output.parent / f"video-{index:03d}.mp4"; video_segments.append(segment)
        command = ["ffmpeg", "-y", "-ss", str(start), "-t", str(duration), "-i", str(source), "-an",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-movflags", "+faststart", str(segment)]
        subprocess.run(command, capture_output=True, text=True, timeout=300, check=True)
    for index, clip in enumerate(plan["narrative_segments"]):
        if clip.get("audio_strategy") == "ai_voiceover":
            source = storage.local_path(clip["audio_url"])
            if not source: raise RuntimeError("无法下载 AI 配音")
            start, end = 0.0, float(clip["duration_seconds"])
        else:
            asset = asset_map[clip["asset_id"]]
            if not asset.has_audio: raise RuntimeError(f"原声片段缺少音轨：{asset.filename}")
            source = storage.local_path(asset.object_url)
            start, end = clip["source_range"]["start"], clip["source_range"]["end"]
        segment = output.parent / f"audio-{index:03d}.wav"; audio_segments.append(segment)
        subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(end-start), "-i", str(source), "-vn",
            "-af", "loudnorm=I=-16:LRA=7:TP=-1.5,aresample=async=1:first_pts=0", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(segment)],
            capture_output=True, text=True, timeout=300, check=True)
    video_list = output.parent / "video-concat.txt"; audio_list = output.parent / "audio-concat.txt"
    video_list.write_text("\n".join(f"file '{p.as_posix()}'" for p in video_segments), encoding="utf-8")
    audio_list.write_text("\n".join(f"file '{p.as_posix()}'" for p in audio_segments), encoding="utf-8")
    video_joined = output.parent / "video-track.mp4"; audio_joined = output.parent / "narrative.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(video_list), "-c", "copy", str(video_joined)], capture_output=True, text=True, timeout=600, check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(audio_list), "-c", "copy", str(audio_joined)], capture_output=True, text=True, timeout=600, check=True)
    # A restrained, license-free ambient bed. It is ducked under speech in the final mix.
    bgm = output.parent / "background-music.wav"
    duration = float(plan["actual_duration_seconds"])
    bgm_source = (
        f"aevalsrc=0.08*sin(2*PI*220*t)+0.055*sin(2*PI*277.18*t)+"
        f"0.04*sin(2*PI*329.63*t):s=48000:d={duration:.3f}"
    )
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", bgm_source,
                    "-af", "lowpass=f=900,afade=t=in:st=0:d=1.5,afade=t=out:st=" + str(max(0.0, duration-2)) + ":d=2",
                    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(bgm)],
                   capture_output=True, text=True, timeout=120, check=True)
    command = ["ffmpeg", "-y", "-i", str(video_joined), "-i", str(audio_joined), "-i", str(bgm)]
    captions = _write_caption_images(plan["narrative_segments"], output.parent, width, height)
    filters = ["[2:a][1:a]sidechaincompress=threshold=0.025:ratio=10:attack=15:release=450[ducked]",
               "[ducked]volume=0.16[bed]", "[1:a][bed]amix=inputs=2:duration=first:dropout_transition=2[aout]"]
    if captions:
        for path, _, _ in captions: command += ["-i", str(path)]
        current = "[0:v]"
        for index, (_, start, end) in enumerate(captions):
            target = f"[caption{index}]"
            filters.append(f"{current}[{index+3}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'{target}")
            current = target
        command += ["-filter_complex", ";".join(filters), "-map", current, "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "192k"]
    else:
        command += ["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    command += ["-shortest", "-movflags", "+faststart", str(output)]
    subprocess.run(command, capture_output=True, text=True, timeout=600, check=True)
    return float(plan["actual_duration_seconds"])


@router.post("/{project_id}/render")
async def render(project_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    project = _project(db, auth, project_id)
    record = db.query(EditingPlanVersion).filter(EditingPlanVersion.project_id == project.id, EditingPlanVersion.tenant_id == auth.tenant_id).order_by(EditingPlanVersion.version.desc()).first()
    if not record or record.validation_status != "valid": raise HTTPException(409, "请先生成有效的剪辑方案")
    assets = _eligible_assets_query(
        db, auth.tenant_id, project.edit_mode, product_id=project.product_id, project_id=project.id
    ).all()
    asset_by_public_id = {asset.asset_id: asset for asset in assets}
    db.query(VideoRenderJob).filter(VideoRenderJob.project_id == project.id, VideoRenderJob.tenant_id == auth.tenant_id).delete(synchronize_session=False)
    project.status = "rendering"; db.commit()
    for variant in record.plan_json.get("variants", []):
        job = VideoRenderJob(tenant_id=auth.tenant_id, project_id=project.id, plan_version_id=record.id,
                             variant_id=variant["variant_id"], status="rendering")
        db.add(job); db.commit(); db.refresh(job)
        try:
            with tempfile.TemporaryDirectory(prefix=f"auto-edit-{project.id}-") as directory:
                output = Path(directory) / f"{variant['variant_id']}.mp4"
                duration = await asyncio.to_thread(_render_variant, variant, assets, output, project.aspect_ratio)
                rendered = output.read_bytes()
                url = await get_storage().upload(rendered, output.name, f"auto-edit/{project.id}/outputs")
                origin_ids = origin_ids_from_variant(variant)
                source_assets = [asset_by_public_id[asset_id] for asset_id in origin_ids if asset_id in asset_by_public_id]
                metadata = finished_output_metadata(source_assets, origin_ids)
                if project.edit_mode == "smart_compose":
                    metadata["has_background_music"] = True
                    metadata["burned_subtitles"] = True
                generated_asset = VideoAsset(
                    tenant_id=auth.tenant_id, project_id=project.id, product_id=project.product_id,
                    asset_id=f"asset-{uuid.uuid4().hex[:12]}", filename=f"{project.name}-{variant['variant_id']}.mp4",
                    source="smart_compose_output" if project.edit_mode == "smart_compose" else "finished_remix_output",
                    library_type="finished", asset_kind="final_output",
                    generation_depth=metadata["generation_depth"],
                    origin_asset_ids=metadata["origin_asset_ids"],
                    reusable_status=metadata["reusable_status"],
                    burned_subtitles=metadata["burned_subtitles"],
                    has_background_music=metadata["has_background_music"],
                    object_url=url, content_hash=hashlib.sha256(rendered).hexdigest(),
                    duration_seconds=duration,
                    width=1080 if project.aspect_ratio != "16:9" else 1920,
                    height=1920 if project.aspect_ratio == "9:16" else 1080,
                    fps=30, has_audio=True, status="queued_for_analysis", excluded=False,
                )
                db.add(generated_asset)
                inherit_public_tags_to_final(db, generated_asset, source_assets, auth.user_id)
            job.status = "completed"; job.output_url = url; job.duration_seconds = duration
        except Exception as exc:
            job.status = "failed"; job.error_message = str(exc)[:1000]
        db.commit(); enqueue_video_analysis()
    failed = db.query(VideoRenderJob).filter(VideoRenderJob.project_id == project.id, VideoRenderJob.status == "failed").count()
    project.status = "completed" if not failed else "partially_failed"; db.commit(); db.refresh(project)
    return _serialize(db, project)
