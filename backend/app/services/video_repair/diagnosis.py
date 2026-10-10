from __future__ import annotations

import math
import re
from typing import Any


NON_VIDEO_PATTERNS = (
    "资质", "营业执照", "商品备案", "账户", "开户", "落地页", "预算", "定向", "保证金",
)
PROCESS_PATTERNS = ("使用过程", "操作过程", "补充过程", "缺少过程", "实际使用")
COPY_PATTERNS = (
    "绝对化", "夸大", "违规文案", "违规口播", "字幕", "最高级", "第一", "百分百", "100%",
    "根治", "永久", "最", "唯一", "保证",
)
VISUAL_PATTERNS = ("画面", "商品展示", "展示不准确", "效果展示", "效果对比", "误解", "敏感画面", "前后对比")


def _contains(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _duration(plan: dict[str, Any]) -> float:
    variants = plan.get("variants") or []
    if not variants:
        return 0.0
    try:
        value = float(variants[0].get("actual_duration_seconds") or 0)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def _normalized_ranges(rows: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            start, end = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
            continue
        if duration:
            start, end = min(start, duration), min(end, duration)
        if end <= start:
            continue
        result.append({
            "start": round(start, 3), "end": round(end, 3),
            "precision": "EXACT", "source": "INPUT", "confidence": 1.0,
            "evidence": "输入事实包含明确时间段",
        })
    return result


def _text_issue_ranges(plan: dict[str, Any], reason: str) -> list[dict[str, Any]]:
    variants = plan.get("variants") or []
    if not variants:
        return []
    risky_terms = [term for term in COPY_PATTERNS if term in reason]
    risky_terms.extend(re.findall(r"[“\"']([^”\"']{2,20})[”\"']", reason))
    generic = not risky_terms
    result = []
    for segment in variants[0].get("narrative_segments") or []:
        text = str(segment.get("speech_text") or "")
        words = "".join(str(word.get("text") or "") for word in segment.get("subtitle_words") or [])
        if generic or not any(term in text or term in words for term in risky_terms):
            continue
        timeline = segment.get("timeline_range") or {}
        try:
            start, end = float(timeline["start"]), float(timeline["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= start < end:
            result.append({
                "start": round(start, 3), "end": round(end, 3),
                "precision": "APPROXIMATE", "source": "SUBTITLE_ASR", "confidence": 0.78,
                "evidence": "审核文本与系统字幕或口播片段匹配",
            })
    return result


def diagnose_feedback(raw_reason: str, provider_time_ranges: list[dict[str, Any]],
                      plan: dict[str, Any], category_hint: str = "UNKNOWN") -> dict[str, Any]:
    """Deterministic V1 diagnosis. It never upgrades inferred timing to platform fact."""
    reason = raw_reason.strip()
    duration = _duration(plan)
    if _contains(reason, NON_VIDEO_PATTERNS):
        return {
            "normalized_category": "NON_VIDEO_COMPLIANCE",
            "content_fixable": False,
            "affected_modalities": ["full_video"],
            "issue_ranges": [],
            "recommended_action": "NON_VIDEO_FIXABLE",
            "explanation": "反馈指向账户、商品资质、落地页或投放设置，修改视频不能解决。",
            "evidence": [reason],
        }
    if _contains(reason, PROCESS_PATTERNS):
        category, modalities, action = "MISSING_PROCESS_EVIDENCE", ["audio_visual"], "PROCESS_INSERT"
    elif _contains(reason, COPY_PATTERNS):
        category, modalities, action = "PROHIBITED_COPY", ["audio", "caption"], "AUDIO_CAPTION_REWRITE"
    elif _contains(reason, VISUAL_PATTERNS):
        category, modalities, action = "MISLEADING_VISUAL_CLAIM", ["visual"], "VISUAL_REPLACE"
    elif category_hint == "VISUAL":
        category, modalities, action = "VISUAL_RISK", ["visual"], "VISUAL_REPLACE"
    elif category_hint == "COPY":
        category, modalities, action = "COPY_RISK", ["audio", "caption"], "AUDIO_CAPTION_REWRITE"
    elif category_hint == "AV":
        category, modalities, action = "AUDIO_VISUAL_RISK", ["audio_visual"], "AV_SEGMENT_REPLACE"
    elif category_hint == "PROCESS":
        category, modalities, action = "MISSING_PROCESS_EVIDENCE", ["audio_visual"], "PROCESS_INSERT"
    elif category_hint == "FULL":
        category, modalities, action = "FULL_VIDEO_RISK", ["full_video"], "FULL_REPLAN"
    else:
        category, modalities, action = "UNKNOWN_VIDEO_RISK", ["full_video"], "FULL_REPLAN"

    ranges = _normalized_ranges(provider_time_ranges, duration)
    if not ranges and action == "AUDIO_CAPTION_REWRITE":
        ranges = _text_issue_ranges(plan, reason)
    return {
        "normalized_category": category,
        "content_fixable": True,
        "affected_modalities": modalities,
        "issue_ranges": ranges,
        "recommended_action": action,
        "explanation": {
            "VISUAL_REPLACE": "建议只替换问题画面，保留原口播和字幕。",
            "AUDIO_CAPTION_REWRITE": "建议同步修改可编辑的系统口播与字幕。",
            "PROCESS_INSERT": "建议补充真实的商品使用过程素材。",
            "FULL_REPLAN": "无法可靠局部定位，建议人工确认后重新规划。",
        }[action],
        "evidence": [reason],
    }
