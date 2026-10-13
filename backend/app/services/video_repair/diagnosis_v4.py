"""V4 diagnosis contract: the model may propose only executable V4 actions."""
from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import Field, model_validator

from app.services.video_repair.ai_provider import MultimodalDiagnosisProvider
from app.services.video_repair.diagnosis_schema import DiagnosedIssue, Evidence, IssueRange, VideoRepairDiagnosisV2
from app.services.video_repair.diagnosis_v2 import _issue_range_evidence, rule_fallback
from app.services.video_repair.protocol_v4 import ACTIONS, V4DiagnosticAction


V4_ACTION_GUIDANCE = {
    "VISUAL_REPLACE": "只替换画面，保留原声音和字幕；只适用于纯画面问题。",
    "AUDIO_REPLACE": "只替换声音，保留原画面和字幕；只适用于纯音频问题。",
    "CONTENT_INSERT": "新增一段画面和声音并延长总时长；适用于缺少功能展示、使用过程或证明片段，且新增完整音画片段即可解决。",
    "UNSUPPORTED": "若问题同时要求修改画面与声音或字幕，或需要单独修改字幕，必须选择此项。",
}


class V4DiagnosedIssue(DiagnosedIssue):
    suggested_action: V4DiagnosticAction

    @model_validator(mode="after")
    def validate_v4_action(self):
        modalities = set(self.affected_modalities)
        if self.suggested_action == "VISUAL_REPLACE" and modalities != {"visual"}:
            raise ValueError("画面替换只能处理纯画面问题")
        if self.suggested_action == "AUDIO_REPLACE" and modalities != {"audio"}:
            raise ValueError("音频替换不能修复画面或字幕")
        if self.suggested_action == "CONTENT_INSERT" and self.category != "MISSING_PROCESS_EVIDENCE":
            raise ValueError("内容插入只能处理缺少过程或证据")
        if self.suggested_action == "CONTENT_INSERT" and "caption" in modalities:
            raise ValueError("内容插入不能修复已有字幕")
        return self


class VideoRepairDiagnosisV4(VideoRepairDiagnosisV2):
    schema_version: Literal["video_repair_diagnosis.v4"] = "video_repair_diagnosis.v4"
    issues: list[V4DiagnosedIssue] = Field(default_factory=list)


def _available(context: dict[str, Any], diagnosis: VideoRepairDiagnosisV4) -> VideoRepairDiagnosisV4:
    available = set(context.get("capabilities") or [])
    for issue in diagnosis.issues:
        if issue.suggested_action != "UNSUPPORTED" and issue.suggested_action not in available:
            raise ValueError(f"当前任务未启用动作 {issue.suggested_action}")
    return diagnosis


def _normalize_proposed_actions(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Preserve model evidence while refusing unsafe or legacy action suggestions."""
    result = dict(payload)
    result["schema_version"] = "video_repair_diagnosis.v4"
    available = set(context.get("capabilities") or [])
    issues = []
    for item in result.get("issues") or []:
        if not isinstance(item, dict):
            issues.append(item)
            continue
        issue = dict(item)
        action = issue.get("suggested_action")
        modalities = set(issue.get("affected_modalities") or [])
        category = issue.get("category")
        if (action not in ACTIONS or action not in available
                or action == "VISUAL_REPLACE" and modalities != {"visual"}
                or action == "AUDIO_REPLACE" and modalities != {"audio"}
                or action == "CONTENT_INSERT" and (category != "MISSING_PROCESS_EVIDENCE"
                                                    or "caption" in modalities)):
            issue["suggested_action"] = "UNSUPPORTED"
        issues.append(issue)
    result["issues"] = issues
    return result


def rule_fallback_v4(context: dict[str, Any], error: str | None = None) -> VideoRepairDiagnosisV4:
    legacy = rule_fallback(context, error)
    payload = legacy.model_dump(mode="json")
    payload["schema_version"] = "video_repair_diagnosis.v4"
    available = set(context.get("capabilities") or [])
    for issue in payload["issues"]:
        modalities = set(issue["affected_modalities"])
        if issue["suggested_action"] == "VISUAL_REPLACE" and modalities == {"visual"}:
            action = "VISUAL_REPLACE"
        elif (issue["category"] == "MISSING_PROCESS_EVIDENCE"
              and issue["suggested_action"] == "PROCESS_INSERT"):
            action = "CONTENT_INSERT"
        elif modalities == {"audio"} and issue["suggested_action"] != "UNSUPPORTED":
            action = "AUDIO_REPLACE"
        else:
            action = "UNSUPPORTED"
        issue["suggested_action"] = action if action in available else "UNSUPPORTED"
    return VideoRepairDiagnosisV4.model_validate(payload)


def diagnose_video_v4(context: dict[str, Any], provider: MultimodalDiagnosisProvider | None,
                      *, prompt_version: str = "video-repair-diagnosis-v4") -> VideoRepairDiagnosisV4:
    if provider is None:
        diagnosis = rule_fallback_v4(context, "AI provider disabled or unavailable")
        for issue in diagnosis.issues:
            issue.suggested_action = "UNSUPPORTED"
        diagnosis.summary = "多模态诊断未启用，规则结果仅供定位参考；请人工判断修复动作。"
        return diagnosis
    started = time.monotonic()
    try:
        result = provider.diagnose(context, schema_model=VideoRepairDiagnosisV4)
        diagnosis = VideoRepairDiagnosisV4.model_validate(
            _normalize_proposed_actions(result.payload, context))
        explicit_ranges = context["review"].get("explicit_time_ranges") or []
        primary = next((issue for issue in diagnosis.issues
                        if issue.issue_id == diagnosis.primary_issue_id), None)
        if primary and explicit_ranges:
            primary.ranges = [IssueRange.model_validate(row) for row in explicit_ranges]
            primary.evidence.extend(Evidence.model_validate(row)
                                    for row in _issue_range_evidence(explicit_ranges))
            diagnosis.requires_human_range_confirmation = False
        diagnosis.validate_duration(context["video"]["duration_ms"])
        _available(context, diagnosis)
        diagnosis.meta.mode = "MULTIMODAL"
        diagnosis.meta.model = result.model
        diagnosis.meta.input_mode = result.input_mode
        diagnosis.meta.prompt_version = prompt_version
        diagnosis.meta.latency_ms = round((time.monotonic() - started) * 1000)
        diagnosis.meta.token_usage = result.token_usage
        return diagnosis
    except Exception as exc:
        diagnosis = rule_fallback_v4(context, f"{type(exc).__name__}: {exc}"[:1000])
        for issue in diagnosis.issues:
            issue.suggested_action = "UNSUPPORTED"
        diagnosis.summary = "多模态诊断失败，规则结果仅供定位参考；请人工判断修复动作。"
        return diagnosis
