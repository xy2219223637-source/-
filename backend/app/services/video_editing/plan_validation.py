from __future__ import annotations

import math
from typing import Any, Iterable


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def validate_timeline(clips: Iterable[dict[str, Any]], *, require_contiguous: bool = True,
                      tolerance: float = 0.05) -> list[str]:
    errors: list[str] = []
    previous_end = 0.0
    for index, clip in enumerate(clips, 1):
        timeline = clip.get("timeline_range") or {}
        start, end = _number(timeline.get("start")), _number(timeline.get("end"))
        if start is None or end is None or start < 0 or end <= start:
            errors.append(f"片段 {index} 的成片时间段无效")
            continue
        if require_contiguous and abs(start - previous_end) > tolerance:
            errors.append(f"片段 {index} 与前一片段不连续")
        elif not require_contiguous and start < previous_end - tolerance:
            errors.append(f"片段 {index} 与前一片段重叠")
        source = clip.get("source_range")
        if source is not None:
            source_start = _number(source.get("start"))
            source_end = _number(source.get("end"))
            if source_start is None or source_end is None or source_start < 0 or source_end <= source_start:
                errors.append(f"片段 {index} 的来源时间段无效")
            elif abs((end - start) - (source_end - source_start)) > tolerance:
                errors.append(f"片段 {index} 的来源时长与成片时长不一致")
        previous_end = end
    return errors


def validate_repair_variant(variant: dict[str, Any], valid_asset_ids: set[str]) -> list[str]:
    errors: list[str] = []
    for track in ("video_track", "narrative_segments"):
        clips = variant.get(track) or []
        if not clips:
            errors.append(f"缺少 {track}")
            continue
        errors.extend(f"{track}: {error}" for error in validate_timeline(clips))
        for index, clip in enumerate(clips, 1):
            asset_id = clip.get("asset_id")
            if asset_id and str(asset_id) not in valid_asset_ids:
                errors.append(f"{track} 片段 {index} 引用了不可用素材")
    duration = _number(variant.get("actual_duration_seconds"))
    video_end = _number(((variant.get("video_track") or [{}])[-1].get("timeline_range") or {}).get("end"))
    audio_end = _number(((variant.get("narrative_segments") or [{}])[-1].get("timeline_range") or {}).get("end"))
    if duration is None or duration <= 0:
        errors.append("输出总时长无效")
    elif video_end is None or abs(video_end - duration) > 0.05:
        errors.append("画面轨结束时间与输出时长不一致")
    elif audio_end is None or abs(audio_end - duration) > 0.05:
        errors.append("叙事轨结束时间与输出时长不一致")
    return errors
