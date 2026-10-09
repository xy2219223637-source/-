from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

from app.config import get_settings
from app.database import SessionLocal
from app.models import VideoAsset, VideoRepairInput, VideoRepairJob, VideoRepairPreview, VideoRepairTask
from app.services.video_repair.ai_context import build_ai_context
from app.services.video_repair.candidates import find_candidates
from app.services.video_repair.diagnosis_v4 import V4_ACTION_GUIDANCE, diagnose_video_v4
from app.services.video_repair.insertion_planner import recommend_insertion_positions
from app.services.video_repair.protocol_v4 import ACTIONS
from app.services.video_repair.service import _provider
from app.services.video_repair.state_machine_v4 import transition
from app.services.video_repair.v4_renderer import render_preview


logger = logging.getLogger(__name__)
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_wake = threading.Event()


def wake_video_repair_worker() -> None:
    _wake.set()


def _confidence(value: float) -> str:
    return "HIGH" if value >= .8 else "MEDIUM" if value >= .55 else "LOW"


def _v4_action(issue) -> str | None:
    settings = get_settings()
    action = issue.suggested_action
    if action not in ACTIONS:
        return None
    if action == "AUDIO_REPLACE" and not settings.video_repair_audio_replace_enabled:
        return None
    if action == "CONTENT_INSERT" and not settings.video_repair_content_insert_enabled:
        return None
    return action


