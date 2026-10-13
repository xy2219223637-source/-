"""Expose a product's ecommerce videos to the existing repair renderer."""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    EcommerceVideoAsset, EcommerceVideoAssetAnalysis, EcommerceVideoHighlight,
    VideoAsset, VideoAssetAnalysis, VideoHighlight, VideoRepairInput, VideoRepairTask,
)


def _create_or_reuse_mirror(db: Session, task: VideoRepairTask,
                            row: EcommerceVideoAsset) -> VideoAsset:
    marker = f"ecommerce_video_asset:{row.id}"
    mirror = VideoAsset(
        tenant_id=task.tenant_id, product_id=task.product_id, project_id=None,
        asset_id=f"repair-ecommerce-{row.id}", filename=row.filename, source=marker,
        library_type="repair_only", asset_kind="material",
        object_url=row.object_url, content_hash=row.content_hash,
        storage_provider=row.storage_provider, duration_seconds=row.duration_seconds,
        width=row.width, height=row.height, fps=row.fps, has_audio=row.has_audio,
        burned_subtitles=row.burned_subtitles, has_background_music=row.has_background_music,
        reusable_status=row.reusable_status, status="uploaded", excluded=False,
    )
    try:
        # A savepoint keeps the caller's transaction usable after a unique-key race.
        with db.begin_nested():
            db.add(mirror)
            db.flush()
        return mirror
    except IntegrityError:
        existing = db.query(VideoAsset).filter_by(
            tenant_id=task.tenant_id, asset_id=mirror.asset_id,
            product_id=task.product_id, source=marker).first()
        if existing is None:
            raise
        return existing


def sync_ecommerce_materials(db: Session, task: VideoRepairTask) -> list[VideoAsset]:
    """Mirror media metadata and completed analysis without copying video bytes."""
    repair_input = db.get(VideoRepairInput, task.input_id)
    ecommerce_product_id = (repair_input.raw_payload_json or {}).get("ecommerce_product_id") if repair_input else None
    if not ecommerce_product_id:
        return []
    query = db.query(EcommerceVideoAsset).filter_by(
        tenant_id=task.tenant_id, product_id=ecommerce_product_id,
        asset_kind="material").order_by(EcommerceVideoAsset.id)
    # The API and repair worker may synchronize the same product concurrently.
    # Serialize the whole mirror + analysis copy, not just the first INSERT.
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    rows = query.all()
    mirrors: list[VideoAsset] = []
    for row in rows:
        marker = f"ecommerce_video_asset:{row.id}"
        mirror = db.query(VideoAsset).filter_by(
            tenant_id=task.tenant_id, product_id=task.product_id, source=marker).first()
        usable = not row.excluded and row.reusable_status not in {"excluded", "blocked"}
        if not mirror and usable and row.content_hash:
            existing = db.query(VideoAsset).filter_by(
                tenant_id=task.tenant_id, product_id=task.product_id,
                asset_kind="material", content_hash=row.content_hash,
                excluded=False).filter(VideoAsset.source != marker).first()
            if existing and existing.object_url and existing.duration_seconds > 0:
                mirrors.append(existing)
                continue
        if not mirror:
            if not usable or not row.object_url or row.duration_seconds <= 0:
                continue
            mirror = _create_or_reuse_mirror(db, task, row)
        mirror.excluded = not usable
        mirror.reusable_status = row.reusable_status
        if not usable:
            continue
        for field in ("filename", "object_url", "content_hash", "storage_provider",
                      "duration_seconds", "width", "height", "fps", "has_audio",
                      "burned_subtitles", "has_background_music"):
            setattr(mirror, field, getattr(row, field))
        analysis = db.query(EcommerceVideoAssetAnalysis).filter_by(
            tenant_id=task.tenant_id, asset_id=row.id, status="completed").order_by(
            EcommerceVideoAssetAnalysis.id.desc()).first()
        if not analysis:
            mirror.status = "uploaded"
            mirrors.append(mirror)
            continue
        version = f"ecommerce-analysis:{analysis.id}"
        copied = db.query(VideoAssetAnalysis).filter_by(
            tenant_id=task.tenant_id, asset_id=mirror.id,
            prompt_version=version, status="completed").first()
        if not copied:
            db.query(VideoAssetAnalysis).filter_by(
                tenant_id=task.tenant_id, asset_id=mirror.id,
                status="completed").update({"status": "superseded"})
            copied = VideoAssetAnalysis(
                tenant_id=task.tenant_id, asset_id=mirror.id,
                schema_version=analysis.schema_version, model=analysis.model,
                prompt_version=version, status="completed", summary=analysis.summary,
                analysis_json=analysis.analysis_json, input_video_seconds=analysis.input_video_seconds,
                request_count=analysis.request_count, retry_count=analysis.retry_count,
                completed_at=analysis.completed_at,
            )
            db.add(copied)
            db.flush()
            highlights = db.query(EcommerceVideoHighlight).filter_by(
                tenant_id=task.tenant_id, asset_id=row.id, analysis_id=analysis.id).all()
            for item in highlights:
                db.add(VideoHighlight(
                    tenant_id=task.tenant_id, asset_id=mirror.id, analysis_id=copied.id,
                    highlight_id=f"ecom-{row.id}-{analysis.id}-{item.id}",
                    start_seconds=item.start_seconds, end_seconds=item.end_seconds,
                    description=item.description, reason=item.reason,
                    recommended_roles=item.recommended_roles, content_role=item.content_role,
                    track_role=item.track_role, speech_present=item.speech_present,
                    lip_visible=item.lip_visible, speech_text=item.speech_text,
                    speaker_id=item.speaker_id, audio_strategy=item.audio_strategy,
                    shot_type=item.shot_type, selling_points=item.selling_points, score=item.score,
                ))
        mirror.status = "analyzed"
        mirrors.append(mirror)
    db.flush()
    return mirrors
