"""Independent ecommerce product, asset, and fact management."""

from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.auth import AuthContext, current_auth
from app.database import get_db
from app.models import EcommerceProduct as Product, EcommerceProductAsset as ProductAsset, EcommerceProductFact as ProductFact
from app.schemas import ProductAssetOut, ProductAssetUpdate, ProductCreate, ProductOut, ProductUpdate
from app.services.storage import get_storage
from app.services.image_postprocess import local_path, product_foreground_mask
from app.services.llm import get_llm
from app.services.quality_pipeline import inspect_source
from PIL import Image

router = APIRouter(prefix="/ecommerce-products", tags=["ecommerce-products"])


class FactPayload(BaseModel):
    fact_key: str
    label: str
    value: str
    source_type: str = "manual"
    source_ref: str = ""


DEFAULT_FACTS = [("name", "商品名称"), ("brand_name", "品牌"), ("category", "品类"),
                 ("description", "核心卖点"), ("target_users", "目标用户"),
                 ("ingredients", "成分"), ("usage_method", "使用方法"),
                 ("specifications", "规格/套装组成")]


def _product_or_404(product_id: int, db: Session, tenant_id: str) -> Product:
    product = db.query(Product).filter_by(id=product_id, tenant_id=tenant_id).first()
    if not product:
        raise HTTPException(404, "商品不存在")
    return product


def _sync_primary_image(product: Product, asset: ProductAsset) -> None:
    """Keep the product cover synchronized with asset metadata."""
    urls = [url for url in (product.image_urls or []) if url != asset.file_url]
    is_image = asset.asset_type == "product_image" and asset.mime_type.startswith("image/")
    is_primary = asset.material_role == "product" or asset.locked or asset.benchmark_role == "product_front"
    if is_image and not asset.excluded and is_primary:
        urls.insert(0, asset.file_url)
    product.image_urls = urls


def _to_out(product: Product) -> ProductOut:
    return ProductOut(
        id=product.id,
        name=product.name,
        category=product.category,
        price=product.price,
        description=product.description,
        target_users=product.target_users,
        brand_name=product.brand_name,
        ingredients=product.ingredients,
        usage_method=product.usage_method,
        specifications=product.specifications,
        image_urls=product.image_urls or [],
        detail_image_urls=product.detail_image_urls or [],
        learned_profile_enabled=product.learned_profile_enabled,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


@router.get("", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)) -> list[ProductOut]:
    products = db.query(Product).filter(
        Product.tenant_id == auth.tenant_id,
    ).order_by(Product.created_at.desc()).all()
    return [_to_out(p) for p in products]


