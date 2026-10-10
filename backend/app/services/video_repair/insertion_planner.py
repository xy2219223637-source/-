from __future__ import annotations

from typing import Any


_INTRO_WORDS = ("介绍", "这款", "产品", "首先", "打开", "我们看")
_RESULT_WORDS = ("效果", "使用后", "总结", "推荐", "下单", "赶紧", "所以")


def _milliseconds(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        seconds_key = key.removesuffix("_ms")
        value = row.get(seconds_key)
        if value is not None:
            try:
                return round(float(value) * 1000)
            except (TypeError, ValueError):
                return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def recommend_insertion_positions(context: dict[str, Any], *, duration_ms: int) -> dict[str, Any]:
    """Rank safe insertion boundaries without pretending a heuristic is model evidence."""
    explicit = (context.get("review") or {}).get("explicit_time_ranges") or []
    if explicit:
        start_ms = _milliseconds(explicit[0], "start_ms")
        if start_ms is not None:
            return {
                "recommended_ms": max(0, min(duration_ms, start_ms)),
                "alternatives": [], "confidence": .95, "confidence_level": "HIGH",
                "basis": "审核意见提供了明确时间位置", "evidence": ["REVIEW_TIME_RANGE"],
            }

    timeline = context.get("timeline") or {}
    observations = context.get("observations") or {}
    rows: list[dict[str, Any]] = []
    for key in ("narrative_segments", "video_segments"):
        rows.extend(row for row in timeline.get(key) or [] if isinstance(row, dict))
    for key in ("shots", "asr_segments"):
        rows.extend(row for row in observations.get(key) or [] if isinstance(row, dict))

    ranked: dict[int, tuple[float, list[str]]] = {}
    for row in rows:
        boundary = _milliseconds(row, "end_ms")
        if boundary is None or boundary <= duration_ms * .12 or boundary >= duration_ms * .92:
            continue
        ratio = boundary / max(duration_ms, 1)
        score = .5 - abs(ratio - .62) * .45
        reasons = ["候选位置位于已有时间轴片段边界"]
        text = " ".join(str(row.get(key) or "") for key in (
            "text", "transcript", "speech_text", "description", "story_role", "content_role"))
        if any(word in text for word in _INTRO_WORDS):
            score += .18; reasons.append("位于产品介绍或打开环节之后")
        if any(word in text for word in _RESULT_WORDS):
            score -= .08
        current = ranked.get(boundary)
        if current is None or score > current[0]:
            ranked[boundary] = (score, reasons)

    if ranked:
        ordered = sorted(ranked.items(), key=lambda item: item[1][0], reverse=True)[:3]
        best_ms, (best_score, reasons) = ordered[0]
        confidence = min(.88, max(.58, best_score))
        level = "HIGH" if confidence >= .8 else "MEDIUM"
        return {
            "recommended_ms": best_ms,
            "alternatives": [position for position, _ in ordered[1:]],
            "confidence": round(confidence, 2), "confidence_level": level,
            "basis": "；".join(reasons), "evidence": ["TIMELINE_BOUNDARY"],
        }

    # There is no transcript/shot evidence. Give a reversible preview default,
    # but label it LOW so the UI and audit trail do not overstate certainty.
    positions = [round(duration_ms * ratio / 100) * 100 for ratio in (.62, .48, .76)]
    positions = [max(0, min(duration_ms, value)) for value in positions]
    return {
        "recommended_ms": positions[0], "alternatives": positions[1:],
        "confidence": .35, "confidence_level": "LOW",
        "basis": "原片暂无字幕、镜头边界和关键帧证据，先在内容展开后段生成可撤销预览",
        "evidence": ["DURATION_HEURISTIC"],
    }
