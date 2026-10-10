"""Import completed legacy finished-video analysis for identical ecommerce media."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    EcommerceProduct, EcommerceVideoAsset, EcommerceVideoAssetAnalysis,
    EcommerceVideoHighlight, Product, VideoAsset, VideoAssetAnalysis,
    VideoHighlight,
)


def import_finished_video_analysis(
    db: Session, *, tenant_id: str, legacy_product_id: int,
    ecommerce_product_id: int,
) -> dict[str, int]:
    """Copy analysis metadata only; callers own the transaction and commit."""
    legacy_product = db.query(Product).filter_by(
        id=legacy_product_id, tenant_id=tenant_id).one()
    ecommerce_product = db.query(EcommerceProduct).filter_by(
        id=ecommerce_product_id, tenant_id=tenant_id).one()
    if (legacy_product.name, legacy_product.brand_name, legacy_product.category) != (
        ecommerce_product.name, ecommerce_product.brand_name, ecommerce_product.category
    ):
        raise ValueError("PRODUCT_IDENTITY_MISMATCH")

    old_assets = db.query(VideoAsset).filter_by(
        tenant_id=tenant_id, product_id=legacy_product_id,
        library_type="finished", asset_kind="material", status="analyzed",
        excluded=False,
    ).all()
    ecommerce_assets = db.query(EcommerceVideoAsset).filter_by(
        tenant_id=tenant_id, product_id=ecommerce_product_id,
        library_type="finished", asset_kind="material",
    ).all()
    by_hash: dict[str, list[EcommerceVideoAsset]] = {}
    for asset in ecommerce_assets:
        by_hash.setdefault(asset.content_hash, []).append(asset)

    result = {"matched": 0, "imported_assets": 0, "imported_highlights": 0,
              "already_analyzed": 0, "unmatched": 0}
    for source in old_assets:
        matches = by_hash.get(source.content_hash, []) if source.content_hash else []
        if len(matches) != 1:
            result["unmatched"] += 1
            continue
        target = matches[0]
        if (abs(source.duration_seconds - target.duration_seconds) > .1 or
                source.width and source.height and target.width and target.height and
                (source.width >= source.height) != (target.width >= target.height)):
            result["unmatched"] += 1
            continue
        result["matched"] += 1
        existing = db.query(EcommerceVideoAssetAnalysis).filter_by(
            tenant_id=tenant_id, asset_id=target.id, status="completed").first()
        if existing:
            result["already_analyzed"] += 1
            continue
        analyses = db.query(VideoAssetAnalysis).filter_by(
            tenant_id=tenant_id, asset_id=source.id, status="completed").order_by(
            VideoAssetAnalysis.id.desc()).all()
        selected = None
        highlights: list[VideoHighlight] = []
        for analysis in analyses:
            highlights = db.query(VideoHighlight).filter_by(
                tenant_id=tenant_id, asset_id=source.id,
                analysis_id=analysis.id).order_by(VideoHighlight.id).all()
            if highlights:
                selected = analysis
                break
        if selected is None:
            result["unmatched"] += 1
            continue
        copied = EcommerceVideoAssetAnalysis(
            tenant_id=tenant_id, asset_id=target.id,
            schema_version=selected.schema_version, model=selected.model,
            prompt_version=f"imported-legacy:{selected.id}",
            status="completed", summary=selected.summary,
            analysis_json=selected.analysis_json,
            input_video_seconds=selected.input_video_seconds,
            request_count=selected.request_count, retry_count=selected.retry_count,
            created_at=selected.created_at, completed_at=selected.completed_at,
        )
        db.add(copied)
        db.flush()
        for item in highlights:
            db.add(EcommerceVideoHighlight(
                tenant_id=tenant_id, asset_id=target.id, analysis_id=copied.id,
                highlight_id=f"legacy-{source.id}-{item.id}",
                start_seconds=item.start_seconds, end_seconds=item.end_seconds,
                description=item.description, reason=item.reason,
                recommended_roles=item.recommended_roles,
                content_role=item.content_role, track_role=item.track_role,
                speech_present=item.speech_present, lip_visible=item.lip_visible,
                speech_text=item.speech_text, speaker_id=item.speaker_id,
                audio_strategy=item.audio_strategy, shot_type=item.shot_type,
                selling_points=item.selling_points, score=item.score,
            ))
        target.status = "analyzed"
        target.ai_tags = list(source.ai_tags or [])
        target.burned_subtitles = source.burned_subtitles
        target.has_background_music = source.has_background_music
        result["imported_assets"] += 1
        result["imported_highlights"] += len(highlights)
    db.flush()
    return result
