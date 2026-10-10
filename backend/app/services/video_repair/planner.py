from __future__ import annotations

from typing import Any

from app.services.video_repair.capabilities import REPAIR_CAPABILITIES


def _value(row: Any, name: str, default: Any = None) -> Any:
    return getattr(row, name, row.get(name, default) if isinstance(row, dict) else default)


def select_action_for_issue(issue: Any) -> str:
    category = _value(issue, "category")
    modalities = set(_value(issue, "affected_modalities", []))
    suggested = _value(issue, "suggested_action", "UNSUPPORTED")
    if category == "NON_VIDEO_COMPLIANCE":
        return "UNSUPPORTED"
    if {"audio", "caption"} & modalities:
        return "AV_SEGMENT_REPLACE"
    if modalities == {"visual"}:
        return "VISUAL_REPLACE"
    if category == "MISSING_PROCESS_EVIDENCE":
        return "AV_SEGMENT_REPLACE" if suggested == "AV_SEGMENT_REPLACE" else "PROCESS_INSERT"
    return suggested if suggested in REPAIR_CAPABILITIES else "UNSUPPORTED"


def _candidate_source_range(candidate: dict[str, Any]) -> dict[str, int]:
    source = candidate["source_range"]
    return {"start_ms": round(float(source["start"]) * 1000),
            "end_ms": round(float(source["end"]) * 1000)}


def build_plan_action(action_type: str, target_range: dict[str, int],
                      candidates: list[dict[str, Any]], *, action_id: str = "action-1",
                      selected_candidate_id: str | None = None) -> dict[str, Any]:
    capability = REPAIR_CAPABILITIES[action_type]
    selected = next(
        (row for row in candidates if row["id"] == selected_candidate_id), None
    ) if selected_candidate_id else None
    recommended = next(
        (row for row in candidates if row.get("recommendation_level") == "RECOMMENDED"), None)
    target_duration_ms = max(0, target_range["end_ms"] - target_range["start_ms"])
    delta = round(selected["duration_seconds"] * 1000) if (
        selected and action_type == "PROCESS_INSERT") else 0
    return {
        "action_id": action_id,
        "type": action_type,
        "target_range": target_range,
        "affected_tracks": list(capability["affected_tracks"]),
        "duration_policy": capability["duration_policy"],
        "candidate_ids": [row["id"] for row in candidates],
        "candidates": candidates,
        "recommended_candidate_id": recommended["id"] if recommended else None,
        "selected_candidate_id": selected["id"] if selected else None,
        "candidate_score": ({key: selected.get(key) for key in (
            "score", "recommendation_level", "score_breakdown", "reasons", "warnings",
        )} if selected else None),
        "candidate_source_range": _candidate_source_range(selected) if selected else None,
        "predicted_effects": {
            "total_duration_delta_ms": delta,
            "visual_changed": True,
            "audio_changed": action_type != "VISUAL_REPLACE",
            "caption_changed": action_type == "AV_SEGMENT_REPLACE",
            "target_duration_ms": target_duration_ms,
        },
    }


def build_repair_plan(diagnosis: Any, base_plan: dict[str, Any],
                      candidates: list[dict[str, Any]],
                      repair_context: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    issues = _value(diagnosis, "issues", [])
    primary_id = _value(diagnosis, "primary_issue_id")
    primary = next((row for row in issues if _value(row, "issue_id") == primary_id), None)
    generated_by = {
        "diagnosis_mode": _value(_value(diagnosis, "meta", {}), "mode", "RULE_FALLBACK"),
        "model": _value(_value(diagnosis, "meta", {}), "model"),
        "prompt_version": _value(
            _value(diagnosis, "meta", {}), "prompt_version", "video-repair-diagnosis-v2"),
    }
    if primary is None:
        return "NEEDS_CONFIRMATION", {
            "schema_version": "video_repair_plan.v3", "primary_issue_id": None,
            "primary_action": "UNSUPPORTED", "actions": [],
            "warnings": ["AI 未形成可执行主问题，请人工指定动作和时间段"],
            "generated_by": generated_by, "repair_context": repair_context,
        }
    action = select_action_for_issue(primary)
    category = _value(primary, "category")
    if category == "NON_VIDEO_COMPLIANCE":
        return "NON_VIDEO_FIXABLE", {
            "schema_version": "video_repair_plan.v3", "primary_issue_id": primary_id,
            "primary_action": "UNSUPPORTED", "actions": [],
            "warnings": [_value(primary, "description")], "generated_by": generated_by,
            "repair_context": repair_context,
        }
    ranges = _value(primary, "ranges", [])
    if action == "UNSUPPORTED" or not ranges:
        return "NEEDS_CONFIRMATION", {
            "schema_version": "video_repair_plan.v3", "primary_issue_id": primary_id,
            "primary_action": action, "actions": [],
            "warnings": ["诊断尚未形成完整可执行方案，请人工指定动作和时间段"],
            "generated_by": generated_by, "repair_context": repair_context,
        }
    issue_range = ranges[0]
    target = {"start_ms": int(_value(issue_range, "start_ms")),
              "end_ms": int(_value(issue_range, "end_ms"))}
    warnings: list[str] = []
    suggested = _value(primary, "suggested_action")
    if action != suggested:
        warnings.append(f"AI 建议 {suggested} 与轨道能力冲突，系统调整为 {action}")
    if not candidates:
        return "NEED_MORE_MATERIAL", {
            "schema_version": "video_repair_plan.v3", "primary_issue_id": primary_id,
            "primary_action": action,
            "actions": [build_plan_action(action, target, [])],
            "warnings": warnings + ["当前商品素材库没有满足动作硬约束的候选片段"],
            "generated_by": generated_by, "repair_context": repair_context,
        }
    return "NEEDS_CONFIRMATION", {
        "schema_version": "video_repair_plan.v3", "primary_issue_id": primary_id,
        "primary_action": action,
        "actions": [build_plan_action(action, target, candidates)],
        "warnings": warnings, "generated_by": generated_by,
        "repair_context": repair_context,
    }
