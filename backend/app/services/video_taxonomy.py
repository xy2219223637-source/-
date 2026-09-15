from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import VideoAsset, VideoAssetTag, VideoCategory, VideoTag, VideoTagGroup, VideoTaxonomyState


OBJECT_MATERIAL = "material"
OBJECT_FINAL = "final_output"
OBJECT_TYPES = {OBJECT_MATERIAL, OBJECT_FINAL}
APPLIES_TO_VALUES = {OBJECT_MATERIAL, OBJECT_FINAL, "both"}

DEFAULT_CATEGORIES = {
    OBJECT_MATERIAL: [
        ("people", "人物口播", [
            ("people.creator", "达人口播"),
            ("people.presenter", "真人讲解"),
            ("people.voiceover", "配音口播"),
        ]),
        ("product", "商品展示", [
            ("product.single", "单品展示"),
            ("product.bundle", "套装展示"),
            ("product.package", "包装细节"),
            ("product.texture", "质地特写"),
        ]),
        ("usage", "使用过程", [
            ("usage.unboxing", "开箱"),
            ("usage.face", "上脸过程"),
            ("usage.demo", "操作演示"),
        ]),
        ("proof", "效果证明", [
            ("proof.before_after", "前后对比"),
            ("proof.test", "产品实测"),
            ("proof.feedback", "用户反馈"),
        ]),
        ("scene", "场景氛围", [
            ("scene.people", "人物场景"),
            ("scene.product", "产品空镜"),
            ("scene.environment", "环境空镜"),
        ]),
        ("package", "转场包装", [
            ("package.transition", "转场"),
            ("package.intro", "片头"),
            ("package.outro", "片尾"),
            ("package.brand", "品牌露出"),
        ]),
    ],
    OBJECT_FINAL: [
        ("conversion", "转化型", [
            ("conversion.selling_point", "单品卖点"),
            ("conversion.pain_point", "痛点解决"),
            ("conversion.promotion", "活动促销"),
        ]),
        ("seeding", "种草型", [
            ("seeding.experience", "体验分享"),
            ("seeding.creator", "达人推荐"),
            ("seeding.scene", "场景种草"),
        ]),
        ("brand", "品牌型", [
            ("brand.story", "品牌故事"),
            ("brand.technology", "成分科技"),
            ("brand.endorsement", "权威背书"),
        ]),
        ("live", "直播引流型", [
            ("live.preview", "直播预告"),
            ("live.benefit", "福利引导"),
            ("live.clip", "直播切片"),
        ]),
    ],
}

DEFAULT_TAG_GROUPS = [
    ("marketing", "营销信息", ["卖点", "痛点", "优惠", "CTA", "创新点"]),
    ("people", "人物角色", ["达人", "模特", "素人", "主播"]),
    ("scene", "内容场景", ["室内", "户外", "直播间", "通勤", "居家"]),
    ("shot", "镜头语言", ["近景", "特写", "对比", "空镜", "跟拍"]),
    ("script", "脚本特征", ["测评", "教程", "剧情", "清单", "口播"]),
]


def object_type_for_asset(asset: VideoAsset) -> str:
    return OBJECT_FINAL if asset.asset_kind == OBJECT_FINAL else OBJECT_MATERIAL


def applies_to_object(applies_to: str, object_type: str) -> bool:
    return applies_to == "both" or applies_to == object_type


def ensure_default_taxonomy(db: Session, tenant_id: str) -> None:
    if db.query(VideoTaxonomyState).filter(VideoTaxonomyState.tenant_id == tenant_id).first():
        return
    existing_categories = {
        (row.object_type, row.code): row
        for row in db.query(VideoCategory).filter(VideoCategory.tenant_id == tenant_id).all()
    }
    for object_type, parents in DEFAULT_CATEGORIES.items():
        for parent_index, (parent_code, parent_name, children) in enumerate(parents):
            parent = existing_categories.get((object_type, parent_code))
            if parent is None:
                parent = VideoCategory(
                    tenant_id=tenant_id,
                    object_type=object_type,
                    name=parent_name,
                    code=parent_code,
                    sort_order=parent_index,
                )
                db.add(parent)
                db.flush()
                existing_categories[(object_type, parent_code)] = parent
            for child_index, (child_code, child_name) in enumerate(children):
                if (object_type, child_code) in existing_categories:
                    continue
                child = VideoCategory(
                    tenant_id=tenant_id,
                    object_type=object_type,
                    parent_id=parent.id,
                    name=child_name,
                    code=child_code,
                    sort_order=child_index,
                )
                db.add(child)
                existing_categories[(object_type, child_code)] = child

    existing_groups = {
        row.code: row
        for row in db.query(VideoTagGroup).filter(VideoTagGroup.tenant_id == tenant_id).all()
    }
    existing_tags = {
        row.code
        for row in db.query(VideoTag).filter(
            VideoTag.tenant_id == tenant_id,
            VideoTag.scope == "public",
            VideoTag.owner_id == "",
        ).all()
    }
    for group_index, (group_code, group_name, tag_names) in enumerate(DEFAULT_TAG_GROUPS):
        group = existing_groups.get(group_code)
        if group is None:
            group = VideoTagGroup(
                tenant_id=tenant_id,
                name=group_name,
                code=group_code,
                applies_to="both",
                inherit_to_final=True,
                sort_order=group_index,
            )
            db.add(group)
            db.flush()
            existing_groups[group_code] = group
        for tag_index, tag_name in enumerate(tag_names):
            tag_code = f"{group_code}.{tag_index + 1}"
            if tag_code in existing_tags:
                continue
            db.add(VideoTag(
                tenant_id=tenant_id,
                group_id=group.id,
                name=tag_name,
                code=tag_code,
                scope="public",
                owner_id="",
                applies_to="both",
                sort_order=tag_index,
            ))
            existing_tags.add(tag_code)
    db.add(VideoTaxonomyState(tenant_id=tenant_id))


