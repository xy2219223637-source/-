from __future__ import annotations

from copy import deepcopy
from typing import Any


def video_track_from_narrative(narrative: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the canonical visual track for legacy source-synchronous plans."""
    result: list[dict[str, Any]] = []
    for index, segment in enumerate(narrative, 1):
        segment_id = str(segment.get("segment_id") or f"segment-{index}")
        result.append({
            "clip_id": f"{segment_id}-source",
            "asset_id": segment["asset_id"],
            "highlight_id": segment.get("highlight_id", ""),
            "source_range": deepcopy(segment["source_range"]),
            "timeline_range": deepcopy(segment["timeline_range"]),
            "duration_seconds": segment["duration_seconds"],
            "content_role": segment.get("content_role", "custom"),
            "visual_type": "source_sync",
        })
    return result


def ensure_video_track(variant: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with a visual track, preserving already-canonical plans."""
    normalized = deepcopy(variant)
    if not normalized.get("video_track"):
        normalized["video_track"] = video_track_from_narrative(
            normalized.get("narrative_segments") or [])
    return normalized


def tracks_are_source_synced(variant: dict[str, Any]) -> bool:
    """Whether the visual track is only a mirror of the coupled A/V narrative."""
    video = variant.get("video_track") or []
    narrative = variant.get("narrative_segments") or []
    if len(video) != len(narrative) or not video:
        return False
    keys = ("asset_id", "source_range", "timeline_range")
    return all(all(visual.get(key) == audio.get(key) for key in keys)
               for visual, audio in zip(video, narrative))
