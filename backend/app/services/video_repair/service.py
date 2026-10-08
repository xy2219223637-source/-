from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    EditingPlanVersion, VideoAsset, VideoLineageSegment, VideoRepairInput, VideoRepairJob,
    VideoRepairPreview, VideoRepairTask,
)
from app.services.video_repair.candidates import (
    candidate_snapshot, find_candidates, forbidden_lineage_ranges, resolve_candidate,
    validate_candidate_for_action,
)
from app.services.video_repair.context_window import build_repair_context
from app.config import get_settings
from app.services.video_repair.ai_context import build_ai_context
from app.services.video_repair.ai_provider import OpenAICompatibleDiagnosisProvider
from app.services.video_repair.capabilities import (
    CapabilityValidationError, validate_plan_against_capabilities,
)
from app.services.video_repair.diagnosis_v2 import diagnose_video
from app.services.video_repair.planner import (
    build_plan_action, build_repair_plan, select_action_for_issue,
)


ACTIVE_STATUSES = (
    "ANALYZING", "NEEDS_CONFIRMATION", "APPROVED", "RENDERING",
    "AWAITING_MANUAL_DECISION", "AWAITING_MATERIAL", "CONFIGURING",
    "PREVIEW_QUEUED", "PREVIEW_RENDERING", "PREVIEW_READY",
)


def _provider():
    settings = get_settings()
    if not settings.video_repair_ai_enabled:
        return None
    try:
        return OpenAICompatibleDiagnosisProvider(
            api_key=settings.llm_api_key, base_url=settings.llm_api_base,
            model=settings.video_repair_ai_model or settings.llm_vision_model,
            timeout_seconds=settings.video_repair_ai_timeout_seconds,
            prompt_version=settings.video_repair_ai_prompt_version,
        )
    except ValueError:
        return None


def _expected_timestamp(actual: datetime, expected_updated_at: datetime) -> None:
    expected = expected_updated_at.replace(tzinfo=None) if expected_updated_at.tzinfo else expected_updated_at
    if actual != expected:
        raise HTTPException(409, detail={"code": "STALE_REPAIR_TASK",
                                        "message": "方案已变化，请刷新后重新确认"})


def _target_duration_seconds(target: dict[str, Any]) -> float:
    if "start_ms" in target:
        return max(0.0, (float(target["end_ms"]) - float(target["start_ms"])) / 1000)
    return max(0.0, float(target["end"]) - float(target["start"]))


def _primary_issue(diagnosis: dict[str, Any]) -> dict[str, Any] | None:
    primary_id = diagnosis.get("primary_issue_id")
    return next((row for row in diagnosis.get("issues") or []
                 if row.get("issue_id") == primary_id), None)