def _analyze(db, task: VideoRepairTask) -> None:
    repair_input = db.get(VideoRepairInput, task.input_id)
    source = db.get(VideoAsset, task.source_asset_id)
    if not repair_input or not source:
        raise RuntimeError("SOURCE_MISSING: 修复输入或源成片不存在")
    settings = get_settings()
    capabilities = ["VISUAL_REPLACE"]
    if settings.video_repair_audio_replace_enabled:
        capabilities.append("AUDIO_REPLACE")
    if settings.video_repair_content_insert_enabled:
        capabilities.append("CONTENT_INSERT")
    context = build_ai_context(
        db, repair_input=repair_input, source_asset=source, base_plan={},
        max_keyframes=settings.video_repair_ai_max_keyframes,
        capabilities=capabilities)
    context["action_guidance"] = {
        action: guidance for action, guidance in V4_ACTION_GUIDANCE.items()
        if action in capabilities or action == "UNSUPPORTED"
    }
    diagnosis_model = diagnose_video_v4(
        context, _provider() if settings.video_repair_multimodal_enabled else None,
        prompt_version="video-repair-diagnosis-v4")
    primary = next((row for row in diagnosis_model.issues
                    if row.issue_id == diagnosis_model.primary_issue_id), None)
    recommended = _v4_action(primary) if primary else None
    ranges = [row.model_dump(mode="json") for row in (primary.ranges if primary else [])]
    diagnosis = diagnosis_model.model_dump(mode="json")
    diagnosis.update({
        "schema_version": "video_repair_diagnosis.v4",
        "recommended_action": recommended,
        "confidence_level": _confidence(float(diagnosis_model.overall_confidence)),
        "evidence_sufficiency": "SUFFICIENT" if primary and ranges else "INSUFFICIENT",
        "reported_ranges": context["review"].get("explicit_time_ranges") or [],
        "suggested_ranges": ranges,
    })
    modalities = set(primary.affected_modalities or []) if primary else set()
    unsupported_av = bool(primary and not recommended and "visual" in modalities
                          and ({"audio", "caption"} & modalities))
    unsupported_caption = bool(primary and not recommended and "caption" in modalities
                               and not unsupported_av)
    proposal: dict = {
        "action": None,
        "summary": ("AI 判断该问题同时涉及画面与声音或字幕；当前三种返修动作无法完整修复。"
                    if unsupported_av else
                    "AI 判断该问题涉及字幕；当前音频替换不会修改字幕，无法完整修复。"
                    if unsupported_caption else
                    "AI 已定位问题，但当前三种返修动作无法完整修复，请人工判断。"),
        "requires_input": [],
        "field_sources": {
            "action": "AI_ANALYSIS" if recommended else "HUMAN_REQUIRED",
            "target_range": "AI_ANALYSIS" if ranges else "HUMAN_REQUIRED",
            "material": "HUMAN_REQUIRED",
        },
    }
    candidates: list[dict] = []
    if recommended == "VISUAL_REPLACE" and ranges:
        first_range = ranges[0]
        start_ms = int(first_range.get("start_ms", 0))
        end_ms = int(first_range.get("end_ms", start_ms))
        target_duration = max(.1, (end_ms - start_ms) / 1000)
        candidates = find_candidates(
            db, tenant_id=task.tenant_id, source_asset=source,
            action="VISUAL_REPLACE", target_duration=target_duration,
            issue_description=primary.description if primary else "",
            recommend_score=settings.video_repair_recommend_score,
            selectable_score=settings.video_repair_selectable_score,
        )
        selectable = next((row for row in candidates
                           if row.get("recommendation_level") != "BELOW_THRESHOLD"), None)
        if selectable:
            source_start = float(selectable["source_range"]["start"])
            source_end = min(float(selectable["source_range"]["end"]), source_start + target_duration)
            proposal["action"] = {
                "type": "VISUAL_REPLACE",
                "target_range": {"start_ms": start_ms, "end_ms": end_ms},
                "material": {
                    "ref_type": "VIDEO_ASSET",
                    "ref_id": selectable["source_asset_id"],
                    "source_range": {
                        "start_ms": round(source_start * 1000),
                        "end_ms": round(source_end * 1000),
                    },
                    "content_hash": selectable["content_hash"],
                    "candidate_id": selectable["id"],
                },
                "fit_mode": "COVER",
                "transition": {"type": "CUT", "duration_ms": 0},
            }
            proposal.update({
                "summary": f"建议将 {start_ms / 1000:g}–{end_ms / 1000:g} 秒画面替换为“{selectable.get('description') or '同商品素材'}”。",
                "requires_input": [],
                "field_sources": {"action": "AI_ANALYSIS", "target_range": "AI_ANALYSIS", "material": "AI_CANDIDATE_RANKING"},
                "risks": selectable.get("warnings", []),
            })
        else:
            proposal["requires_input"] = ["visual_material"]
            proposal["summary"] = "AI 已确定画面替换区间，但没有找到达到可用门槛的同商品素材。"
    elif recommended == "AUDIO_REPLACE" and ranges:
        first_range = ranges[0]
        proposal.update({
            "summary": "AI 建议替换问题区间的音频；请上传或生成确认后的口播。",
            "requires_input": ["audio"],
            "action_template": {
                "type": "AUDIO_REPLACE",
                "target_range": {"start_ms": int(first_range.get("start_ms", 0)), "end_ms": int(first_range.get("end_ms", 0))},
            },
        })
    elif recommended == "CONTENT_INSERT":
        first_range = ranges[0] if ranges else None
        placement = recommend_insertion_positions(
            context, duration_ms=round(float(source.duration_seconds or 0) * 1000))
        start_ms = int(first_range.get("start_ms", 0)) if first_range else int(placement["recommended_ms"])
        end_ms = int(first_range.get("end_ms", start_ms + 3000)) if first_range else None
        target_duration = max(.5, (end_ms - start_ms) / 1000) if first_range else 3.0
        candidates = find_candidates(
            db, tenant_id=task.tenant_id, source_asset=source,
            action="PROCESS_INSERT", target_duration=target_duration,
            issue_description=primary.description if primary else "",
            recommend_score=settings.video_repair_recommend_score,
            selectable_score=settings.video_repair_selectable_score,
        )
        selectable = next((row for row in candidates
                           if row.get("recommendation_level") != "BELOW_THRESHOLD"), None)
        if selectable:
            source_start = float(selectable["source_range"]["start"])
            duration = min(target_duration, float(selectable["duration_seconds"]))
            source_end = source_start + duration
            template = {
                    "type": "CONTENT_INSERT",
                    "duration_ms": round(duration * 1000),
                    "visual": {
                        "ref_type": "VIDEO_ASSET",
                        "ref_id": selectable["source_asset_id"],
                        "source_range": {
                            "start_ms": round(source_start * 1000),
                            "end_ms": round(source_end * 1000),
                        },
                        "content_hash": selectable["content_hash"],
                        "candidate_id": selectable["id"],
                    },
                    "fit_mode": "COVER",
                    "transition": {"type": "CUT", "duration_ms": 0},
                }
            template["insert_at_ms"] = start_ms
            if selectable.get("has_audio"):
                template["audio"] = {
                    "source_type": "VIDEO_ASSET", "ref_type": "VIDEO_ASSET",
                    "ref_id": selectable["source_asset_id"],
                    "source_range": dict(template["visual"]["source_range"]),
                    "content_hash": selectable["content_hash"],
                }
            missing = [] if template.get("audio") else ["audio"]
            suffix = "候选素材原声将与画面一起插入。" if template.get("audio") else "仍需补充确认后的口播音频。"
            placement_seconds = start_ms / 1000
            proposal.update({
                "summary": f"已从同商品素材库找到“{selectable.get('description') or '实际使用过程'}”，建议在 {placement_seconds:g} 秒插入；{suffix}",
                "requires_input": missing,
                "action_template": template,
                "action": template if not missing else None,
                "placement": placement,
                "field_sources": {
                    "action": "AI_ANALYSIS", "insert_at": "AI_TIMELINE_ANALYSIS",
                    "material": "AI_CANDIDATE_RANKING",
                    "audio": "CANDIDATE_ORIGINAL_AUDIO" if template.get("audio") else "HUMAN_REQUIRED",
                },
                "risks": [*selectable.get("warnings", []), *(
                    ["插入位置缺少字幕/镜头证据，当前为低置信度可撤销预览"]
                    if placement["confidence_level"] == "LOW" else [])],
            })
        else:
            proposal.update({
                "summary": "AI 已确定需要补充实际使用过程，但同商品素材库中没有找到达到可用门槛的画面。",
                "requires_input": ["visual_material", "audio"],
                "action_template": ({
                    "type": "CONTENT_INSERT", "duration_ms": round(target_duration * 1000),
                } | ({"insert_at_ms": start_ms} if start_ms is not None else {})),
            })
    diagnosis["ai_proposal"] = proposal
    diagnosis["material_candidates"] = candidates
    task.diagnosis_json = diagnosis
    task.error_code = task.error_message = ""
    transition(task, "CONFIGURING" if recommended and ranges else "AWAITING_MANUAL_DECISION")