@router.get("/{product_id}/facts")
def list_facts(product_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    product = _product_or_404(product_id, db, auth.tenant_id)
    existing = {row.fact_key: row for row in db.query(ProductFact).filter_by(product_id=product_id, tenant_id=auth.tenant_id).all()}
    for key, label in DEFAULT_FACTS:
        value = str(getattr(product, key) or "")
        if key not in existing:
            row = ProductFact(tenant_id=auth.tenant_id, product_id=product_id, fact_key=key, label=label,
                              value=value, source_type="product_record", source_ref=f"ecommerce_product:{product_id}",
                              status="confirmed" if value else "pending")
            db.add(row)
            existing[key] = row
        elif value and existing[key].value and value != existing[key].value and value not in existing[key].conflict_values:
            existing[key].conflict_values = [*existing[key].conflict_values, value]
            existing[key].status = "conflict"
    db.commit()
    return sorted(existing.values(), key=lambda row: row.id)


@router.put("/{product_id}/facts")
def upsert_fact(product_id: int, payload: FactPayload, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _product_or_404(product_id, db, auth.tenant_id)
    row = db.query(ProductFact).filter_by(product_id=product_id, tenant_id=auth.tenant_id, fact_key=payload.fact_key).first()
    if not row:
        row = ProductFact(tenant_id=auth.tenant_id, product_id=product_id, fact_key=payload.fact_key, label=payload.label)
        db.add(row)
    if row.value and row.value != payload.value:
        row.conflict_values = list(dict.fromkeys([*row.conflict_values, row.value, payload.value]))
        row.status = "conflict"
    row.value = payload.value
    row.label = payload.label
    row.source_type = payload.source_type
    row.source_ref = payload.source_ref
    row.confidence = 1.0
    db.commit()
    db.refresh(row)
    return row


@router.post("/{product_id}/facts/{fact_id}/confirm")
def confirm_fact(product_id: int, fact_id: int, value: str | None = None, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(ProductFact).filter_by(id=fact_id, product_id=product_id, tenant_id=auth.tenant_id).first()
    if not row:
        raise HTTPException(404, "事实不存在")
    if value is not None:
        row.value = value
    row.status = "confirmed"
    row.conflict_values = []
    row.confirmed_by = auth.user_id
    row.confirmed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


@router.get("/{product_id}/readiness")
def readiness(product_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    product = _product_or_404(product_id, db, auth.tenant_id)
    assets = db.query(ProductAsset).filter_by(product_id=product_id, tenant_id=auth.tenant_id, excluded=False).all()
    fields = [
        ("product_images", "商品原图", bool(product.image_urls) or any(a.material_role in {"product", "package"} for a in assets), True),
        ("ingredients", "成分资料", bool(product.ingredients.strip()), True),
        ("specifications", "规格与套装组成", bool(product.specifications.strip()), True),
        ("usage_method", "使用方法", bool(product.usage_method.strip()), False),
        ("brand", "品牌名称", bool(product.brand_name.strip()), True),
        ("benchmarks", "质检基准图", any(a.benchmark_role != "none" for a in assets), False),
    ]
    items = [{"key": key, "label": label, "complete": complete, "required": required} for key, label, complete, required in fields]
    required = [item for item in items if item["required"]]
    score = round(sum(item["complete"] for item in required) / len(required) * 100)
    return {"product_id": product_id, "score": score, "status": "ready" if score == 100 else "blocked",
            "items": items, "missing_required": [item["label"] for item in required if not item["complete"]]}


@router.get("/{product_id}/image-admission")
def image_admission(product_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    product = _product_or_404(product_id, db, auth.tenant_id)
    assets = db.query(ProductAsset).filter_by(product_id=product_id, tenant_id=auth.tenant_id).all()
    sources = [{"url": url, "label": f"商品原图 {index + 1}"} for index, url in enumerate(product.image_urls or [])]
    sources += [{"url": asset.file_url, "label": asset.name, "asset_id": asset.id} for asset in assets if asset.mime_type.startswith("image/") and not asset.excluded]
    results = [{**source, "inspection": inspect_source(source["url"])} for source in sources]
    return {"status": "passed" if results and all(result["inspection"]["status"] == "passed" for result in results) else "review",
            "images": results, "missing_roles": [role for role in ["product_front", "package", "set_composition"] if not any(asset.benchmark_role == role for asset in assets)]}


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)) -> ProductOut:
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    return _to_out(product)


@router.post("", response_model=ProductOut)
def create_product(payload: ProductCreate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)) -> ProductOut:
    product = Product(
        tenant_id=auth.tenant_id, name=payload.name,
        category=payload.category,
        price=payload.price,
        description=payload.description,
        target_users=payload.target_users,
        brand_name=payload.brand_name,
        ingredients=payload.ingredients,
        usage_method=payload.usage_method,
        specifications=payload.specifications,
        image_urls=payload.image_urls,
        detail_image_urls=payload.detail_image_urls,
        learned_profile_enabled=payload.learned_profile_enabled,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return _to_out(product)


@router.get("/{product_id}/assets", response_model=list[ProductAssetOut])
def list_product_assets(product_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)) -> list[ProductAssetOut]:
    if not db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first():
        raise HTTPException(status_code=404, detail="商品不存在")
    assets = (
        db.query(ProductAsset)
        .filter(ProductAsset.tenant_id == auth.tenant_id, ProductAsset.product_id == product_id)
        .order_by(ProductAsset.created_at.desc())
        .all()
    )
    return [ProductAssetOut.model_validate(asset) for asset in assets]


@router.post("/{product_id}/assets", response_model=list[ProductAssetOut])
async def upload_product_assets(
    product_id: int,
    asset_type: str = Form("product_image"),
    description: str = Form(""),
    tags: str = Form(""),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
) -> list[ProductAssetOut]:
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    settings = get_settings()
    created: list[ProductAsset] = []
    for upload in files:
        raw = await upload.read()
        if len(raw) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"文件不能超过 {settings.max_upload_mb}MB")
        subdir = "ecommerce/images" if (upload.content_type or "").startswith("image/") else "ecommerce/documents"
        file_url = await get_storage().upload(raw, upload.filename or "asset.bin", subdir)
        stored_name = Path(file_url).name
        asset = ProductAsset(
            tenant_id=auth.tenant_id, product_id=product_id,
            name=upload.filename or stored_name,
            asset_type=asset_type,
            file_url=file_url,
            mime_type=upload.content_type or mimetypes.guess_type(stored_name)[0] or "",
            description=description,
            tags=[item.strip() for item in tags.split(",") if item.strip()],
        )
        db.add(asset)
        created.append(asset)
    # Seed the product cover from the first uploaded product image.
    if asset_type == "product_image" and not (product.image_urls or []):
        first_image = next((asset for asset in created if asset.mime_type.startswith("image/")), None)
        if first_image:
            product.image_urls = [first_image.file_url]
    db.commit()
    for asset in created:
        db.refresh(asset)
    return [ProductAssetOut.model_validate(asset) for asset in created]


@router.delete("/{product_id}/assets/{asset_id}")
async def delete_product_asset(
    product_id: int, asset_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)
) -> dict:
    asset = db.query(ProductAsset).filter(
        ProductAsset.id == asset_id, ProductAsset.product_id == product_id, ProductAsset.tenant_id == auth.tenant_id
    ).first()
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    file_url = asset.file_url
    product = asset.product
    if product:
        product.image_urls = [url for url in (product.image_urls or []) if url != file_url]
        product.detail_image_urls = [url for url in (product.detail_image_urls or []) if url != file_url]
    db.delete(asset)
    db.commit()
    await get_storage().delete(file_url)
    return {"ok": True, "id": asset_id}