def serialize_task(db: Session, row: VideoRepairTask) -> dict[str, Any]:
    source = db.get(VideoAsset, row.source_asset_id)
    output = db.get(VideoAsset, row.output_asset_id) if row.output_asset_id else None
    repair_input = db.get(VideoRepairInput, row.input_id)
    if not repair_input:
        raise HTTPException(500, detail={
            "code": "REPAIR_INPUT_MISSING", "message": "修复任务缺少输入快照",
            "field": None, "retryable": False,
        })
    asset_json = lambda asset: ({
        "id": asset.id, "asset_id": asset.asset_id, "filename": asset.filename,
        "object_url": asset.object_url, "duration_seconds": asset.duration_seconds,
        "status": asset.status,
    } if asset else None)
    previews = db.query(VideoRepairPreview).filter_by(task_id=row.id, tenant_id=row.tenant_id).order_by(
        VideoRepairPreview.created_at.desc(), VideoRepairPreview.id.desc()).all()
    settings = get_settings()
    capabilities = ["VISUAL_REPLACE"]
    if settings.video_repair_audio_replace_enabled:
        capabilities.append("AUDIO_REPLACE")
    if settings.video_repair_content_insert_enabled:
        capabilities.append("CONTENT_INSERT")
    return {
        "id": row.id, "status": row.status, "input_id": row.input_id,
        "schema_version": row.schema_version,
        "product_id": getattr(row, "product_id", None),
        "material_library_product_id": (repair_input.raw_payload_json or {}).get("material_library_product_id"),
        "draft_revision": getattr(row, "draft_revision", 1),
        "accepted_preview_id": getattr(row, "accepted_preview_id", None),
        "cancel_reason": getattr(row, "cancel_reason", "") or "",
        "capabilities": capabilities if getattr(row, "schema_version", "") == "video_repair_task.v2" else [],
        "tts_available": bool(settings.doubao_tts_api_key),
        "input": {
            "id": repair_input.id, "source_kind": repair_input.source_kind,
            "audit_platform": repair_input.audit_platform, "raw_reason": repair_input.raw_reason,
            "suggestions": repair_input.suggestions_json or [],
            "time_ranges": repair_input.time_ranges_json or [],
            "occurred_at": repair_input.occurred_at, "created_at": repair_input.created_at,
        },
        "review_feedback_id": row.review_feedback_id,
        "source_asset_id": row.source_asset_id,
        "base_plan_version_id": row.base_plan_version_id,
        "diagnosis": row.diagnosis_json or {}, "repair_plan": row.repair_plan_json or {},
        "selected_candidates": row.selected_candidates_json or {},
        "output_asset_id": row.output_asset_id,
        "output_plan_version_id": row.output_plan_version_id,
        "source_asset": asset_json(source), "output_asset": asset_json(output),
        "error_code": row.error_code, "error_message": row.error_message,
        "approved_at": row.approved_at, "started_at": row.started_at,
        "completed_at": row.completed_at, "created_at": row.created_at,
        "updated_at": row.updated_at,
        "previews": [{
            "id": item.id, "draft_revision": item.draft_revision,
            "plan_hash": item.plan_hash, "status": item.status,
            "object_url": item.object_url, "content_hash": item.content_hash,
            "duration_seconds": item.duration_seconds, "width": item.width,
            "height": item.height, "error_code": item.error_code,
            "error_message": item.error_message, "created_at": item.created_at,
            "completed_at": item.completed_at,
        } for item in previews],
    }


def owned_task(db: Session, tenant_id: str, user_id: str, task_id: int) -> VideoRepairTask:
    row = db.query(VideoRepairTask).filter_by(
        id=task_id, tenant_id=tenant_id, created_by=user_id).first()
    if not row:
        raise HTTPException(404, "修复任务不存在")
    return row


def _base_plan(db: Session, tenant_id: str, source_asset_id: int) -> tuple[EditingPlanVersion, dict]:
    lineage = db.query(VideoLineageSegment).filter_by(
        tenant_id=tenant_id, target_asset_id=source_asset_id).order_by(
            VideoLineageSegment.segment_index).all()
    plan_ids = {row.plan_version_id for row in lineage}
    variant_ids = {row.variant_id for row in lineage}
    if len(plan_ids) != 1 or len(variant_ids) != 1:
        raise HTTPException(409, detail={"code": "UNSUPPORTED_SOURCE_ASSET",
                                        "message": "成片缺少唯一有效的剪辑方案或来源链路"})
    record = db.query(EditingPlanVersion).filter_by(
        id=next(iter(plan_ids)), tenant_id=tenant_id, validation_status="valid").first()
    if not record:
        raise HTTPException(409, detail={"code": "UNSUPPORTED_SOURCE_ASSET",
                                        "message": "成片的基线剪辑方案无效或不可访问"})
    selected = [variant for variant in (record.plan_json.get("variants") or [])
                if str(variant.get("variant_id")) == next(iter(variant_ids))]
    if len(selected) != 1:
        raise HTTPException(409, detail={"code": "UNSUPPORTED_SOURCE_ASSET",
                                        "message": "来源链路无法映射到唯一方案版本"})
    from app.services.video_editing.plan_normalization import ensure_video_track
    plan = deepcopy(record.plan_json)
    plan["variants"] = [ensure_video_track(selected[0])]
    return record, plan


def _optional_base_plan(
    db: Session, tenant_id: str, source_asset_id: int,
) -> tuple[EditingPlanVersion | None, dict]:
    """Return an editable plan when lineage exists, otherwise use flat-video mode."""
    try:
        return _base_plan(db, tenant_id, source_asset_id)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") != "UNSUPPORTED_SOURCE_ASSET":
            raise
        return None, {}