def _execute(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(VideoRepairJob, job_id)
        if not job:
            return
        claimed = db.query(VideoRepairJob).filter_by(id=job_id, status="QUEUED").update({
            VideoRepairJob.status: "RUNNING",
            VideoRepairJob.attempts: VideoRepairJob.attempts + 1,
            VideoRepairJob.started_at: datetime.utcnow(),
        }, synchronize_session=False)
        if not claimed:
            db.rollback()
            return
        db.commit()
        db.refresh(job)
        task = db.get(VideoRepairTask, job.task_id)
        if not task or task.status in {"COMPLETED", "CANCELED"}:
            job.status = "CANCELED"
            job.finished_at = datetime.utcnow()
            db.commit()
            return
        try:
            if job.job_type == "ANALYZE":
                _analyze(db, task)
            elif job.job_type == "RENDER_PREVIEW":
                preview = db.get(VideoRepairPreview, int(job.payload_json["preview_id"]))
                if not preview or preview.status == "SUPERSEDED":
                    job.status = "CANCELED"
                    job.finished_at = datetime.utcnow()
                    db.commit()
                    return
                transition(task, "PREVIEW_RENDERING")
                preview.status = "RENDERING"
                db.commit()
                render_preview(db, task, preview)
                transition(task, "PREVIEW_READY")
                task.error_code = task.error_message = ""
            else:
                raise RuntimeError(f"UNSUPPORTED_JOB: {job.job_type}")
            job.status = "SUCCEEDED"
            job.error_code = job.error_message = ""
            job.finished_at = datetime.utcnow()
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(VideoRepairJob, job_id)
            task = db.get(VideoRepairTask, job.task_id) if job else None
            message = str(exc)[:2000]
            if job:
                retry = job.attempts < job.max_attempts
                job.status = "QUEUED" if retry else "FAILED"
                job.error_code = "JOB_FAILED"
                job.error_message = message
                job.finished_at = None if retry else datetime.utcnow()
            if task and not retry:
                if job.job_type == "ANALYZE":
                    task.status = "AWAITING_MANUAL_DECISION"
                else:
                    task.status = "FAILED"
                task.error_code = "ANALYSIS_FAILED" if job.job_type == "ANALYZE" else "RENDER_FAILED"
                task.error_message = message
            preview_id = (job.payload_json or {}).get("preview_id") if job else None
            preview = db.get(VideoRepairPreview, int(preview_id)) if preview_id else None
            if preview and preview.status != "SUPERSEDED" and not retry:
                preview.status = "FAILED"
                preview.error_code = "RENDER_FAILED"
                preview.error_message = message
                preview.completed_at = datetime.utcnow()
            db.commit()
            logger.exception("V4 video repair job %s failed", job_id)
            if retry:
                wake_video_repair_worker()
    finally:
        db.close()


def _next_job_id() -> int | None:
    db = SessionLocal()
    try:
        row = db.query(VideoRepairJob).filter_by(status="QUEUED").order_by(
            VideoRepairJob.created_at, VideoRepairJob.id).first()
        return row.id if row else None
    finally:
        db.close()


def _loop() -> None:
    while not _stop.is_set():
        try:
            job_id = _next_job_id()
            if job_id is None:
                _wake.wait(1)
                _wake.clear()
                continue
            _execute(job_id)
        except Exception:
            logger.exception("V4 video repair worker iteration failed")
            _wake.wait(1)
            _wake.clear()


def recover_interrupted_jobs() -> int:
    db = SessionLocal()
    try:
        count = db.query(VideoRepairJob).filter_by(status="RUNNING").update({
            VideoRepairJob.status: "QUEUED",
            VideoRepairJob.error_code: "WORKER_RESTARTED",
            VideoRepairJob.error_message: "服务重启，任务已重新排队",
            VideoRepairJob.started_at: None,
        }, synchronize_session=False)
        db.query(VideoRepairPreview).filter_by(status="RENDERING").update({
            VideoRepairPreview.status: "QUEUED"}, synchronize_session=False)
        db.query(VideoRepairTask).filter_by(status="PREVIEW_RENDERING").update({
            VideoRepairTask.status: "PREVIEW_QUEUED"}, synchronize_session=False)
        db.commit()
        return int(count or 0)
    finally:
        db.close()


def start_video_repair_worker() -> None:
    global _thread
    settings = get_settings()
    if not settings.video_repair_v4_enabled or not settings.video_repair_async_worker_enabled:
        return
    recover_interrupted_jobs()
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="video-repair-v4-worker", daemon=True)
    _thread.start()
    wake_video_repair_worker()


def stop_video_repair_worker() -> None:
    _stop.set()
    wake_video_repair_worker()
