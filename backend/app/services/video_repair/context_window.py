from __future__ import annotations

from typing import Any


def _overlaps(segment: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    return int(segment.get("start_ms") or 0) < end_ms and int(segment.get("end_ms") or 0) > start_ms


def _window_summary(segments: list[dict[str, Any]], start_ms: int, end_ms: int) -> str:
    matching = [row for row in segments if _overlaps(row, start_ms, end_ms)]
    speech = [str(row.get("speech_text") or "").strip() for row in matching]
    speech = [value for value in speech if value]
    if speech:
        return " ".join(dict.fromkeys(speech))[:500]
    roles = [str(row.get("content_role") or "").strip() for row in matching]
    roles = [value for value in roles if value]
    return " / ".join(dict.fromkeys(roles))[:500]


def _observations_in_range(rows: list[dict[str, Any]], start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        raw_start = row.get("start_ms", row.get("start", 0))
        raw_end = row.get("end_ms", row.get("end", raw_start))
        scale = 1000 if "start_ms" not in row and max(float(raw_start or 0), float(raw_end or 0)) < 1000 else 1
        normalized = dict(row)
        normalized["start_ms"] = round(float(raw_start or 0) * scale)
        normalized["end_ms"] = round(float(raw_end or 0) * scale)
        if _overlaps(normalized, start_ms, end_ms):
            result.append(normalized)
    return result


def build_repair_context(
    ai_context: dict[str, Any], *, requested_range: dict[str, int], target_intent: str,
    pre_ms: int = 4000, post_ms: int = 4000, max_ms: int = 8000,
) -> dict[str, Any]:
    """Build deterministic context evidence without inventing missing media facts."""
    video = ai_context.get("video") or {}
    duration_ms = max(0, int(video.get("duration_ms") or requested_range["end_ms"]))
    start_ms = int(requested_range["start_ms"])
    end_ms = int(requested_range["end_ms"])
    pre_ms = min(max(0, pre_ms), max_ms)
    post_ms = min(max(0, post_ms), max_ms)
    pre_start = max(0, start_ms - pre_ms)
    post_end = min(duration_ms, end_ms + post_ms)
    timeline = ai_context.get("timeline") or {}
    segments = list(timeline.get("narrative_segments") or timeline.get("video_segments") or [])
    observations = ai_context.get("observations") or {}
    asr = _observations_in_range(list(observations.get("asr_segments") or []), start_ms, end_ms)
    ocr = _observations_in_range(list(observations.get("ocr_segments") or []), start_ms, end_ms)
    target_summary = _window_summary(segments, start_ms, end_ms)
    transcript_target = " ".join(
        str(row.get("text") or row.get("transcript") or "").strip() for row in asr
    ).strip()
    ocr_text = " ".join(str(row.get("text") or "").strip() for row in ocr).strip()
    target_has_subtitles = bool(ocr)
    subtitle_confidence = max(
        [float(row.get("confidence") or 0) for row in ocr], default=0.0)
    evidence: list[dict[str, Any]] = []
    if segments:
        evidence.append({"source": "TIMELINE", "confidence": 1.0,
                         "description": "前后文来自已冻结剪辑时间线"})
    if asr:
        evidence.append({"source": "ASR", "confidence": max(
            [float(row.get("confidence") or 0) for row in asr], default=0.5),
                         "description": "目标区间包含语音识别文本"})
    if ocr:
        evidence.append({"source": "OCR", "confidence": subtitle_confidence,
                         "description": "目标区间检测到 OCR 文本"})
    return {
        "schema_version": "repair_context.v1",
        "requested_range": {"start_ms": start_ms, "end_ms": end_ms},
        "context_window": {
            "pre": {"start_ms": pre_start, "end_ms": start_ms},
            "target": {"start_ms": start_ms, "end_ms": end_ms},
            "post": {"start_ms": end_ms, "end_ms": post_end},
        },
        "narrative": {
            "pre_summary": _window_summary(segments, pre_start, start_ms),
            "target_intent": target_intent,
            "target_summary": target_summary,
            "post_summary": _window_summary(segments, end_ms, post_end),
            "entities": [], "actions": [],
            "required_bridge": "候选应承接前文，并能自然进入后文",
        },
        "visual": {
            "aspect_ratio": f'{video.get("width")}:{video.get("height")}'
            if video.get("width") and video.get("height") else None,
        },
        "audio": {"has_speech": bool(asr or transcript_target),
                  "transcript_target": transcript_target},
        "subtitle": {
            "target_has_burned_subtitles": target_has_subtitles,
            "source_video_has_burned_subtitles": bool(video.get("burned_subtitles")),
            "ocr_text": ocr_text, "ocr_confidence": round(subtitle_confidence, 4),
        },
        "evidence": evidence,
    }