def create_task(db: Session, *, tenant_id: str, user_id: str,
                source_asset_id: int, input_id: int,
                review_feedback_id: int | None = None) -> VideoRepairTask:
    existing = db.query(VideoRepairTask).filter(
        VideoRepairTask.tenant_id == tenant_id,
        VideoRepairTask.created_by == user_id,
        VideoRepairTask.input_id == input_id,
        VideoRepairTask.source_asset_id == source_asset_id,
        VideoRepairTask.status.in_(ACTIVE_STATUSES),
    ).first()
    if existing:
        return existing
    repair_input = db.query(VideoRepairInput).filter_by(
        id=input_id, tenant_id=tenant_id, created_by=user_id,
        source_asset_id=source_asset_id).first()
    source = db.query(VideoAsset).filter_by(
        id=source_asset_id, tenant_id=tenant_id, asset_kind="final_output").first()
    if not repair_input or not source:
        raise HTTPException(404, detail={
            "code": "SOURCE_ASSET_NOT_FOUND", "message": "修复输入或源成片不存在",
            "field": None, "retryable": False,
        })
    record, plan = _optional_base_plan(db, tenant_id, source.id)
    task = VideoRepairTask(
        tenant_id=tenant_id, created_by=user_id, input_id=repair_input.id,
        review_feedback_id=review_feedback_id,
        source_asset_id=source.id, product_id=source.product_id,
        base_plan_version_id=record.id if record else None,
        status="ANALYZING")
    db.add(task); db.flush()
    settings = get_settings()
    context = build_ai_context(
        db, repair_input=repair_input, source_asset=source, base_plan=plan,
        max_keyframes=settings.video_repair_ai_max_keyframes)
    diagnosis_model = diagnose_video(
        context, _provider(), prompt_version=settings.video_repair_ai_prompt_version)
    diagnosis = diagnosis_model.model_dump(mode="json")
    primary = next((row for row in diagnosis_model.issues
                    if row.issue_id == diagnosis_model.primary_issue_id), None)
    target_ranges = primary.ranges if primary else []
    target_duration = ((target_ranges[0].end_ms - target_ranges[0].start_ms) / 1000
                       if target_ranges else 0.0)
    action = select_action_for_issue(primary) if primary else "UNSUPPORTED"
    requested_range = ({"start_ms": target_ranges[0].start_ms,
                        "end_ms": target_ranges[0].end_ms} if target_ranges else None)
    repair_context = build_repair_context(
        context, requested_range=requested_range,
        target_intent=primary.description if primary else "",
        pre_ms=settings.video_repair_context_pre_ms,
        post_ms=settings.video_repair_context_post_ms,
        max_ms=settings.video_repair_context_max_ms,
    ) if requested_range else None
    forbidden = forbidden_lineage_ranges(
        db, tenant_id=tenant_id, target_asset_id=source.id,
        issue_ranges=[row.model_dump() for row in target_ranges])
    candidates = [] if action == "UNSUPPORTED" or not target_ranges else find_candidates(
        db, tenant_id=tenant_id, source_asset=source, action=action,
        target_duration=target_duration, forbidden_source_ranges=forbidden,
        issue_description=primary.description if primary else "", repair_context=repair_context,
        recommend_score=settings.video_repair_recommend_score,
        selectable_score=settings.video_repair_selectable_score)
    status, repair_plan = build_repair_plan(
        diagnosis_model, plan, candidates, repair_context=repair_context)
    repair_plan["render_mode"] = "PLAN" if record else "FLAT_VISUAL"
    if not record:
        repair_plan.setdefault("warnings", []).append(
            "该成片没有原剪辑方案，将直接替换目标画面并保留原音频")
    task.status = status
    task.diagnosis_json = diagnosis
    task.repair_plan_json = repair_plan
    task.selected_candidates_json = {
        row["action_id"]: row["selected_candidate_id"]
        for row in repair_plan.get("actions") or [] if row.get("selected_candidate_id")
    }
    db.commit(); db.refresh(task)
    return task


