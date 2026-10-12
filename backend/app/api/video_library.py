"""Product-level video material library with cached multimodal analysis."""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auto_edit import _probe
from app.auth import AuthContext, current_auth
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.models import Product, Tenant, VideoAsset, VideoAssetAnalysis, VideoEditProject, VideoEditProjectAsset, VideoHighlight, VideoUploadSession
from app.services.llm import LLMClient
from app.services.storage import get_storage_by_name, get_storage_for_url, get_video_storage
from app.services.transcription import transcribe_media
from app.services.video_analysis_queue import enqueue_video_analysis
from app.services.video_taxonomy import load_asset_taxonomy
from app.services.video_lineage import reference_counts

router = APIRouter(prefix="/video-assets", tags=["video-library"])
PROMPT_VERSION = "video-library-v3-whisper-timestamps"
REUSABLE_STATUSES = {"review_required", "reusable", "excluded", "blocked"}


class ExcludeUpdate(BaseModel):
    excluded: bool


class TagsUpdate(BaseModel):
    manual_tags: list[str]
    removed_ai_tags: list[str]


class ReusableStatusUpdate(BaseModel):
    reusable_status: str = Field(pattern="^(review_required|reusable|excluded|blocked)$")


class MultipartStart(BaseModel):
    product_id: int
    filename: str = Field(min_length=1, max_length=512)
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(gt=0)
    source: str = "manual_upload"
    library_type: str = Field(default="semi_finished", pattern="^(semi_finished|finished)$")


class MultipartComplete(BaseModel):
    session_id: str


_NOT_LOADED = object()


def _tenant_video_storage(db: Session, tenant_id: str):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    return get_video_storage(tenant_id, tenant.code if tenant else "")


def _asset_storage(asset: VideoAsset):
    provider = getattr(asset, "storage_provider", "")
    return get_storage_by_name(provider) if provider else get_storage_for_url(asset.object_url)


def _serialize(db: Session, asset: VideoAsset, analysis: Any = _NOT_LOADED,
               highlights: Any = _NOT_LOADED, taxonomy_category: Any = _NOT_LOADED,
               taxonomy_tags: Any = _NOT_LOADED, user_id: str = "",
               reference_count: Any = _NOT_LOADED) -> dict[str, Any]:
    if reference_count is _NOT_LOADED:
        reference_count = reference_counts(db, asset.tenant_id, [asset.id]).get(asset.id, 0)
    if analysis is _NOT_LOADED:
        analysis = db.query(VideoAssetAnalysis).filter(VideoAssetAnalysis.asset_id == asset.id).order_by(VideoAssetAnalysis.id.desc()).first()
    if highlights is _NOT_LOADED:
        highlights = db.query(VideoHighlight).filter(VideoHighlight.asset_id == asset.id).order_by(VideoHighlight.start_seconds).all()
    if taxonomy_category is _NOT_LOADED or taxonomy_tags is _NOT_LOADED:
        categories, tags = load_asset_taxonomy(db, [asset], asset.tenant_id, user_id)
        taxonomy_category = categories.get(asset.id)
        taxonomy_tags = tags.get(asset.id, [])
    effective_tags = list(dict.fromkeys([tag for tag in (asset.ai_tags or []) if tag not in (asset.removed_ai_tags or [])] + (asset.manual_tags or [])))
    return {"id": asset.id, "asset_id": asset.asset_id, "product_id": asset.product_id, "filename": asset.filename,
            "source": asset.source, "library_type": asset.library_type, "asset_kind": asset.asset_kind,
            "reference_count": reference_count,
            "category_id": asset.category_id, "category": taxonomy_category,
            "generation_depth": asset.generation_depth,
            "origin_asset_ids": asset.origin_asset_ids or [], "reusable_status": asset.reusable_status,
            "burned_subtitles": asset.burned_subtitles, "has_background_music": bool(asset.has_background_music),
            "object_url": asset.object_url, "storage_provider": asset.storage_provider,
            "duration_seconds": asset.duration_seconds,
            "width": asset.width, "height": asset.height, "fps": asset.fps, "has_audio": asset.has_audio,
            "status": asset.status, "excluded": asset.excluded,
            "created_at": asset.created_at.replace(tzinfo=timezone.utc).isoformat(),
            "ai_tags": asset.ai_tags or [],
            "manual_tags": asset.manual_tags or [], "removed_ai_tags": asset.removed_ai_tags or [], "effective_tags": effective_tags,
            "taxonomy_tags": taxonomy_tags,
            "public_tags": [tag for tag in taxonomy_tags if tag["scope"] == "public"],
            "personal_tags": [tag for tag in taxonomy_tags if tag["scope"] == "personal"],
            "analysis": None if not analysis else {"id": analysis.id, "status": analysis.status, "summary": analysis.summary,
                "model": analysis.model, "prompt_version": analysis.prompt_version, "analysis_json": analysis.analysis_json,
                "request_count": analysis.request_count, "retry_count": analysis.retry_count, "error_message": analysis.error_message},
            "highlights": [{"highlight_id": h.highlight_id, "start": h.start_seconds, "end": h.end_seconds,
                "description": h.description, "reason": h.reason, "recommended_roles": h.recommended_roles,
                "content_role": h.content_role, "track_role": h.track_role, "speech_present": h.speech_present,
                "lip_visible": h.lip_visible, "speech_text": h.speech_text, "speaker_id": h.speaker_id,
                "audio_strategy": h.audio_strategy, "shot_type": h.shot_type, "selling_points": h.selling_points,
                "score": h.score} for h in highlights]}


