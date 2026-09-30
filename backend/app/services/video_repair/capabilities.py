from __future__ import annotations

from typing import Any


REPAIR_CAPABILITIES: dict[str, dict[str, Any]] = {
    "VISUAL_REPLACE": {
        "affected_tracks": ["video_track"],
        "duration_policy": "KEEP_TOTAL_DURATION",
        "requires_candidate": True,
        "requires_candidate_audio": False,
        "preserve_original_audio": True,
        "minimum_range_ms": 300,
    },
    "AV_SEGMENT_REPLACE": {
        "affected_tracks": ["video_track", "narrative_segments", "subtitles"],
        "duration_policy": "KEEP_TOTAL_DURATION",
        "requires_candidate": True,
        "requires_candidate_audio": True,
        "preserve_original_audio": False,
        "minimum_range_ms": 300,
    },
    "PROCESS_INSERT": {
        "affected_tracks": ["video_track", "narrative_segments", "subtitles"],
        "duration_policy": "EXTEND_TIMELINE",
        "requires_candidate": True,
        "requires_candidate_audio": True,
        "preserve_original_audio": False,
        "minimum_range_ms": 0,
    },
}


class CapabilityValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def capability_for(action: str) -> dict[str, Any]:
    capability = REPAIR_CAPABILITIES.get(action)
    if not capability:
        raise CapabilityValidationError("UNSUPPORTED_ACTION", f"系统不支持修复动作 {action}")
    return capability


def validate_diagnosis_against_capabilities(diagnosis: Any) -> None:
    issues = diagnosis.issues if hasattr(diagnosis, "issues") else diagnosis.get("issues", [])
    for issue in issues:
        action = issue.suggested_action if hasattr(issue, "suggested_action") else issue["suggested_action"]
        category = issue.category if hasattr(issue, "category") else issue["category"]
        modalities = set(
            issue.affected_modalities if hasattr(issue, "affected_modalities")
            else issue.get("affected_modalities", []))
        ranges = issue.ranges if hasattr(issue, "ranges") else issue.get("ranges", [])
        if action == "UNSUPPORTED":
            continue
        capability = capability_for(action)
        if category == "NON_VIDEO_COMPLIANCE":
            raise CapabilityValidationError(
                "ACTION_MODALITY_CONFLICT", "非视频问题不能生成视频修复动作")
        if action == "VISUAL_REPLACE" and ({"audio", "caption", "full_video"} & modalities):
            raise CapabilityValidationError(
                "ACTION_MODALITY_CONFLICT", "音频、字幕或全片问题不能只替换画面")
        if action == "PROCESS_INSERT" and category != "MISSING_PROCESS_EVIDENCE":
            raise CapabilityValidationError(
                "ACTION_MODALITY_CONFLICT", "插入动作仅用于补充缺失过程或证据")
        minimum = capability["minimum_range_ms"]
        for issue_range in ranges:
            start = issue_range.start_ms if hasattr(issue_range, "start_ms") else issue_range["start_ms"]
            end = issue_range.end_ms if hasattr(issue_range, "end_ms") else issue_range["end_ms"]
            if end - start < minimum:
                raise CapabilityValidationError(
                    "INVALID_TARGET_RANGE", f"修复范围短于动作最小值 {minimum}ms")


def validate_plan_against_capabilities(plan: dict[str, Any], source_asset: Any = None,
                                       base_plan: dict[str, Any] | None = None) -> None:
    for action in plan.get("actions") or []:
        capability = capability_for(action["type"])
        target = action.get("target_range") or {}
        start_ms = target.get("start_ms")
        end_ms = target.get("end_ms")
        if start_ms is None or end_ms is None or start_ms < 0 or end_ms <= start_ms:
            raise CapabilityValidationError("INVALID_TARGET_RANGE", "修复目标时间范围无效")
        if action["type"] != "PROCESS_INSERT" and end_ms - start_ms < capability["minimum_range_ms"]:
            raise CapabilityValidationError("INVALID_TARGET_RANGE", "修复目标时间范围过短")
        if action.get("affected_tracks") != capability["affected_tracks"]:
            raise CapabilityValidationError("ACTION_MODALITY_CONFLICT", "计划轨道影响与动作能力不一致")
        if action.get("duration_policy") != capability["duration_policy"]:
            raise CapabilityValidationError("ACTION_MODALITY_CONFLICT", "计划时长策略与动作能力不一致")
        if capability["requires_candidate"] and not action.get("selected_candidate_id"):
            raise CapabilityValidationError("CANDIDATE_NOT_ALLOWED", "修复动作尚未选择候选素材")
        snapshot = action.get("selected_candidate_snapshot")
        if capability["requires_candidate_audio"] and snapshot and not snapshot.get("has_audio"):
            raise CapabilityValidationError("CANDIDATE_AUDIO_REQUIRED", "该动作要求候选素材包含音频")