def create_v4_task(db: Session, *, tenant_id: str, user_id: str,
                   source_asset_id: int, input_id: int,
                   review_feedback_id: int | None = None) -> VideoRepairTask:
    """Create the durable V4 draft and analysis job without running AI in the request."""
    existing = db.query(VideoRepairTask).filter(
        VideoRepairTask.tenant_id == tenant_id,
        VideoRepairTask.created_by == user_id,
        VideoRepairTask.input_id == input_id,
        VideoRepairTask.source_asset_id == source_asset_id,
        VideoRepairTask.status.in_(ACTIVE_STATUSES),
    ).first()
    if existing:
        return existing
    repair_input = db.query(VideoRepairInput).filter_by(
        id=input_id, tenant_id=tenant_id, created_by=user_id,
        source_asset_id=source_asset_id,
    ).first()
    source = db.query(VideoAsset).filter_by(
        id=source_asset_id, tenant_id=tenant_id, asset_kind="final_output",
    ).first()
    if not repair_input or not source:
        raise HTTPException(404, detail={
            "code": "SOURCE_ASSET_NOT_FOUND", "message": "修复输入或源成片不存在",
            "field": None, "retryable": False,
        })
    task = VideoRepairTask(
        tenant_id=tenant_id,
        created_by=user_id,
        input_id=repair_input.id,
        review_feedback_id=review_feedback_id,
        source_asset_id=source.id,
        product_id=source.product_id,
        base_plan_version_id=None,
        schema_version="video_repair_task.v2",
        draft_revision=1,
        status="ANALYZING",
    )
    db.add(task)
    db.flush()
    db.add(VideoRepairJob(
        tenant_id=tenant_id,
        task_id=task.id,
        job_type="ANALYZE",
        status="QUEUED",
        idempotency_key=f"analyze:{task.id}:input:{repair_input.id}",
        payload_json={"input_id": repair_input.id, "source_asset_id": source.id},
        max_attempts=2,
    ))
    db.commit()
    db.refresh(task)
    from app.services.video_repair.worker_v4 import wake_video_repair_worker
    wake_video_repair_worker()
    return task


def select_candidate(db: Session, task: VideoRepairTask, action_id: str,
                     candidate_id: str, override_reason: str | None = None) -> VideoRepairTask:
    if task.status != "NEEDS_CONFIRMATION":
        raise HTTPException(409, "只有待确认方案可以更换候选")
    plan = deepcopy(task.repair_plan_json)
    action = next((row for row in plan.get("actions") or [] if row.get("action_id") == action_id), None)
    if not action or candidate_id not in (action.get("candidate_ids") or []):
        raise HTTPException(422, "候选不属于当前任务或已失效")
    listed_candidate = next(
        (row for row in action.get("candidates") or [] if row.get("id") == candidate_id), None)
    if (listed_candidate and listed_candidate.get("recommendation_level") == "BELOW_THRESHOLD"
            and not (override_reason or "").strip()):
        raise HTTPException(422, detail={
            "code": "REPAIR_CANDIDATE_SCORE_TOO_LOW",
            "message": "候选低于可选阈值，强制选择时必须填写原因",
        })
    source = db.query(VideoAsset).filter_by(
        id=task.source_asset_id, tenant_id=task.tenant_id, asset_kind="final_output").first()
    forbidden = forbidden_lineage_ranges(
        db, tenant_id=task.tenant_id, target_asset_id=task.source_asset_id,
        issue_ranges=[action["target_range"]])
    candidate = resolve_candidate(
        db, tenant_id=task.tenant_id, source_asset=source,
        candidate_value=candidate_id, forbidden_source_ranges=forbidden) if source else None
    if not candidate:
        raise HTTPException(422, "候选不属于当前任务或已失效")
    try:
        validate_candidate_for_action(
            candidate, action["type"], _target_duration_seconds(action["target_range"]))
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc), "message": "候选不满足动作约束"}) from exc
    action["selected_candidate_id"] = candidate_id
    action["candidate_score"] = ({key: listed_candidate.get(key) for key in (
        "score", "recommendation_level", "score_breakdown", "reasons", "warnings",
    )} if listed_candidate else None)
    action["selection_override_reason"] = ((override_reason or "").strip() or None)
    action["candidate_source_range"] = {
        "start_ms": round(candidate["source_range"]["start"] * 1000),
        "end_ms": round(candidate["source_range"]["end"] * 1000),
    }
    if action["type"] == "PROCESS_INSERT":
        action.setdefault("predicted_effects", {})["total_duration_delta_ms"] = round(
            candidate["duration_seconds"] * 1000)
    selected = dict(task.selected_candidates_json or {})
    selected[action_id] = candidate_id
    task.repair_plan_json = plan
    task.selected_candidates_json = selected
    db.commit(); db.refresh(task)
    return task