def _matching_edit_mode(library_type: str) -> str:
    return "finished_remix" if library_type == "finished" else "smart_compose"


def _link_asset_to_matching_projects(db: Session, asset: VideoAsset) -> None:
    edit_mode = _matching_edit_mode(asset.library_type)
    if edit_mode == "finished_remix" and (
        asset.reusable_status != "reusable" or asset.generation_depth > 1 or asset.excluded
    ):
        return
    projects = db.query(VideoEditProject).filter(
        VideoEditProject.tenant_id == asset.tenant_id,
        VideoEditProject.product_id == asset.product_id,
        VideoEditProject.edit_mode == edit_mode,
    ).all()
    for project in projects:
        exists = db.query(VideoEditProjectAsset).filter(
            VideoEditProjectAsset.tenant_id == asset.tenant_id,
            VideoEditProjectAsset.project_id == project.id,
            VideoEditProjectAsset.asset_id == asset.id,
        ).first()
        if not exists:
            db.add(VideoEditProjectAsset(
                tenant_id=asset.tenant_id, project_id=project.id, asset_id=asset.id, included=True,
            ))


def _apply_reusable_status(asset: VideoAsset, reusable_status: str) -> None:
    """Keep the review state and the generic exclusion flag consistent."""
    asset.reusable_status = reusable_status
    asset.excluded = reusable_status == "excluded"


def _split_finished_span(start: float, end: float, words: list[dict[str, Any]],
                         max_len: float = 15.0, min_len: float = 4.0) -> list[tuple[float, float]]:
    duration = end - start
    if duration <= max_len:
        return [(start, end)]
    boundaries = [start]
    for word in words:
        try:
            word_end = float(word.get("end", 0))
        except (TypeError, ValueError):
            continue
        text = str(word.get("text") or "")
        if start < word_end <= end and (text.endswith(tuple("。！？；")) or word_end - boundaries[-1] >= max_len):
            if min_len <= word_end - boundaries[-1] <= max_len + 0.5:
                boundaries.append(word_end)
    cursor = boundaries[-1]
    while end - cursor > max_len:
        cursor = round(cursor + max_len, 3)
        boundaries.append(cursor)
    if boundaries[-1] != end:
        if end - boundaries[-1] < min_len and len(boundaries) > 1:
            boundaries[-1] = end
        else:
            boundaries.append(end)
    spans = [(boundaries[index], boundaries[index + 1]) for index in range(len(boundaries) - 1)
             if boundaries[index + 1] - boundaries[index] >= 0.4]
    return spans or [(start, end)]