@router.put("/{product_id}/assets/{asset_id}", response_model=ProductAssetOut)
def update_product_asset(product_id: int, asset_id: int, payload: ProductAssetUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    asset = db.query(ProductAsset).filter(ProductAsset.id == asset_id, ProductAsset.product_id == product_id, ProductAsset.tenant_id == auth.tenant_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    for key, value in payload.model_dump().items():
        setattr(asset, key, value)
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first()
    if product:
        _sync_primary_image(product, asset)
    db.commit(); db.refresh(asset)
    return asset


@router.post("/{product_id}/assets/{asset_id}/auto-mask", response_model=ProductAssetOut)
def create_asset_mask(product_id:int,asset_id:int,db:Session=Depends(get_db),auth:AuthContext=Depends(current_auth)):
    asset=db.query(ProductAsset).filter_by(id=asset_id,product_id=product_id,tenant_id=auth.tenant_id).first()
    if not asset:raise HTTPException(404,"商品素材不存在")
    path=local_path(asset.file_url)
    if not path:raise HTTPException(409,"自动蒙版需要已保存的本地图片")
    image=Image.open(path).convert("RGB");mask=product_foreground_mask(image);name=f"mask-{asset.id}-{uuid.uuid4().hex}.png";output=BytesIO();mask.save(output,"PNG");url=get_storage().save_bytes(output.getvalue(),name,"ecommerce/masks")
    asset.protection={**(asset.protection or {}),"mask_url":url,"mask_source":"auto","position":{"x":.5,"y":.5,"scale":.72,"rotation":0},"preserve_shadow":False,"preserve_reflection":False};db.commit();db.refresh(asset);return asset


@router.post("/{product_id}/assets/{asset_id}/mask", response_model=ProductAssetOut)
async def upload_asset_mask(product_id:int,asset_id:int,file:UploadFile=File(...),db:Session=Depends(get_db),auth:AuthContext=Depends(current_auth)):
    asset=db.query(ProductAsset).filter_by(id=asset_id,product_id=product_id,tenant_id=auth.tenant_id).first()
    if not asset:raise HTTPException(404,"商品素材不存在")
    raw=await file.read()
    try:mask=Image.open(BytesIO(raw)).convert("L")
    except Exception as exc:raise HTTPException(415,"蒙版必须是有效图片") from exc
    name=f"mask-{asset.id}-{uuid.uuid4().hex}.png";output=BytesIO();mask.save(output,"PNG");url=get_storage().save_bytes(output.getvalue(),name,"ecommerce/masks")
    asset.protection={**(asset.protection or {}),"mask_url":url,"mask_source":"manual"};db.commit();db.refresh(asset);return asset


@router.post("/{product_id}/assets/{asset_id}/analyze-protection", response_model=ProductAssetOut)
async def analyze_asset_protection(product_id:int,asset_id:int,db:Session=Depends(get_db),auth:AuthContext=Depends(current_auth)):
    asset=db.query(ProductAsset).filter_by(id=asset_id,product_id=product_id,tenant_id=auth.tenant_id).first();settings=get_settings()
    if not asset:raise HTTPException(404,"商品素材不存在")
    if settings.llm_mock_mode or not settings.llm_api_key:raise HTTPException(503,"未配置真实视觉模型，无法提取Logo和包装文字保护区")
    prompt='找出图中所有Logo和包装文字区域。输出JSON：{"regions":[{"type":"logo|text","text":"可读文字或空字符串","x":0-1,"y":0-1,"width":0-1,"height":0-1,"confidence":0-1}]}。坐标为归一化左上角和宽高。'
    result=await get_llm().chat_vision(prompt,[asset.file_url],system_prompt='只输出JSON，不要猜测看不清的文字。',temperature=.1,max_tokens=1600,as_json=True);regions=result.get("regions") if isinstance(result,dict) else []
    asset.protection={**(asset.protection or {}),"protected_regions":regions if isinstance(regions,list) else [],"ocr_model":settings.llm_vision_model};db.commit();db.refresh(asset);return asset


@router.put("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int, payload: ProductUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)
) -> ProductOut:
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return _to_out(product)


@router.delete("/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)) -> dict:
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    db.delete(product)
    db.commit()
    return {"ok": True, "id": product_id}


@router.post("/{product_id}/images")
async def upload_product_images(
    product_id: int,
    image_type: str = Form("product"),  # product | detail
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
) -> ProductOut:
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == auth.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    settings = get_settings()
    saved_urls: list[str] = []
    for f in files:
        if not (f.content_type or "").startswith("image/"):
            raise HTTPException(status_code=415, detail="仅支持图片文件")
        content = await f.read()
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"图片不能超过 {settings.max_upload_mb}MB")
        saved_urls.append(await get_storage().upload(content, f.filename or "img.jpg", "ecommerce/images"))

    if image_type == "detail":
        product.detail_image_urls = (product.detail_image_urls or []) + saved_urls
    else:
        product.image_urls = (product.image_urls or []) + saved_urls

    db.commit()
    db.refresh(product)
    return _to_out(product)


@router.post("/upload-image")
async def upload_standalone_image(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(current_auth),
) -> dict:
    """Upload an image before product creation; returns URL."""
    settings = get_settings()
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="仅支持图片文件")
    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"图片不能超过 {settings.max_upload_mb}MB")
    return {"url": await get_storage().upload(content, file.filename or "img.jpg", "ecommerce/images")}
