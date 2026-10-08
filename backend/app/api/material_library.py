"""Standalone, user-managed product material library."""
from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import AuthContext
from app.config import get_settings
from app.database import get_db
from app.models import (MaterialLibraryAsset, MaterialLibraryProduct,
                        MaterialLibraryRepairAssetLink, VideoAsset)
from app.permissions import require_permission
from app.services.storage import get_storage, get_storage_by_name

router = APIRouter(prefix="/material-library", tags=["material-library"])
auth_required = Depends(require_permission("material_library.access"))
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}
logger = logging.getLogger(__name__)


def _kind(filename: str, header: bytes, *, image_only: bool = False) -> str:
    suffix = Path(filename).suffix.lower()
    image = (header.startswith(b"\xff\xd8\xff") if suffix in {".jpg", ".jpeg"} else
             header.startswith(b"\x89PNG\r\n\x1a\n") if suffix == ".png" else
             header[:4] == b"RIFF" and header[8:12] == b"WEBP" if suffix == ".webp" else
             header.startswith((b"GIF87a", b"GIF89a")) if suffix == ".gif" else False)
    if suffix in IMAGE_EXTENSIONS and image:
        return "image"
    if not image_only and suffix in VIDEO_EXTENSIONS:
        if suffix in {".mp4", ".mov"} and header[4:8] == b"ftyp":
            return "video"
        if suffix in {".webm", ".mkv"} and header.startswith(b"\x1a\x45\xdf\xa3"):
            return "video"
    raise HTTPException(415, "文件格式无效；仅支持 JPG、PNG、WebP、GIF、MP4、MOV、WebM 和 MKV")


async def _save_upload(upload: UploadFile, subdir: str, *, image_only: bool = False,
                       duplicate_lookup: Callable[[str], MaterialLibraryAsset | None] | None = None
                       ) -> tuple[str, str, int, str, str, MaterialLibraryAsset | None]:
    filename = Path(upload.filename or "").name
    if not filename or filename in {".", ".."}:
        raise HTTPException(422, "文件名不能为空")
    limit_mb = get_settings().max_upload_mb if image_only else get_settings().director_video_max_upload_mb
    limit = limit_mb * 1024 * 1024
    size = 0
    digest = hashlib.sha256()
    header = b""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "upload"
        with path.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"文件不能超过 {limit_mb}MB")
                if len(header) < 16:
                    header = (header + chunk)[:16]
                digest.update(chunk)
                output.write(chunk)
        if not size:
            raise HTTPException(422, "文件不能为空")
        kind = _kind(filename, header, image_only=image_only)
        content_hash = digest.hexdigest()
        existing = duplicate_lookup(content_hash) if duplicate_lookup else None
        if existing:
            return existing.object_url, existing.kind, existing.size_bytes, content_hash, existing.storage_provider, existing
        storage = get_storage()
        started = time.monotonic()
        logger.info("Material-library storage upload started: kind=%s bytes=%s", kind, size)
        url = await storage.upload_path(path, filename, subdir)
        logger.info("Material-library storage upload finished: kind=%s bytes=%s elapsed_seconds=%.1f",
                    kind, size, time.monotonic() - started)
    return url, kind, size, content_hash, storage.name, None


def _product(db: Session, tenant_id: str, product_id: int) -> MaterialLibraryProduct:
    row = db.query(MaterialLibraryProduct).filter_by(id=product_id, tenant_id=tenant_id).first()
    if row is None:
        raise HTTPException(404, "素材库商品不存在")
    return row


def _product_out(row: MaterialLibraryProduct) -> dict:
    return {"id": row.id, "image_url": row.image_url, "introduction": row.introduction,
            "created_at": row.created_at, "updated_at": row.updated_at}


def _asset_out(row: MaterialLibraryAsset) -> dict:
    return {"id": row.id, "product_id": row.product_id, "kind": row.kind,
            "filename": row.filename, "object_url": row.object_url,
            "size_bytes": row.size_bytes, "created_at": row.created_at}


@router.get("/products")
def list_products(db: Session = Depends(get_db), auth: AuthContext = auth_required):
    rows = db.query(MaterialLibraryProduct).filter_by(tenant_id=auth.tenant_id).order_by(
        MaterialLibraryProduct.created_at.desc(), MaterialLibraryProduct.id.desc()).all()
    return [_product_out(row) for row in rows]