def load_asset_taxonomy(
    db: Session,
    assets: Iterable[VideoAsset],
    tenant_id: str,
    user_id: str,
) -> tuple[dict[int, dict | None], dict[int, list[dict]]]:
    asset_rows = list(assets)
    if not asset_rows:
        return {}, {}
    category_ids = {asset.category_id for asset in asset_rows if asset.category_id is not None}
    categories = {
        row.id: row
        for row in db.query(VideoCategory).filter(
            VideoCategory.tenant_id == tenant_id,
            VideoCategory.id.in_(category_ids),
        ).all()
    } if category_ids else {}
    parent_ids = {row.parent_id for row in categories.values() if row.parent_id is not None}
    parents = {
        row.id: row
        for row in db.query(VideoCategory).filter(
            VideoCategory.tenant_id == tenant_id,
            VideoCategory.id.in_(parent_ids),
        ).all()
    } if parent_ids else {}
    category_by_asset: dict[int, dict | None] = {}
    for asset in asset_rows:
        category = categories.get(asset.category_id)
        parent = parents.get(category.parent_id) if category else None
        category_by_asset[asset.id] = None if category is None else {
            "id": category.id,
            "name": category.name,
            "code": category.code,
            "status": category.status,
            "parent_id": category.parent_id,
            "parent_name": parent.name if parent else None,
            "path": [value for value in [parent.name if parent else None, category.name] if value],
        }

    asset_ids = [asset.id for asset in asset_rows]
    rows = db.query(VideoAssetTag, VideoTag, VideoTagGroup).join(
        VideoTag, VideoTag.id == VideoAssetTag.tag_id
    ).outerjoin(
        VideoTagGroup, VideoTagGroup.id == VideoTag.group_id
    ).filter(
        VideoAssetTag.tenant_id == tenant_id,
        VideoAssetTag.asset_id.in_(asset_ids),
    ).order_by(VideoTag.sort_order, VideoTag.id).all()
    tags_by_asset: dict[int, list[dict]] = defaultdict(list)
    for assignment, tag, group in rows:
        if tag.scope == "personal" and tag.owner_id != user_id:
            continue
        tags_by_asset[assignment.asset_id].append({
            "id": tag.id,
            "name": tag.name,
            "scope": tag.scope,
            "status": tag.status,
            "group_id": tag.group_id,
            "group_name": group.name if group else None,
            "applies_to": tag.applies_to,
            "source": assignment.source,
            "confidence": assignment.confidence,
            "confirmed": assignment.confirmed,
        })
    return category_by_asset, dict(tags_by_asset)


def inherit_public_tags_to_final(
    db: Session,
    final_asset: VideoAsset,
    source_assets: Iterable[VideoAsset],
    actor_id: str,
) -> int:
    source_ids = [asset.id for asset in source_assets]
    if not source_ids:
        return 0
    db.flush()
    rows = db.query(VideoAssetTag, VideoTag, VideoTagGroup).join(
        VideoTag, VideoTag.id == VideoAssetTag.tag_id
    ).outerjoin(
        VideoTagGroup, VideoTagGroup.id == VideoTag.group_id
    ).filter(
        VideoAssetTag.tenant_id == final_asset.tenant_id,
        VideoAssetTag.asset_id.in_(source_ids),
        VideoAssetTag.confirmed.is_(True),
        VideoTag.scope == "public",
        VideoTag.status == "active",
    ).all()
    inherited = 0
    seen: set[int] = set()
    for _, tag, group in rows:
        if tag.id in seen or not applies_to_object(tag.applies_to, OBJECT_FINAL):
            continue
        if group is not None and not group.inherit_to_final:
            continue
        seen.add(tag.id)
        db.add(VideoAssetTag(
            tenant_id=final_asset.tenant_id,
            asset_id=final_asset.id,
            tag_id=tag.id,
            source="inherited",
            confidence=1.0,
            confirmed=True,
            created_by=actor_id,
        ))
        inherited += 1
    return inherited
