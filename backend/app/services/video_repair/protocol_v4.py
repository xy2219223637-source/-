from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA_VERSION = "video_repair_plan.v4"
ACTIONS = frozenset({"VISUAL_REPLACE", "AUDIO_REPLACE", "CONTENT_INSERT"})

ACTION_CAPABILITIES = {
    "VISUAL_REPLACE": {
        "affected_tracks": ["video"],
        "duration_policy": "KEEP_TOTAL_DURATION",
        "requires_visual_material": True,
        "requires_audio_input": False,
        "preserve_source_audio": True,
    },
    "AUDIO_REPLACE": {
        "affected_tracks": ["audio"],
        "duration_policy": "KEEP_TOTAL_DURATION",
        "requires_visual_material": False,
        "requires_audio_input": True,
        "preserve_source_video": True,
    },
    "CONTENT_INSERT": {
        "affected_tracks": ["video", "audio"],
        "duration_policy": "EXTEND_TIMELINE",
        "requires_visual_material": True,
        "requires_audio_input": False,
        "supports_candidate_audio": True,
    },
}


class InvalidRepairPlan(ValueError):
    pass


def _positive_range(value: Any, name: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise InvalidRepairPlan(f"{name} 必须是对象")
    try:
        start_ms, end_ms = int(value["start_ms"]), int(value["end_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRepairPlan(f"{name} 缺少合法的 start_ms/end_ms") from exc
    if start_ms < 0 or end_ms <= start_ms:
        raise InvalidRepairPlan(f"{name} 时间范围无效")
    return {"start_ms": start_ms, "end_ms": end_ms}


def validate_action(action: dict[str, Any]) -> dict[str, Any]:
    action_type = action.get("type")
    if action_type not in ACTIONS:
        raise InvalidRepairPlan(f"不支持的 V4 修复动作: {action_type}")
    normalized = dict(action)
    normalized["duration_policy"] = ACTION_CAPABILITIES[action_type]["duration_policy"]
    if action_type == "VISUAL_REPLACE":
        normalized["target_range"] = _positive_range(action.get("target_range"), "target_range")
        material = action.get("material")
        if not isinstance(material, dict) or material.get("ref_type") != "VIDEO_ASSET" or not material.get("ref_id"):
            raise InvalidRepairPlan("画面替换必须选择 VIDEO_ASSET 素材")
        normalized["material"] = dict(material)
        normalized["material"]["source_range"] = _positive_range(
            material.get("source_range"), "material.source_range")
    elif action_type == "AUDIO_REPLACE":
        normalized["target_range"] = _positive_range(action.get("target_range"), "target_range")
        audio = action.get("audio")
        if not isinstance(audio, dict) or audio.get("source_type") not in {"ATTACHMENT", "TTS"}:
            raise InvalidRepairPlan("音频替换必须引用 ATTACHMENT 或 TTS 音频")
        if not audio.get("attachment_id"):
            raise InvalidRepairPlan("音频替换缺少 attachment_id")
    else:
        insert_at_ms = action.get("insert_at_ms")
        duration_ms = action.get("duration_ms")
        if not isinstance(insert_at_ms, int) or insert_at_ms < 0:
            raise InvalidRepairPlan("内容插入缺少合法的 insert_at_ms")
        if not isinstance(duration_ms, int) or duration_ms <= 0:
            raise InvalidRepairPlan("内容插入缺少合法的 duration_ms")
        if not isinstance(action.get("visual"), dict) or not isinstance(action.get("audio"), dict):
            raise InvalidRepairPlan("内容插入必须同时配置视觉和音频")
        visual = dict(action["visual"])
        if visual.get("ref_type") != "VIDEO_ASSET" or not visual.get("ref_id"):
            raise InvalidRepairPlan("内容插入必须选择 VIDEO_ASSET 视觉素材")
        visual["source_range"] = _positive_range(visual.get("source_range"), "visual.source_range")
        audio = dict(action["audio"])
        if audio.get("source_type") == "VIDEO_ASSET":
            if audio.get("ref_type") != "VIDEO_ASSET" or not audio.get("ref_id"):
                raise InvalidRepairPlan("内容插入的素材原声引用无效")
            audio["source_range"] = _positive_range(audio.get("source_range"), "audio.source_range")
        elif audio.get("source_type") not in {"ATTACHMENT", "TTS"} or not audio.get("attachment_id"):
            raise InvalidRepairPlan("内容插入必须选择素材原声、ATTACHMENT 或 TTS 音频")
        normalized["visual"] = visual
        normalized["audio"] = audio
    return normalized


def build_plan(*, task_id: int, draft_revision: int, source_asset_id: int,
               product_id: int, action: dict[str, Any], diagnosis_snapshot: dict[str, Any] | None = None,
               warnings: list[str] | None = None, generated_by: dict[str, Any] | None = None,
               updated_by: str = "USER") -> dict[str, Any]:
    if min(task_id, draft_revision, source_asset_id, product_id) <= 0:
        raise InvalidRepairPlan("任务、草稿、成品和商品标识必须为正整数")
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "draft_revision": draft_revision,
        "source_asset_id": source_asset_id,
        "product_id": product_id,
        "action": validate_action(action),
        "diagnosis_snapshot": diagnosis_snapshot or {},
        "warnings": warnings or [],
        "generated_by": generated_by or {},
        "updated_by": updated_by,
    }


def plan_hash(plan: dict[str, Any]) -> str:
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise InvalidRepairPlan("只能计算 video_repair_plan.v4 的计划哈希")
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
