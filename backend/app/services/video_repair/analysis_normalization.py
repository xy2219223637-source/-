from __future__ import annotations

from typing import Any


def normalize_transcript(analysis_json: dict[str, Any] | None) -> dict[str, Any]:
    """Return the V2 transcript contract while accepting historical field layouts."""
    payload = analysis_json or {}
    transcript = payload.get("transcript")
    transcript = transcript if isinstance(transcript, dict) else {}
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        segments = payload.get("asr_segments")
    if not isinstance(segments, list):
        segments = payload.get("transcript_segments")
    if not isinstance(segments, list):
        segments = []
    words = transcript.get("words") if isinstance(transcript.get("words"), list) else []
    text = transcript.get("text") if isinstance(transcript.get("text"), str) else ""
    if not text:
        text = " ".join(
            str(segment.get("text") or segment.get("transcript") or "").strip()
            for segment in segments if isinstance(segment, dict)
        ).strip()
    return {"segments": segments, "words": words, "text": text}


def normalize_analysis(analysis_json: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(analysis_json or {})
    payload["schema_version"] = "video_analysis.v2"
    payload["transcript"] = normalize_transcript(payload)
    for key in ("shots", "keyframes", "ocr_segments", "script_segments"):
        if not isinstance(payload.get(key), list):
            payload[key] = []
    return payload