def _extract_frames(source: Path, duration: float, target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    interval = max(1.0, duration / 6)
    subprocess.run(["ffmpeg", "-y", "-i", str(source), "-vf", f"fps=1/{interval},scale=640:-2",
                    "-frames:v", "6", str(target / "frame-%02d.jpg")], capture_output=True, text=True, timeout=180, check=True)
    return sorted(target.glob("frame-*.jpg"))


def _is_finished_for_analysis(asset: VideoAsset) -> bool:
    return asset.library_type == "finished" or (
        asset.source == "video_repair_upload" and asset.asset_kind == "final_output")


def _normalize_analysis(asset: VideoAsset, result: dict[str, Any], transcript: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    highlights = []
    words = (transcript or {}).get("words", [])
    highlight_index = 0
    for item in result.get("highlights") or []:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        start, end = max(0.0, start), min(asset.duration_seconds, end)
        if end - start < .4:
            continue
        spans = _split_finished_span(start, end, words) if _is_finished_for_analysis(asset) else [(start, end)]
        roles = item.get("recommended_positions") or item.get("recommended_roles") or ["core_content"]
        for span_start, span_end in spans:
            matching_words = [word for word in words
                              if float(word.get("end", 0)) > span_start and float(word.get("start", 0)) < span_end]
            exact_speech = "".join(str(word.get("text") or "") for word in matching_words).strip()
            speech_present = bool(exact_speech or item.get("speech_present"))
            lip_visible = bool(item.get("lip_visible"))
            track_role = str(item.get("track_role") or ("a_roll" if speech_present and (lip_visible or _is_finished_for_analysis(asset)) else "b_roll"))
            if track_role == "a_roll" and (not speech_present or (not _is_finished_for_analysis(asset) and not lip_visible) or not (exact_speech or str(item.get("speech_text") or "").strip())):
                track_role = "reject"
            content_role = str(item.get("content_role") or roles[0])
            highlight_index += 1
            highlight = {"highlight_id": f"{asset.asset_id}-h{highlight_index:02d}", "start": round(span_start, 3), "end": round(span_end, 3),
                "description": str(item.get("description") or "有价值候选片段"), "reason": str(item.get("reason") or "模型判断具有剪辑价值"),
                "recommended_roles": [str(role) for role in roles], "content_role": content_role, "track_role": track_role,
                "speech_present": speech_present, "lip_visible": lip_visible, "speech_text": exact_speech or str(item.get("speech_text") or ""),
                "speaker_id": str(item.get("speaker_id") or (f"{asset.asset_id}-speaker-1" if track_role == "a_roll" else "")),
                "audio_strategy": "keep_original" if track_role == "a_roll" else "mute",
                "shot_type": str(item.get("shot_type") or "unknown"), "selling_points": [str(x) for x in item.get("selling_points", [])],
                "score": min(1.0, max(0.0, float(item.get("score", .7))))}
            highlights.append(highlight)
    if not highlights:
        raise ValueError("模型没有返回合法 highlights")
    automatic_tags = [str(tag).strip() for tag in (result.get("video_tags") or result.get("material_types") or []) if str(tag).strip()]
    automatic_tags += ["原声讲解" if item["track_role"] == "a_roll" else "辅助画面" for item in highlights if item["track_role"] in {"a_roll", "b_roll"}]
    role_labels = {"hook": "Hook", "pain_point": "痛点", "selling_point": "核心卖点", "brand_trust": "品牌信任", "usage": "使用过程", "proof": "效果证明", "product_demo": "商品展示", "cta": "CTA"}
    automatic_tags += [role_labels.get(item["content_role"], item["content_role"]) for item in highlights if item["track_role"] != "reject"]
    result["video_tags"] = list(dict.fromkeys(automatic_tags))[:20]
    result["schema_version"] = "1.0"; result["asset_id"] = asset.asset_id; result["highlights"] = highlights
    result["library_type"] = asset.library_type
    asset.burned_subtitles = bool(result.get("burned_subtitles", _is_finished_for_analysis(asset)))
    asset.has_background_music = bool(result.get("has_background_music", False))
    if asset.generation_depth > 1:
        asset.reusable_status = "blocked"
        result["remix_suitable"] = False
    else:
        result["remix_suitable"] = bool(result.get("remix_suitable", True))
    if transcript:
        result["transcript"] = transcript
    return result


async def analyze_video_asset(asset_db_id: int, force: bool = False) -> None:
    db = SessionLocal()
    try:
        asset = db.query(VideoAsset).filter(VideoAsset.id == asset_db_id).first()
        if not asset or asset.excluded:
            return
        prompt_version = PROMPT_VERSION
        cached = db.query(VideoAssetAnalysis).filter(VideoAssetAnalysis.asset_id == asset.id,
            VideoAssetAnalysis.model == get_settings().llm_vision_model,
            VideoAssetAnalysis.prompt_version == prompt_version,
            VideoAssetAnalysis.status == "completed").order_by(VideoAssetAnalysis.id.desc()).first()
        if cached and not force and (cached.analysis_json or {}).get("source_hash") == asset.content_hash:
            asset.status = "analyzed"; db.commit(); return
        analysis = VideoAssetAnalysis(tenant_id=asset.tenant_id, asset_id=asset.id, schema_version="1.0",
            model=get_settings().llm_vision_model, prompt_version=prompt_version, status="analyzing",
            summary="", analysis_json={}, input_video_seconds=asset.duration_seconds, request_count=0, retry_count=0)
        db.add(analysis); asset.status = "analyzing"; db.commit(); db.refresh(analysis)
        transcript: dict[str, Any] = {}
        transcript_error = ""
        if asset.has_audio and get_settings().whisper_enabled:
            try:
                source = _asset_storage(asset).local_path(asset.object_url)
                if not source: raise RuntimeError("无法从 OSS 获取音频转写源文件")
                product = db.query(Product).filter(Product.id == asset.product_id).first()
                vocabulary = f"品牌和商品专有词：{product.name if product else ''}。美妆、素颜霜、精华霜、遮瑕、妆效、持妆、促销、直播间。"
                transcript = await asyncio.to_thread(transcribe_media, source, vocabulary)
            except Exception as exc:
                transcript_error = str(exc)[:500]
        last_error = ""
        for attempt in range(2):
            try:
                analysis.request_count += 1; analysis.retry_count = attempt; db.commit()
                material_instruction = ("这是已剪辑成品。按完整语义句、原始镜头边界和自然停顿切成 4～15 秒的可复用段落；"
                    "每段原声必须与原画面绑定，即使说话者没有出镜也标为 a_roll；识别是否存在烧录字幕和原 BGM。"
                    if _is_finished_for_analysis(asset) else
                    "这是半成品素材。区分可保留原声的人物口播 a_roll 与可静音重配的商品/使用/效果 b_roll。")
                prompt = f"""分析一条时长 {asset.duration_seconds:.3f} 秒的美妆短视频。{material_instruction}
只返回 JSON，包含 summary, video_tags, people{{count,description}}, product{{visible,name,exposure}}, actions, scenes,
speech{{summary,selling_points}}, emotion{{intensity,description}}, material_types, recommended_positions,
usability{{score,summary,issues}}, burned_subtitles, has_background_music, highlights。
highlights 每项必须包含 start,end,description,reason,recommended_positions,content_role,track_role,speech_present,
lip_visible,speech_text,speaker_id,shot_type,selling_points,score；时间必须在 0 到 {asset.duration_seconds:.3f} 秒内。
content_role 使用 hook、pain_point、selling_point、brand_trust、usage、proof、product_demo、cta；代言人、品牌身份、权威背书或品牌信任场景标为 brand_trust；track_role 只能为 a_roll、b_roll、reject。
有清晰讲话、明显口型、音画匹配且句子完整才是 a_roll；商品、质地、上妆、效果等辅助画面是 b_roll；
有口型但声音不匹配、句子残缺的片段标记 reject。a_roll 必须保留完整 speech_text 和稳定 speaker_id。不要虚构看不清或听不清的信息。"""
                if attempt == 0:
                    # Prefer the complete video so motion and speech can be understood.
                    signed = _asset_storage(asset).signed_url(asset.object_url)
                    result = await LLMClient().chat_json([{"role": "user", "content": [
                        {"type": "text", "text": prompt}, {"type": "video_url", "video_url": {"url": signed}}
                    ]}], temperature=.2, max_tokens=4096)
                else:
                    # Compatibility fallback for endpoints without video_url support.
                    source = _asset_storage(asset).local_path(asset.object_url)
                    if not source: raise RuntimeError("无法从 OSS 获取视频")
                    with tempfile.TemporaryDirectory(prefix=f"analyze-{asset.id}-") as directory:
                        frames = _extract_frames(source, asset.duration_seconds, Path(directory))
                        result = await LLMClient().chat_vision(prompt + "当前输入仅为均匀抽取的关键帧；无法确认动作边界时使用镜头区间，无法确认的口播和商品可见程度请标为不确定。",
                            [str(frame) for frame in frames], temperature=.2, max_tokens=4096, as_json=True)
                normalized = _normalize_analysis(asset, result, transcript)
                normalized["source_hash"] = asset.content_hash
                if transcript_error:
                    normalized["transcript_error"] = transcript_error
                db.query(VideoHighlight).filter(VideoHighlight.asset_id == asset.id).delete(synchronize_session=False)
                analysis.summary = str(normalized.get("summary") or "分析完成")
                analysis.analysis_json = normalized; analysis.status = "completed"; analysis.completed_at = datetime.utcnow(); analysis.error_message = ""
                asset.ai_tags = normalized["video_tags"]
                db.flush()
                for item in normalized["highlights"]:
                    db.add(VideoHighlight(tenant_id=asset.tenant_id, asset_id=asset.id, analysis_id=analysis.id,
                        highlight_id=item["highlight_id"], start_seconds=item["start"], end_seconds=item["end"],
                        description=item["description"], reason=item["reason"], recommended_roles=item["recommended_roles"],
                        content_role=item["content_role"], track_role=item["track_role"], speech_present=item["speech_present"],
                        lip_visible=item["lip_visible"], speech_text=item["speech_text"], speaker_id=item["speaker_id"],
                        audio_strategy=item["audio_strategy"], shot_type=item["shot_type"], selling_points=item["selling_points"], score=item["score"]))
                asset.status = "analyzed"; db.commit(); return
            except Exception as exc:
                last_error = str(exc)[:1000]
        analysis.status = "failed"; analysis.error_message = last_error; analysis.completed_at = datetime.utcnow()
        asset.status = "analysis_failed"; db.commit()
    finally:
        db.close()


def validate_video_asset(asset_db_id: int) -> None:
    db = SessionLocal()
    try:
        asset = db.query(VideoAsset).filter(VideoAsset.id == asset_db_id, VideoAsset.status == "validating").first()
        if not asset:
            return
        try:
            media = _probe(_asset_storage(asset).signed_url(asset.object_url))
            max_duration = get_settings().video_library_max_duration_seconds
            if media["duration"] > max_duration + .05:
                raise ValueError(f"视频超过 {max_duration / 60:g} 分钟限制")
            if asset.library_type == "finished":
                local = _asset_storage(asset).local_path(asset.object_url)
                if not local:
                    raise ValueError("无法读取成品素材")
                digest = hashlib.sha256()
                with Path(local).open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                asset.content_hash = digest.hexdigest()
            asset.duration_seconds = media["duration"]; asset.width = media["width"]; asset.height = media["height"]
            asset.fps = media["fps"]; asset.has_audio = media["has_audio"]; asset.status = "queued_for_analysis"
        except Exception as exc:
            db.add(VideoAssetAnalysis(
                tenant_id=asset.tenant_id,
                asset_id=asset.id,
                model="media-validation",
                prompt_version="ffprobe-v1",
                status="failed",
                error_message=str(getattr(exc, "detail", exc))[:1000],
                completed_at=datetime.utcnow(),
            ))
            asset.status = "analysis_failed"
        db.commit()
    finally:
        db.close()


@router.post("/multipart/start")
async def start_multipart(payload: MultipartStart, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    product = db.query(Product).filter(Product.id == payload.product_id, Product.tenant_id == auth.tenant_id).first()
    if not product: raise HTTPException(404, "商品不存在")
    if not payload.content_type.startswith("video/") and Path(payload.filename).suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}:
        raise HTTPException(415, "只支持视频文件")
    part_size = 16 * 1024 * 1024
    part_count = (payload.size_bytes + part_size - 1) // part_size
    if part_count > 10000: raise HTTPException(413, "文件过大")
    storage = _tenant_video_storage(db, auth.tenant_id)
    key, upload_id = await storage.begin_multipart(payload.filename, f"video-library/{product.id}/source", payload.content_type)
    session = VideoUploadSession(id=uuid.uuid4().hex, tenant_id=auth.tenant_id, product_id=product.id, library_type=payload.library_type,
        filename=payload.filename, content_type=payload.content_type, size_bytes=payload.size_bytes, object_key=key,
        multipart_upload_id=upload_id, storage_provider=storage.name,
        part_size=part_size, part_count=part_count, status="uploading")
    db.add(session); db.commit()
    urls = [storage.signed_part_url(key, upload_id, number) for number in range(1, part_count + 1)]
    return {"session_id": session.id, "part_size": part_size, "part_count": part_count, "part_urls": urls}


@router.post("/multipart/complete")
async def complete_multipart(payload: MultipartComplete, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    session = db.query(VideoUploadSession).filter(VideoUploadSession.id == payload.session_id, VideoUploadSession.tenant_id == auth.tenant_id).first()
    if not session: raise HTTPException(404, "上传会话不存在")
    if session.status == "completed":
        asset = None
        if session.video_asset_id is not None:
            asset = db.query(VideoAsset).filter(
                VideoAsset.id == session.video_asset_id, VideoAsset.tenant_id == auth.tenant_id
            ).first()
        if not asset:
            raise HTTPException(409, "上传已完成，但关联素材记录不存在")
        return _serialize(db, asset, user_id=auth.user_id)
    storage = get_storage_by_name(session.storage_provider)
    url, size, etag = await storage.complete_multipart(session.object_key, session.multipart_upload_id)
    if size != session.size_bytes: raise HTTPException(422, "OSS 文件大小与上传文件不一致")
    asset = VideoAsset(tenant_id=auth.tenant_id, project_id=None, product_id=session.product_id,
        asset_id=f"asset-{uuid.uuid4().hex[:12]}", filename=session.filename, source="manual_upload",
        library_type=session.library_type, reusable_status="review_required", object_url=url,
        storage_provider=storage.name, content_hash=f"{storage.name}:{etag}",
        duration_seconds=0, width=0, height=0, fps=0, has_audio=False,
        status="validating", excluded=False)
    db.add(asset); db.flush()
    session.video_asset_id = asset.id
    session.status = "completed"; session.completed_at = datetime.utcnow(); db.commit(); db.refresh(asset)
    _link_asset_to_matching_projects(db, asset)
    db.commit(); enqueue_video_analysis()
    return _serialize(db, asset, user_id=auth.user_id)


@router.get("")
def list_assets(product_id: Optional[int] = None, library_type: Optional[str] = None,
                asset_kind: Optional[str] = None,
                db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    query = db.query(VideoAsset).filter(VideoAsset.tenant_id == auth.tenant_id)
    if product_id is not None: query = query.filter(VideoAsset.product_id == product_id)
    if library_type is not None: query = query.filter(VideoAsset.library_type == library_type)
    if asset_kind is not None: query = query.filter(VideoAsset.asset_kind == asset_kind)
    assets = query.order_by(VideoAsset.created_at.desc()).all()
    if not assets:
        return []
    asset_ids = [asset.id for asset in assets]
    latest_analysis: dict[int, VideoAssetAnalysis] = {}
    for analysis in db.query(VideoAssetAnalysis).filter(VideoAssetAnalysis.asset_id.in_(asset_ids)).order_by(VideoAssetAnalysis.id.desc()).all():
        latest_analysis.setdefault(analysis.asset_id, analysis)
    highlights_by_asset: dict[int, list[VideoHighlight]] = {asset_id: [] for asset_id in asset_ids}
    for highlight in db.query(VideoHighlight).filter(VideoHighlight.asset_id.in_(asset_ids)).order_by(VideoHighlight.asset_id, VideoHighlight.start_seconds).all():
        highlights_by_asset[highlight.asset_id].append(highlight)
    categories_by_asset, tags_by_asset = load_asset_taxonomy(db, assets, auth.tenant_id, auth.user_id)
    counts = reference_counts(db, auth.tenant_id, asset_ids)
    return [_serialize(
        db, asset, latest_analysis.get(asset.id), highlights_by_asset[asset.id],
        categories_by_asset.get(asset.id), tags_by_asset.get(asset.id, []), auth.user_id, counts.get(asset.id, 0),
    ) for asset in assets]


@router.get("/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    asset = db.query(VideoAsset).filter(
        VideoAsset.id == asset_id,
        VideoAsset.tenant_id == auth.tenant_id,
    ).first()
    if not asset:
        raise HTTPException(404, "素材不存在")
    return _serialize(db, asset, user_id=auth.user_id)


@router.post("")
async def upload_assets(product_id: int = Form(...), source: str = Form("manual_upload"),
                        library_type: str = Form("semi_finished"),
                        files: list[UploadFile] = File(...), db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first()
    if not product: raise HTTPException(404, "商品不存在")
    if len(files) > 30: raise HTTPException(422, "单批最多上传 30 条视频")
    result = []
    for file in files:
        raw = await file.read()
        digest = hashlib.sha256(raw).hexdigest()
        if library_type not in {"semi_finished", "finished"}: raise HTTPException(422, "素材库类型无效")
        existing = db.query(VideoAsset).filter(VideoAsset.tenant_id == auth.tenant_id,
            VideoAsset.product_id == product.id, VideoAsset.asset_kind == "material",
            VideoAsset.content_hash == digest, VideoAsset.library_type == library_type).first()
        if existing:
            result.append({**_serialize(db, existing, user_id=auth.user_id), "duplicate": True}); continue
        suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix) as temp:
            temp.write(raw); temp.flush(); media = _probe(Path(temp.name))
        max_duration = get_settings().video_library_max_duration_seconds
        if media["duration"] > max_duration + .05: raise HTTPException(422, f"{file.filename} 超过 {max_duration / 60:g} 分钟限制")
        storage = _tenant_video_storage(db, auth.tenant_id)
        url = await storage.upload(raw, file.filename or "video.mp4", f"video-library/{product.id}/source")
        asset = VideoAsset(tenant_id=auth.tenant_id, project_id=None, product_id=product.id,
            asset_id=f"asset-{uuid.uuid4().hex[:12]}", filename=file.filename or "video.mp4", source=source,
            library_type=library_type, asset_kind="material", reusable_status="review_required",
            object_url=url, storage_provider=storage.name, content_hash=digest,
            duration_seconds=media["duration"], width=media["width"], height=media["height"],
            fps=media["fps"], has_audio=media["has_audio"], status="queued_for_analysis", excluded=False)
        db.add(asset); db.commit(); db.refresh(asset); enqueue_video_analysis()
        _link_asset_to_matching_projects(db, asset)
        db.commit()
        result.append(_serialize(db, asset, user_id=auth.user_id))
    return result


@router.put("/{asset_id}/excluded")
def set_excluded(asset_id: int, payload: ExcludeUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    asset = db.query(VideoAsset).filter(VideoAsset.id == asset_id, VideoAsset.tenant_id == auth.tenant_id).first()
    if not asset: raise HTTPException(404, "素材不存在")
    asset.excluded = payload.excluded; db.commit(); db.refresh(asset); return _serialize(db, asset, user_id=auth.user_id)


@router.put("/{asset_id}/tags")
def update_tags(asset_id: int, payload: TagsUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    asset = db.query(VideoAsset).filter(VideoAsset.id == asset_id, VideoAsset.tenant_id == auth.tenant_id).first()
    if not asset: raise HTTPException(404, "素材不存在")
    clean = lambda values: list(dict.fromkeys(value.strip() for value in values if value.strip()))[:30]
    asset.manual_tags = clean(payload.manual_tags)
    asset.removed_ai_tags = [tag for tag in clean(payload.removed_ai_tags) if tag in (asset.ai_tags or [])]
    db.commit(); db.refresh(asset); return _serialize(db, asset, user_id=auth.user_id)


@router.put("/{asset_id}/reusable-status")
def update_reusable_status(asset_id: int, payload: ReusableStatusUpdate,
                           db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    asset = db.query(VideoAsset).filter(VideoAsset.id == asset_id, VideoAsset.tenant_id == auth.tenant_id).first()
    if not asset:
        raise HTTPException(404, "素材不存在")
    if asset.library_type != "finished":
        raise HTTPException(422, "只有成品素材可以设置复用状态")
    if payload.reusable_status not in REUSABLE_STATUSES:
        raise HTTPException(422, "复用状态无效")
    if asset.generation_depth > 1 and payload.reusable_status != "blocked":
        raise HTTPException(422, "generation_depth 大于 1 的成品默认禁止继续复用")
    _apply_reusable_status(asset, payload.reusable_status)
    if payload.reusable_status == "reusable":
        _link_asset_to_matching_projects(db, asset)
    db.commit(); db.refresh(asset)
    return _serialize(db, asset, user_id=auth.user_id)


@router.post("/{asset_id}/reanalyze")
def reanalyze(asset_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    asset = db.query(VideoAsset).filter(VideoAsset.id == asset_id, VideoAsset.tenant_id == auth.tenant_id).first()
    if not asset: raise HTTPException(404, "素材不存在")
    if asset.status == "analyzing": raise HTTPException(409, "素材正在分析")
    asset.status = "queued_for_analysis"; db.commit(); enqueue_video_analysis()
    return _serialize(db, asset, user_id=auth.user_id)