def update_draft_plan(db: Session, task: VideoRepairTask, expected_updated_at: datetime,
                      action_type: str, target_range: dict[str, int],
                      selected_candidate_id: str | None = None) -> VideoRepairTask:
    if task.status not in {"NEEDS_CONFIRMATION", "NEED_MORE_MATERIAL"}:
        raise HTTPException(409, "只有待确认或缺素材方案可以修改")
    _expected_timestamp(task.updated_at, expected_updated_at)
    source = db.query(VideoAsset).filter_by(
        id=task.source_asset_id, tenant_id=task.tenant_id, asset_kind="final_output").first()
    if not source:
        raise HTTPException(409, detail={"code": "SOURCE_ASSET_NOT_FOUND", "message": "源成片已不可用"})
    if task.base_plan_version_id is None and action_type != "VISUAL_REPLACE":
        raise HTTPException(422, detail={
            "code": "FLAT_SOURCE_VISUAL_ONLY",
            "message": "无原剪辑方案的成片目前只支持局部画面替换",
        })
    duration_ms = round(float(source.duration_seconds or 0) * 1000)
    if target_range["end_ms"] > duration_ms:
        raise HTTPException(422, detail={"code": "INVALID_TARGET_RANGE", "message": "时间段超出视频时长"})
    primary = _primary_issue(task.diagnosis_json or {})
    modalities = set((primary or {}).get("affected_modalities") or [])
    if action_type == "VISUAL_REPLACE" and ({"audio", "caption", "full_video"} & modalities):
        raise HTTPException(422, detail={"code": "ACTION_MODALITY_CONFLICT",
                                         "message": "该问题涉及音频、字幕或全片，不能只替换画面"})
    if action_type == "PROCESS_INSERT" and (primary or {}).get("category") != "MISSING_PROCESS_EVIDENCE":
        raise HTTPException(422, detail={"code": "ACTION_MODALITY_CONFLICT",
                                         "message": "插入动作只适用于缺少过程或证据"})
    forbidden = forbidden_lineage_ranges(
        db, tenant_id=task.tenant_id, target_asset_id=source.id, issue_ranges=[target_range])
    settings = get_settings()
    repair_input = db.get(VideoRepairInput, task.input_id)
    if repair_input:
        _, base_plan = _optional_base_plan(db, task.tenant_id, source.id)
        raw_context = build_ai_context(
            db, repair_input=repair_input, source_asset=source, base_plan=base_plan,
            max_keyframes=settings.video_repair_ai_max_keyframes)
        repair_context = build_repair_context(
            raw_context, requested_range=target_range,
            target_intent=(primary or {}).get("description", ""),
            pre_ms=settings.video_repair_context_pre_ms,
            post_ms=settings.video_repair_context_post_ms,
            max_ms=settings.video_repair_context_max_ms)
        candidates = find_candidates(
            db, tenant_id=task.tenant_id, source_asset=source, action=action_type,
            target_duration=_target_duration_seconds(target_range), forbidden_source_ranges=forbidden,
            issue_description=(primary or {}).get("description", ""),
            repair_context=repair_context,
            recommend_score=settings.video_repair_recommend_score,
            selectable_score=settings.video_repair_selectable_score)
    else:
        repair_context = None
        candidates = find_candidates(
            db, tenant_id=task.tenant_id, source_asset=source, action=action_type,
            target_duration=_target_duration_seconds(target_range),
            forbidden_source_ranges=forbidden,
            issue_description=(primary or {}).get("description", ""),
            recommend_score=settings.video_repair_recommend_score,
            selectable_score=settings.video_repair_selectable_score)
    if selected_candidate_id and selected_candidate_id not in {row["id"] for row in candidates}:
        raise HTTPException(422, detail={"code": "CANDIDATE_NOT_ALLOWED",
                                         "message": "所选候选不满足修改后的动作或时间范围"})
    plan = deepcopy(task.repair_plan_json or {})
    plan.update({
        "schema_version": "video_repair_plan.v3",
        "primary_action": action_type,
        "actions": [build_plan_action(
            action_type, target_range, candidates, selected_candidate_id=selected_candidate_id)],
        "warnings": ([] if candidates else ["当前商品素材库没有满足动作硬约束的候选片段"])
                    + (["该成片没有原剪辑方案，将直接替换目标画面并保留原音频"]
                       if task.base_plan_version_id is None else []),
        "repair_context": repair_context,
        "render_mode": "FLAT_VISUAL" if task.base_plan_version_id is None else "PLAN",
    })
    task.repair_plan_json = plan
    selected = plan["actions"][0].get("selected_candidate_id")
    task.selected_candidates_json = {"action-1": selected} if selected else {}
    task.status = "NEEDS_CONFIRMATION" if candidates else "NEED_MORE_MATERIAL"
    db.commit(); db.refresh(task)
    return task


