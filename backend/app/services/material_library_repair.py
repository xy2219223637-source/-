"""Explicit compatibility bridge from the standalone library to V4 repair media."""
from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    MaterialLibraryAsset, MaterialLibraryProduct, MaterialLibraryRepairAssetLink,
    MaterialLibraryRepairProductLink, Product, VideoAsset,
)
from app.services.video_analysis_queue import enqueue_video_analysis

logger = logging.getLogger(__name__)
BRIDGE_CATEGORY = "__material_library_repair__"


def ensure_repair_product(db: Session, *, tenant_id: str, library_product_id: int) -> Product:
    library = db.query(MaterialLibraryProduct).filter_by(id=library_product_id, tenant_id=tenant_id).first()
    if not library:
        raise HTTPException(404, "素材库商品不存在或无权访问")
    link = db.query(MaterialLibraryRepairProductLink).filter_by(
        tenant_id=tenant_id, library_product_id=library.id).first()
    product = db.get(Product, link.repair_product_id) if link else None
    if product and product.tenant_id != tenant_id:
        raise HTTPException(409, "商品关联数据无效")
    if product is None:
        product = Product(tenant_id=tenant_id, name=f"素材库商品 #{library.id}",
                          category=BRIDGE_CATEGORY, description=library.introduction,
                          image_urls=[library.image_url], learned_profile_enabled=False)
        db.add(product)
        db.flush()
        db.add(MaterialLibraryRepairProductLink(
            tenant_id=tenant_id, library_product_id=library.id, repair_product_id=product.id))
    else:
        product.description = library.introduction
        product.image_urls = [library.image_url]
    db.commit()
    db.refresh(product)
    return product


def library_product_for_repair(db: Session, *, tenant_id: str, repair_product_id: int) -> MaterialLibraryProduct | None:
    link = db.query(MaterialLibraryRepairProductLink).filter_by(
        tenant_id=tenant_id, repair_product_id=repair_product_id).first()
    if not link:
        return None
    return db.query(MaterialLibraryProduct).filter_by(
        id=link.library_product_id, tenant_id=tenant_id).first()


def sync_repair_videos(db: Session, *, tenant_id: str, repair_product_id: int) -> list[VideoAsset]:
    library = library_product_for_repair(db, tenant_id=tenant_id, repair_product_id=repair_product_id)
    if not library:
        return []
    rows = db.query(MaterialLibraryAsset).filter_by(
        tenant_id=tenant_id, product_id=library.id, kind="video", removed_at=None).order_by(
            MaterialLibraryAsset.id).all()
    result: list[VideoAsset] = []
    queued = False
    for row in rows:
        link = db.query(MaterialLibraryRepairAssetLink).filter_by(
            tenant_id=tenant_id, library_asset_id=row.id).first()
        if link:
            mirror = db.query(VideoAsset).filter_by(
                id=link.video_asset_id, tenant_id=tenant_id,
                product_id=repair_product_id).first()
            if mirror and not mirror.excluded and mirror.content_hash == row.content_hash:
                result.append(mirror)
            continue
        mirror = VideoAsset(
            tenant_id=tenant_id, product_id=repair_product_id, project_id=None,
            asset_id=f"asset-{uuid.uuid4().hex[:12]}", filename=row.filename,
            source=f"material_library_asset:{row.id}", library_type="repair_only",
            asset_kind="material", reusable_status="review_required",
            object_url=row.object_url, storage_provider=row.storage_provider,
            content_hash=row.content_hash, duration_seconds=0,
            width=0, height=0, fps=0,
            has_audio=False, status="validating", excluded=False,
        )
        db.add(mirror)
        db.flush()
        db.add(MaterialLibraryRepairAssetLink(
            tenant_id=tenant_id, library_asset_id=row.id, video_asset_id=mirror.id))
        result.append(mirror)
        queued = True
    db.commit()
    if queued:
        enqueue_video_analysis()
    return result
