from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import ProductFact, VideoAsset, VideoAssetAnalysis
from app.services.video_repair.analysis_normalization import normalize_analysis
from app.services.video_repair.capabilities import REPAIR_CAPABILITIES
from app.services.video_repair.time_extraction import prioritized_time_ranges


def _timeline_segments(plan: dict[str, Any], key: str) -> list[dict[str, Any]]:
    variants = plan.get("variants") or []
    if not variants:
        return []
    result = []
    for segment in variants[0].get(key) or []:
        timeline = segment.get("timeline_range") or {}
        result.append({
            "start_ms": round(float(timeline.get("start", 0)) * 1000),
            "end_ms": round(float(timeline.get("end", 0)) * 1000),
            "asset_id": segment.get("asset_id"),
            "content_role": segment.get("content_role"),
            "speech_text": segment.get("speech_text", ""),
            "subtitle_words": segment.get("subtitle_words") or [],
            "audio_strategy": segment.get("audio_strategy"),
        })
    return result


def _observations(analysis: VideoAssetAnalysis | None, max_keyframes: int) -> dict[str, Any]:
    payload = normalize_analysis(analysis.analysis_json if analysis else None)
    keyframes = payload.get("keyframes") or payload.get("frames") or []
    keyframes = keyframes if isinstance(keyframes, list) else []
    if len(keyframes) > max_keyframes:
        step = max(1, len(keyframes) // max_keyframes)
        keyframes = keyframes[::step][:max_keyframes]
    asr_segments = payload["transcript"]["segments"]
    ocr_segments = payload.get("ocr_segments") or []
    return {
        "asr_segments": asr_segments if isinstance(asr_segments, list) else [],
        "ocr_segments": ocr_segments if isinstance(ocr_segments, list) else [],
        "keyframes": keyframes,
        "shots": payload.get("shots") or [],
    }


def build_ai_context(db: Session, *, repair_input: Any, source_asset: VideoAsset,
                     base_plan: dict[str, Any], max_keyframes: int = 12) -> dict[str, Any]:
    duration_ms = round(float(source_asset.duration_seconds or 0) * 1000)
    explicit_ranges = prioritized_time_ranges(
        repair_input.time_ranges_json or [], repair_input.raw_reason, duration_ms or None)
    analysis = db.query(VideoAssetAnalysis).filter_by(
        tenant_id=source_asset.tenant_id, asset_id=source_asset.id,
        status="completed").order_by(VideoAssetAnalysis.id.desc()).first()
    facts = db.query(ProductFact).filter_by(
        tenant_id=source_asset.tenant_id, product_id=source_asset.product_id,
        status="confirmed").all()
    return {
        "review": {
            "raw_reason": repair_input.raw_reason,
            "expected_result": getattr(repair_input, "expected_result", "") or "",
            "suggestions": repair_input.suggestions_json or [],
            "explicit_time_ranges": explicit_ranges,
            "category_hint": str((repair_input.raw_payload_json or {}).get("category_hint") or "UNKNOWN"),
        },
        "video": {
            "duration_ms": duration_ms,
            "width": source_asset.width,
            "height": source_asset.height,
            "burned_subtitles": source_asset.burned_subtitles,
        },
        "timeline": {
            "narrative_segments": _timeline_segments(base_plan, "narrative_segments"),
            "video_segments": _timeline_segments(base_plan, "video_track"),
        },
        "observations": _observations(analysis, max_keyframes),
        "product": {
            "product_id": source_asset.product_id,
            "known_facts": [{"key": row.fact_key, "label": row.label, "value": row.value}
                            for row in facts],
        },
        "capabilities": list(REPAIR_CAPABILITIES),
    }
