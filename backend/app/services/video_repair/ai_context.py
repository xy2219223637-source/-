from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import EcommerceProduct, EcommerceProductFact, Product, ProductAsset, ProductFact, VideoAsset, VideoAssetAnalysis
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
                     base_plan: dict[str, Any], max_keyframes: int = 12,
                     capabilities: list[str] | None = None,
                     source_analysis: VideoAssetAnalysis | None = None) -> dict[str, Any]:
    duration_ms = round(float(source_asset.duration_seconds or 0) * 1000)
    explicit_ranges = prioritized_time_ranges(
        repair_input.time_ranges_json or [], repair_input.raw_reason, duration_ms or None)
    analysis = source_analysis or db.query(VideoAssetAnalysis).filter_by(
        tenant_id=source_asset.tenant_id, asset_id=source_asset.id,
        status="completed").order_by(VideoAssetAnalysis.id.desc()).first()
    facts = db.query(ProductFact).filter_by(
        tenant_id=source_asset.tenant_id, product_id=source_asset.product_id,
        status="confirmed").all()
    product = db.query(Product).filter_by(
        tenant_id=source_asset.tenant_id, id=source_asset.product_id).first()
    ecommerce_product_id = (repair_input.raw_payload_json or {}).get("ecommerce_product_id")
    ecommerce_product = db.query(EcommerceProduct).filter_by(
        tenant_id=source_asset.tenant_id, id=ecommerce_product_id).first() if ecommerce_product_id else None
    if ecommerce_product:
        facts = db.query(EcommerceProductFact).filter_by(
            tenant_id=source_asset.tenant_id, product_id=ecommerce_product.id,
            status="confirmed").all()
    reference_assets = []
    if product:
        reference_assets = db.query(ProductAsset).filter(
            ProductAsset.tenant_id == source_asset.tenant_id,
            ProductAsset.product_id == product.id,
            ProductAsset.benchmark_role != "none",
            ProductAsset.excluded.is_(False),
        ).order_by(ProductAsset.priority.desc(), ProductAsset.id).limit(2).all()
    reference_images = [{
        "asset_id": row.id,
        "role": row.benchmark_role,
        "description": row.description,
        "url": row.file_url,
    } for row in reference_assets]
    display_product = ecommerce_product or product
    if display_product and not reference_images:
        reference_images = [{
            "asset_id": None,
            "role": "product_image",
            "description": "商品管理主图",
            "url": url,
        } for url in (display_product.image_urls or [])[:2]]
    return {
        "review": {
            "raw_reason": repair_input.raw_reason,
            "suggestions": repair_input.suggestions_json or [],
            "explicit_time_ranges": explicit_ranges,
            "category_hint": str((repair_input.raw_payload_json or {}).get("category_hint") or "UNKNOWN"),
        },
        "video": {
            "duration_ms": duration_ms,
            "width": source_asset.width,
            "height": source_asset.height,
            "burned_subtitles": source_asset.burned_subtitles,
            "source_url": source_asset.object_url,
        },
        "timeline": {
            "narrative_segments": _timeline_segments(base_plan, "narrative_segments"),
            "video_segments": _timeline_segments(base_plan, "video_track"),
        },
        "observations": _observations(analysis, max_keyframes),
        "product": {
            "product_id": display_product.id if display_product else source_asset.product_id,
            "name": display_product.name if display_product else "",
            "brand_name": display_product.brand_name if display_product else "",
            "category": display_product.category if display_product else "",
            "known_facts": [{"key": row.fact_key, "label": row.label, "value": row.value}
                            for row in facts],
            "reference_images": reference_images,
        },
        "capabilities": list(capabilities) if capabilities is not None else list(REPAIR_CAPABILITIES),
    }