def approve_task(db: Session, task: VideoRepairTask, expected_updated_at: datetime) -> VideoRepairTask:
    if task.status != "NEEDS_CONFIRMATION":
        raise HTTPException(409, "只有待确认方案可以批准")
    _expected_timestamp(task.updated_at, expected_updated_at)
    if (task.repair_plan_json or {}).get("schema_version") == "video_repair_plan.v1":
        task.approved_plan_json = deepcopy(task.repair_plan_json)
        task.status = "APPROVED"
        task.approved_at = datetime.utcnow()
        db.commit(); db.refresh(task)
        return task
    source = db.query(VideoAsset).filter_by(
        id=task.source_asset_id, tenant_id=task.tenant_id, asset_kind="final_output").first()
    if not source:
        raise HTTPException(409, detail={"code": "SOURCE_ASSET_NOT_FOUND", "message": "源成片已不可用"})
    approved = deepcopy(task.repair_plan_json)
    primary = _primary_issue(task.diagnosis_json or {})
    for action in approved.get("actions") or []:
        if task.base_plan_version_id is None and action["type"] != "VISUAL_REPLACE":
            raise HTTPException(422, detail={
                "code": "FLAT_SOURCE_VISUAL_ONLY",
                "message": "无原剪辑方案的成片目前只支持局部画面替换",
            })
        if action["type"] == "VISUAL_REPLACE" and ({"audio", "caption", "full_video"} & set(
                (primary or {}).get("affected_modalities") or [])):
            raise HTTPException(422, detail={"code": "ACTION_MODALITY_CONFLICT",
                                             "message": "动作与诊断影响轨道冲突"})
        selected_id = action.get("selected_candidate_id")
        listed_candidate = next(
            (row for row in action.get("candidates") or [] if row.get("id") == selected_id), None)
        if (listed_candidate and listed_candidate.get("recommendation_level") == "BELOW_THRESHOLD"
                and not (action.get("selection_override_reason") or "").strip()):
            raise HTTPException(422, detail={
                "code": "REPAIR_CANDIDATE_SCORE_TOO_LOW",
                "message": "低分候选缺少强制选择原因",
            })
        forbidden = forbidden_lineage_ranges(
            db, tenant_id=task.tenant_id, target_asset_id=source.id,
            issue_ranges=[action["target_range"]])
        candidate = resolve_candidate(
            db, tenant_id=task.tenant_id, source_asset=source,
            candidate_value=selected_id or "", forbidden_source_ranges=forbidden)
        if not candidate or selected_id not in (action.get("candidate_ids") or []):
            raise HTTPException(422, detail={"code": "CANDIDATE_NOT_ALLOWED",
                                             "message": "候选已失效或无权访问"})
        try:
            validate_candidate_for_action(
                candidate, action["type"], _target_duration_seconds(action["target_range"]))
        except ValueError as exc:
            raise HTTPException(422, detail={"code": str(exc), "message": "候选不满足动作约束"}) from exc
        action["selected_candidate_snapshot"] = candidate_snapshot(candidate)
    try:
        validate_plan_against_capabilities(approved, source, None)
    except CapabilityValidationError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    task.approved_plan_json = approved
    task.status = "APPROVED"
    task.approved_at = datetime.utcnow()
    db.commit(); db.refresh(task)
    return task
