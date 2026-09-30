from __future__ import annotations

import time
from typing import Any

from app.services.video_repair.ai_provider import (
    DiagnosisProviderResult, MultimodalDiagnosisProvider,
)
from app.services.video_repair.capabilities import validate_diagnosis_against_capabilities
from app.services.video_repair.diagnosis import (
    COPY_PATTERNS, NON_VIDEO_PATTERNS, PROCESS_PATTERNS, VISUAL_PATTERNS, _contains,
)
from app.services.video_repair.diagnosis_schema import Evidence, IssueRange, VideoRepairDiagnosisV2


def _issue_range_evidence(ranges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "source": "USER_INPUT" if row["source"] == "INPUT" else "REVIEW_TEXT",
        "start_ms": row["start_ms"],
        "end_ms": row["end_ms"],
        "description": "用户明确指定问题时间段" if row["source"] == "INPUT" else "审核文本包含时间表达",
    } for row in ranges]


def rule_fallback(context: dict[str, Any], error: str | None = None) -> VideoRepairDiagnosisV2:
    review = context["review"]
    reason = str(review.get("raw_reason") or "").strip()
    ranges = list(review.get("explicit_time_ranges") or [])
    hint = str(review.get("category_hint") or "UNKNOWN")
    category = "UNKNOWN"
    modalities: list[str] = ["full_video"]
    action = "UNSUPPORTED"
    confidence = .3
    if _contains(reason, NON_VIDEO_PATTERNS):
        category, modalities, action, confidence = "NON_VIDEO_COMPLIANCE", ["full_video"], "UNSUPPORTED", .92
        ranges = []
    elif _contains(reason, COPY_PATTERNS) or hint == "COPY":
        category, modalities, action, confidence = "COPY_AUDIO_CAPTION_RISK", ["audio", "caption"], "AV_SEGMENT_REPLACE", .72
    elif _contains(reason, PROCESS_PATTERNS) or hint == "PROCESS":
        category, modalities, action, confidence = "MISSING_PROCESS_EVIDENCE", ["visual", "audio"], "PROCESS_INSERT", .72
    elif _contains(reason, VISUAL_PATTERNS) or hint == "VISUAL":
        category, modalities, action, confidence = "VISUAL_MISMATCH", ["visual"], "VISUAL_REPLACE", .72
    elif hint == "AV":
        category, modalities, action, confidence = "AUDIO_VISUAL_RISK", ["visual", "audio"], "AV_SEGMENT_REPLACE", .65
    if action != "UNSUPPORTED" and not ranges:
        action = "UNSUPPORTED"
        confidence = min(confidence, .45)
    evidence = [{"source": "REVIEW_TEXT", "description": reason or "审核理由为空"}]
    evidence.extend(_issue_range_evidence(ranges))
    issue = {
        "issue_id": "issue-1",
        "category": category,
        "description": reason or "无法可靠判断审核问题",
        "affected_modalities": modalities,
        "ranges": ranges,
        "evidence": evidence,
        "confidence": confidence,
        "suggested_action": action,
    }
    diagnosis = VideoRepairDiagnosisV2.model_validate({
        "summary": "规则降级诊断；需要人工确认" if action == "UNSUPPORTED" else "规则降级诊断完成",
        "issues": [issue],
        "primary_issue_id": "issue-1",
        "requires_human_range_confirmation": not ranges,
        "overall_confidence": confidence,
        "meta": {"mode": "RULE_FALLBACK", "error": error},
    })
    diagnosis.validate_duration(context["video"]["duration_ms"])
    validate_diagnosis_against_capabilities(diagnosis)
    return diagnosis


def diagnose_video(context: dict[str, Any], provider: MultimodalDiagnosisProvider | None,
                   *, prompt_version: str = "video-repair-diagnosis-v2") -> VideoRepairDiagnosisV2:
    if provider is None:
        return rule_fallback(context, "AI provider disabled or unavailable")
    started = time.monotonic()
    try:
        result: DiagnosisProviderResult = provider.diagnose(context)
        diagnosis = VideoRepairDiagnosisV2.model_validate(result.payload)
        diagnosis.validate_duration(context["video"]["duration_ms"])
        explicit_ranges = context["review"].get("explicit_time_ranges") or []
        primary = next(
            (issue for issue in diagnosis.issues if issue.issue_id == diagnosis.primary_issue_id), None)
        if primary and explicit_ranges:
            primary.ranges = [IssueRange.model_validate(row) for row in explicit_ranges]
            primary.evidence.extend(
                Evidence.model_validate(row) for row in _issue_range_evidence(explicit_ranges))
            diagnosis.requires_human_range_confirmation = False
        diagnosis.meta.mode = "MULTIMODAL"
        diagnosis.meta.model = result.model
        diagnosis.meta.prompt_version = prompt_version
        diagnosis.meta.latency_ms = round((time.monotonic() - started) * 1000)
        diagnosis.meta.token_usage = result.token_usage
        validate_diagnosis_against_capabilities(diagnosis)
        return diagnosis
    # The SDK boundary may raise provider-specific exceptions (for example an
    # OpenAI API timeout), so every ordinary provider failure must degrade to
    # the deterministic diagnosis instead of aborting task creation.
    except Exception as exc:
        return rule_fallback(context, f"{type(exc).__name__}: {exc}"[:1000])