@router.post("/products", status_code=201)
async def create_product(introduction: str = Form(...), image: UploadFile = File(...),
                         db: Session = Depends(get_db), auth: AuthContext = auth_required):
    introduction = introduction.strip()
    if not introduction or len(introduction) > 10000:
        raise HTTPException(422, "产品介绍需要填写，且不能超过 10000 字")
    url, _, _, _, provider, _ = await _save_upload(image, f"material-library/{auth.tenant_id}/products", image_only=True)
    row = MaterialLibraryProduct(tenant_id=auth.tenant_id, image_url=url, introduction=introduction)
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except Exception:
        db.rollback()
        await get_storage_by_name(provider).delete(url)
        raise
    return _product_out(row)


@router.get("/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db), auth: AuthContext = auth_required):
    return _product_out(_product(db, auth.tenant_id, product_id))


@router.patch("/products/{product_id}")
async def update_product(product_id: int, introduction: str = Form(...), image: UploadFile | None = File(None),
                         db: Session = Depends(get_db), auth: AuthContext = auth_required):
    row = _product(db, auth.tenant_id, product_id)
    introduction = introduction.strip()
    if not introduction or len(introduction) > 10000:
        raise HTTPException(422, "产品介绍需要填写，且不能超过 10000 字")
    old_url = row.image_url
    new_url = ""
    provider = ""
    if image is not None:
        new_url, _, _, _, provider, _ = await _save_upload(image, f"material-library/{auth.tenant_id}/products", image_only=True)
    row.introduction = introduction
    if new_url:
        row.image_url = new_url
    try:
        db.commit()
        db.refresh(row)
    except Exception:
        db.rollback()
        if new_url:
            await get_storage_by_name(provider).delete(new_url)
        raise
    if new_url:
        try:
            await get_storage_by_name(provider).delete(old_url)
        except Exception:
            logger.warning("Unable to delete replaced material-library product image", exc_info=True)
    return _product_out(row)


@router.get("/products/{product_id}/assets")
def list_assets(product_id: int, db: Session = Depends(get_db), auth: AuthContext = auth_required):
    _product(db, auth.tenant_id, product_id)
    rows = db.query(MaterialLibraryAsset).filter_by(tenant_id=auth.tenant_id, product_id=product_id,
                                                    removed_at=None).order_by(MaterialLibraryAsset.id.desc()).all()
    return [_asset_out(row) for row in rows]


@router.post("/products/{product_id}/assets", status_code=201)
async def upload_asset(product_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                       auth: AuthContext = auth_required):
    _product(db, auth.tenant_id, product_id)
    filename = Path(file.filename or "").name
    url, kind, size, digest, provider, existing = await _save_upload(
        file, f"material-library/{auth.tenant_id}/{product_id}/assets",
        duplicate_lookup=lambda content_hash: db.query(MaterialLibraryAsset).filter_by(
            tenant_id=auth.tenant_id, product_id=product_id, content_hash=content_hash,
            removed_at=None).first())
    if existing:
        return _asset_out(existing)
    row = MaterialLibraryAsset(tenant_id=auth.tenant_id, product_id=product_id, kind=kind,
                               filename=filename, object_url=url, storage_provider=provider,
                               size_bytes=size, content_hash=digest, created_by=auth.user_id)
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except Exception:
        db.rollback()
        await get_storage_by_name(provider).delete(url)
        raise
    return _asset_out(row)


@router.delete("/products/{product_id}/assets/{asset_id}", status_code=204)
async def remove_asset(product_id: int, asset_id: int, db: Session = Depends(get_db),
                       auth: AuthContext = auth_required):
    _product(db, auth.tenant_id, product_id)
    row = db.query(MaterialLibraryAsset).filter_by(id=asset_id, tenant_id=auth.tenant_id,
                                                    product_id=product_id, removed_at=None).first()
    if row is None:
        raise HTTPException(404, "素材不存在")
    row.removed_at = datetime.utcnow()
    linked = db.query(MaterialLibraryRepairAssetLink).filter_by(
        tenant_id=auth.tenant_id, library_asset_id=row.id).first()
    if linked:
        mirror = db.query(VideoAsset).filter_by(
            id=linked.video_asset_id, tenant_id=auth.tenant_id).first()
        if mirror:
            mirror.excluded = True
    db.commit()
    if linked:
        # Keep the underlying object for already frozen repair previews/plans.
        return
    try:
        await get_storage_by_name(row.storage_provider).delete(row.object_url)
    except Exception:
        logger.warning("Unable to delete removed material-library asset %s", row.id, exc_info=True)
