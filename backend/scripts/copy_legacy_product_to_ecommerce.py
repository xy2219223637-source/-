"""One-time, auditable copy of a legacy product and its videos into ecommerce tables.

Run from backend with --source-id and --apply. The source rows and OSS objects are
never modified. A JSON snapshot of the ecommerce categories is written first.
"""
from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.database import SessionLocal
from app.models import (
    EcommerceProduct, EcommerceProductAsset, EcommerceProductFact,
    EcommerceVideoAsset, EcommerceVideoCategory, EcommerceVideoTaxonomyState,
    Product, ProductAsset, ProductFact, VideoAsset,
)
from app.services.storage import _provider_object_url, get_storage_by_name, object_ref


PRODUCT_FIELDS = (
    "name", "category", "price", "description", "target_users", "brand_name",
    "ingredients", "usage_method", "specifications", "learned_profile_enabled",
)


def _snapshot(row, fields: tuple[str, ...]) -> dict:
    return {field: getattr(row, field) for field in fields}


def _json_row(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-id", type=int, required=True)
    parser.add_argument("--expected-videos", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        source = db.get(Product, args.source_id)
        if source is None:
            raise SystemExit(f"旧商品 {args.source_id} 不存在")
        tenant_id = source.tenant_id
        existing = db.query(EcommerceProduct).filter_by(
            tenant_id=tenant_id, name=source.name, brand_name=source.brand_name,
        ).first()
        if existing is not None:
            raise SystemExit(f"新模块已有同名同品牌商品 {existing.id}，拒绝重复复制")
        assets = db.query(ProductAsset).filter_by(product_id=source.id, tenant_id=tenant_id).all()
        facts = db.query(ProductFact).filter_by(product_id=source.id, tenant_id=tenant_id).all()
        videos = db.query(VideoAsset).filter_by(product_id=source.id, tenant_id=tenant_id).order_by(VideoAsset.id).all()
        if len(videos) != args.expected_videos:
            raise SystemExit(f"旧视频数量变为 {len(videos)}，与预期 {args.expected_videos} 不符")
        current_categories = db.query(EcommerceVideoCategory).filter_by(tenant_id=tenant_id).all()
        current_videos = db.query(EcommerceVideoAsset).filter_by(tenant_id=tenant_id).all()
        state = db.get(EcommerceVideoTaxonomyState, tenant_id)
        source_data = _snapshot(source, PRODUCT_FIELDS)
        source_data["image_urls"] = list(source.image_urls or [])
        source_data["detail_image_urls"] = list(source.detail_image_urls or [])
        asset_data = [_json_row(row) for row in assets]
        fact_data = [_json_row(row) for row in facts]
        video_data = [_json_row(row) for row in videos]
        backup = {
            "tenant_id": tenant_id,
            "source_product_id": source.id,
            "source_product_name": source.name,
            "source_video_count": len(videos),
            "ecommerce_categories": [_json_row(row) for row in current_categories],
            "ecommerce_video_category_assignments": [
                {"asset_id": row.id, "category_id": row.category_id}
                for row in current_videos
            ],
            "taxonomy_state_initialized": state is not None,
        }

    summary = {
        "source_product_id": args.source_id,
        "source_product_name": source_data["name"],
        "source_assets": len(asset_data),
        "source_facts": len(fact_data),
        "source_videos": len(video_data),
        "video_kinds": dict(Counter(row["asset_kind"] for row in video_data)),
        "categories_to_clear": len(backup["ecommerce_categories"]),
        "existing_video_assignments_to_clear": sum(
            row["category_id"] is not None for row in backup["ecommerce_video_category_assignments"]
        ),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if not args.apply:
        return

    backup_dir = Path(__file__).resolve().parents[1] / "migration_backups"
    backup_dir.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup_path = backup_dir / f"legacy-product-{args.source_id}-{run_id}.json"
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    print(f"分类备份: {backup_path}", flush=True)

    storage = get_storage_by_name("aliyun_oss")
    provider = storage.provider
    bucket = provider.bucket
    from app.config import get_settings
    bucket_name = get_settings().aliyun_oss_bucket_name
    urls = list(dict.fromkeys([
        *source_data["image_urls"], *source_data["detail_image_urls"],
        *(row["file_url"] for row in asset_data),
        *(row["object_url"] for row in video_data),
    ]))
    copied_urls: dict[str, str] = {}
    new_keys: list[str] = []
    committed = False
    try:
        for index, url in enumerate(urls, 1):
            storage_name, old_key = object_ref(url)
            if storage_name != "aliyun_oss" or old_key is None:
                raise ValueError(f"仅支持同一 OSS 桶内复制: {url}")
            kind = "video" if any(row["object_url"] == url for row in video_data) else "image"
            filename = Path(old_key).name
            new_key = provider._new_key(filename, f"ecommerce-imports/legacy-product-{args.source_id}-{run_id}/{kind}")
            old_size = int(bucket.head_object(old_key).content_length)
            bucket.copy_object(bucket_name, old_key, new_key)
            new_keys.append(new_key)
            new_size = int(bucket.head_object(new_key).content_length)
            if new_size != old_size:
                raise IOError(f"OSS 复制大小不一致: {old_key}")
            copied_urls[url] = _provider_object_url("aliyun_oss", new_key)
            if index % 10 == 0 or index == len(urls):
                print(f"OSS 复制 {index}/{len(urls)}", flush=True)

        with SessionLocal() as db:
            if db.query(EcommerceProduct).filter_by(
                tenant_id=tenant_id, name=source_data["name"], brand_name=source_data["brand_name"],
            ).first():
                raise ValueError("复制期间新模块出现同名商品，已中止")
            product = EcommerceProduct(
                tenant_id=tenant_id,
                **{field: source_data[field] for field in PRODUCT_FIELDS},
                image_urls=[copied_urls[url] for url in source_data["image_urls"]],
                detail_image_urls=[copied_urls[url] for url in source_data["detail_image_urls"]],
            )
            db.add(product)
            db.flush()
            for row in asset_data:
                db.add(EcommerceProductAsset(
                    tenant_id=tenant_id, product_id=product.id,
                    **{field: row[field] for field in (
                        "name", "asset_type", "mime_type", "description", "material_role",
                        "priority", "locked", "excluded", "benchmark_role", "protection",
                    )},
                    file_url=copied_urls[row["file_url"]], tags=[], created_at=row["created_at"],
                ))
            for row in fact_data:
                db.add(EcommerceProductFact(
                    tenant_id=tenant_id, product_id=product.id,
                    **{field: row[field] for field in (
                        "fact_key", "label", "value", "status", "confidence", "conflict_values",
                        "confirmed_by", "confirmed_at", "created_at",
                    )},
                    source_type="legacy_import", source_ref=f"legacy_product:{args.source_id}",
                ))
            for row in video_data:
                db.add(EcommerceVideoAsset(
                    tenant_id=tenant_id, product_id=product.id, category_id=None,
                    asset_id=f"asset-{uuid.uuid4().hex[:12]}", filename=row["filename"],
                    source=f"legacy_video:{row['id']}", library_type="finished",
                    asset_kind=row["asset_kind"],
                    reusable_status="blocked" if row["asset_kind"] == "final_output" else "review_required",
                    burned_subtitles=row["burned_subtitles"], has_background_music=False,
                    object_url=copied_urls[row["object_url"]], storage_provider="aliyun_oss",
                    content_hash=row["content_hash"], duration_seconds=row["duration_seconds"],
                    width=row["width"], height=row["height"], fps=row["fps"], has_audio=row["has_audio"],
                    ai_tags=[], manual_tags=[], removed_ai_tags=[], excluded=False,
                    status="uploaded", created_at=row["created_at"],
                ))
            db.query(EcommerceVideoAsset).filter_by(tenant_id=tenant_id).update(
                {EcommerceVideoAsset.category_id: None}, synchronize_session=False,
            )
            db.flush()
            db.query(EcommerceVideoCategory).filter(
                EcommerceVideoCategory.tenant_id == tenant_id,
                EcommerceVideoCategory.parent_id.is_not(None),
            ).delete(synchronize_session=False)
            db.query(EcommerceVideoCategory).filter_by(tenant_id=tenant_id).delete(synchronize_session=False)
            if db.get(EcommerceVideoTaxonomyState, tenant_id) is None:
                db.add(EcommerceVideoTaxonomyState(tenant_id=tenant_id))
            db.commit()
            committed = True
            print(f"新商品 ID: {product.id}; 新视频: {len(video_data)}; 分类已清空", flush=True)
    except BaseException:
        if not committed:
            for key in reversed(new_keys):
                try:
                    bucket.delete_object(key)
                except Exception as cleanup_error:
                    print(f"无法清理已复制的 OSS 对象 {key}: {cleanup_error}", flush=True)
        raise


if __name__ == "__main__":
    main()
