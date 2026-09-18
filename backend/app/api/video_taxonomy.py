from __future__ import annotations

import re
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.database import get_db
from app.models import (
    VideoAsset,
    VideoAssetTag,
    VideoCategory,
    VideoFilterPreset,
    VideoTag,
    VideoTagGroup,
)
from app.services.video_taxonomy import (
    applies_to_object,
    ensure_default_taxonomy,
    object_type_for_asset,
)

router = APIRouter(prefix="/video-taxonomy", tags=["video-taxonomy"])
ADMIN_ROLES = {"owner", "admin"}
ObjectType = Literal["material", "final_output"]
AppliesTo = Literal["material", "final_output", "both"]


class CategoryCreate(BaseModel):
    object_type: ObjectType
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(default="", max_length=128)
    parent_id: Optional[int] = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    code: Optional[str] = Field(default=None, max_length=128)
    parent_id: Optional[int] = None
    sort_order: Optional[int] = None
    status: Optional[Literal["active", "inactive"]] = None


class TagGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(default="", max_length=128)
    applies_to: AppliesTo = "both"
    inherit_to_final: bool = True
    sort_order: int = 0


class TagGroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    code: Optional[str] = Field(default=None, max_length=128)
    applies_to: Optional[AppliesTo] = None
    inherit_to_final: Optional[bool] = None
    sort_order: Optional[int] = None
    status: Optional[Literal["active", "inactive"]] = None


class PublicTagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(default="", max_length=128)
    group_id: int
    applies_to: AppliesTo = "both"
    sort_order: int = 0


class PersonalTagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    applies_to: AppliesTo = "both"


class TagUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    code: Optional[str] = Field(default=None, max_length=128)
    group_id: Optional[int] = None
    applies_to: Optional[AppliesTo] = None
    sort_order: Optional[int] = None
    status: Optional[Literal["active", "inactive"]] = None


class MergePayload(BaseModel):
    target_id: int


class AssetTaxonomyUpdate(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=500)
    set_category: bool = False
    category_id: Optional[int] = None
    add_tag_ids: list[int] = Field(default_factory=list, max_length=200)
    remove_tag_ids: list[int] = Field(default_factory=list, max_length=200)


class PresetCreate(BaseModel):
    object_type: ObjectType
    name: str = Field(min_length=1, max_length=128)
    filters: dict = Field(default_factory=dict)


class PresetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    filters: Optional[dict] = None


def _require_admin(auth: AuthContext) -> None:
    if auth.role not in ADMIN_ROLES:
        raise HTTPException(403, "仅管理员可以维护分类和公共标签")


def _require_asset_access(auth: AuthContext, assets: list[VideoAsset]) -> None:
    if auth.role == "owner" or "*" in auth.permissions:
        return
    required = {
        "finished_assets.access" if object_type_for_asset(asset) == "material" else "final_videos.access"
        for asset in assets
    }
    missing = required - set(auth.permissions)
    if missing:
        raise HTTPException(403, "当前角色没有对应素材库的操作权限")


def _name(value: str) -> str:
    clean = value.strip()
    if not clean:
        raise HTTPException(422, "名称不能为空")
    return clean


def _code(value: str) -> str:
    clean = value.strip().lower()
    if not clean:
        return uuid.uuid4().hex[:12]
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", clean):
        raise HTTPException(422, "编码只能包含小写字母 数字 点 下划线和连字符")
    return clean


def _category(db: Session, auth: AuthContext, category_id: int) -> VideoCategory:
    row = db.query(VideoCategory).filter(
        VideoCategory.id == category_id,
        VideoCategory.tenant_id == auth.tenant_id,
    ).first()
    if not row:
        raise HTTPException(404, "分类不存在")
    return row


def _group(db: Session, auth: AuthContext, group_id: int) -> VideoTagGroup:
    row = db.query(VideoTagGroup).filter(
        VideoTagGroup.id == group_id,
        VideoTagGroup.tenant_id == auth.tenant_id,
    ).first()
    if not row:
        raise HTTPException(404, "标签组不存在")
    return row


def _tag(db: Session, auth: AuthContext, tag_id: int) -> VideoTag:
    row = db.query(VideoTag).filter(
        VideoTag.id == tag_id,
        VideoTag.tenant_id == auth.tenant_id,
    ).first()
    if not row or (row.scope == "personal" and row.owner_id != auth.user_id):
        raise HTTPException(404, "标签不存在")
    return row


def _validate_parent(
    db: Session,
    auth: AuthContext,
    object_type: str,
    parent_id: Optional[int],
    require_active: bool = False,
) -> Optional[VideoCategory]:
    if parent_id is None:
        return None
    parent = _category(db, auth, parent_id)
    if parent.object_type != object_type or parent.parent_id is not None:
        raise HTTPException(422, "二级分类只能挂在同类型的一级分类下")
    if require_active and parent.status != "active":
        raise HTTPException(422, "不能在已停用的一级分类下维护二级分类")
    return parent


def _validate_unique_category_name(
    db: Session,
    auth: AuthContext,
    object_type: str,
    parent_id: Optional[int],
    name: str,
    exclude_id: Optional[int] = None,
) -> None:
    query = db.query(VideoCategory).filter(
        VideoCategory.tenant_id == auth.tenant_id,
        VideoCategory.object_type == object_type,
        VideoCategory.name == name,
    )
    query = query.filter(VideoCategory.parent_id.is_(None)) if parent_id is None else query.filter(VideoCategory.parent_id == parent_id)
    if exclude_id is not None:
        query = query.filter(VideoCategory.id != exclude_id)
    if query.first():
        raise HTTPException(409, "同级分类名称已存在")


def _validate_group_scope(group: VideoTagGroup, applies_to: str) -> None:
    if group.status != "active":
        raise HTTPException(422, "不能向已停用的标签组添加标签")
    if group.applies_to != "both" and applies_to != group.applies_to:
        raise HTTPException(422, "标签适用范围不能超出标签组适用范围")


def _category_dict(row: VideoCategory, usage_count: int, child_count: int) -> dict:
    return {
        "id": row.id,
        "object_type": row.object_type,
        "parent_id": row.parent_id,
        "name": row.name,
        "code": row.code,
        "sort_order": row.sort_order,
        "status": row.status,
        "usage_count": usage_count,
        "child_count": child_count,
    }


def _group_dict(row: VideoTagGroup, usage_count: int, tag_count: int) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "code": row.code,
        "applies_to": row.applies_to,
        "inherit_to_final": row.inherit_to_final,
        "sort_order": row.sort_order,
        "status": row.status,
        "usage_count": usage_count,
        "tag_count": tag_count,
    }


def _tag_dict(row: VideoTag, usage_count: int) -> dict:
    return {
        "id": row.id,
        "group_id": row.group_id,
        "name": row.name,
        "code": row.code,
        "scope": row.scope,
        "applies_to": row.applies_to,
        "sort_order": row.sort_order,
        "status": row.status,
        "usage_count": usage_count,
    }


def _preset_dict(row: VideoFilterPreset) -> dict:
    return {
        "id": row.id,
        "object_type": row.object_type,
        "name": row.name,
        "filters": row.filters or {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/catalog")
def catalog(
    object_type: ObjectType,
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(current_auth),
):
    ensure_default_taxonomy(db, auth.tenant_id)
    db.commit()
    can_manage = auth.role in ADMIN_ROLES
    include_inactive = include_inactive and can_manage

    category_query = db.query(VideoCategory).filter(
        VideoCategory.tenant_id == auth.tenant_id,
        VideoCategory.object_type == object_type,
    )
    group_query = db.query(VideoTagGroup).filter(VideoTagGroup.tenant_id == auth.tenant_id)
    tag_query = db.query(VideoTag).filter(
        VideoTag.tenant_id == auth.tenant_id,
        ((VideoTag.scope == "public") | (VideoTag.owner_id == auth.user_id)),
    )
    if not include_inactive:
        category_query = category_query.filter(VideoCategory.status == "active")
        group_query = group_query.filter(VideoTagGroup.status == "active")
        tag_query = tag_query.filter(VideoTag.status == "active")

    categories = [row for row in category_query.all()]
    groups = [row for row in group_query.all() if applies_to_object(row.applies_to, object_type)]
    allowed_group_ids = {row.id for row in groups}
    tags = [
        row for row in tag_query.all()
        if applies_to_object(row.applies_to, object_type)
        and (row.scope == "personal" or row.group_id in allowed_group_ids)
    ]

    category_usage = dict(db.query(VideoAsset.category_id, func.count(VideoAsset.id)).filter(
        VideoAsset.tenant_id == auth.tenant_id,
        VideoAsset.category_id.is_not(None),
    ).group_by(VideoAsset.category_id).all())
    child_counts = dict(db.query(VideoCategory.parent_id, func.count(VideoCategory.id)).filter(
        VideoCategory.tenant_id == auth.tenant_id,
        VideoCategory.object_type == object_type,
        VideoCategory.parent_id.is_not(None),
    ).group_by(VideoCategory.parent_id).all())
    tag_usage = dict(db.query(VideoAssetTag.tag_id, func.count(VideoAssetTag.id)).filter(
        VideoAssetTag.tenant_id == auth.tenant_id,
    ).group_by(VideoAssetTag.tag_id).all())
    tag_counts = dict(db.query(VideoTag.group_id, func.count(VideoTag.id)).filter(
        VideoTag.tenant_id == auth.tenant_id,
        VideoTag.scope == "public",
        VideoTag.group_id.is_not(None),
    ).group_by(VideoTag.group_id).all())

    presets = db.query(VideoFilterPreset).filter(
        VideoFilterPreset.tenant_id == auth.tenant_id,
        VideoFilterPreset.owner_id == auth.user_id,
        VideoFilterPreset.object_type == object_type,
    ).order_by(VideoFilterPreset.updated_at.desc()).all()
    return {
        "object_type": object_type,
        "can_manage_public": can_manage,
        "categories": [
            _category_dict(row, category_usage.get(row.id, 0), child_counts.get(row.id, 0))
            for row in sorted(categories, key=lambda item: (item.parent_id is not None, item.sort_order, item.id))
        ],
        "groups": [
            _group_dict(row, sum(tag_usage.get(tag.id, 0) for tag in tags if tag.group_id == row.id), tag_counts.get(row.id, 0))
            for row in sorted(groups, key=lambda item: (item.sort_order, item.id))
        ],
        "public_tags": [
            _tag_dict(row, tag_usage.get(row.id, 0))
            for row in sorted(tags, key=lambda item: (item.group_id or 0, item.sort_order, item.id))
            if row.scope == "public"
        ],
        "personal_tags": [
            _tag_dict(row, tag_usage.get(row.id, 0))
            for row in sorted(tags, key=lambda item: (item.sort_order, item.id))
            if row.scope == "personal"
        ],
        "presets": [_preset_dict(row) for row in presets],
    }


@router.post("/categories")
def create_category(payload: CategoryCreate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _require_admin(auth)
    name = _name(payload.name)
    _validate_parent(db, auth, payload.object_type, payload.parent_id, require_active=True)
    _validate_unique_category_name(db, auth, payload.object_type, payload.parent_id, name)
    row = VideoCategory(
        tenant_id=auth.tenant_id,
        object_type=payload.object_type,
        parent_id=payload.parent_id,
        name=name,
        code=_code(payload.code),
        sort_order=payload.sort_order,
    )
    db.add(row)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "分类编码已存在") from exc
    db.refresh(row)
    return _category_dict(row, 0, 0)


@router.put("/categories/{category_id}")
def update_category(category_id: int, payload: CategoryUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _require_admin(auth)
    row = _category(db, auth, category_id)
    parent_id = payload.parent_id if "parent_id" in payload.model_fields_set else row.parent_id
    _validate_parent(
        db,
        auth,
        row.object_type,
        parent_id,
        require_active=parent_id is not None and (parent_id != row.parent_id or payload.status == "active"),
    )
    if parent_id == row.id:
        raise HTTPException(422, "分类不能成为自己的父分类")
    if payload.status == "active" and parent_id is not None:
        parent = _category(db, auth, parent_id)
        if parent.status != "active":
            raise HTTPException(422, "父分类已停用，不能启用该二级分类")
    if parent_id is not None and db.query(VideoCategory).filter(VideoCategory.parent_id == row.id).first():
        raise HTTPException(422, "包含二级分类的一级分类不能移动")
    name = _name(payload.name) if payload.name is not None else row.name
    _validate_unique_category_name(db, auth, row.object_type, parent_id, name, row.id)
    row.name = name
    row.parent_id = parent_id
    if payload.code is not None:
        row.code = _code(payload.code)
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if payload.status is not None:
        row.status = payload.status
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "分类编码已存在") from exc
    db.refresh(row)
    return _category_dict(row, 0, 0)


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _require_admin(auth)
    row = _category(db, auth, category_id)
    usage = db.query(VideoAsset).filter(VideoAsset.category_id == row.id, VideoAsset.tenant_id == auth.tenant_id).count()
    children = db.query(VideoCategory).filter(VideoCategory.parent_id == row.id, VideoCategory.tenant_id == auth.tenant_id).count()
    if usage or children:
        raise HTTPException(409, {"message": "分类正在使用中 请先停用或合并", "usage_count": usage, "child_count": children})
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/categories/{category_id}/merge")
def merge_category(category_id: int, payload: MergePayload, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _require_admin(auth)
    source = _category(db, auth, category_id)
    target = _category(db, auth, payload.target_id)
    if source.id == target.id or source.object_type != target.object_type or source.parent_id != target.parent_id:
        raise HTTPException(422, "只能合并同一层级和同一父分类下的分类")
    if db.query(VideoCategory).filter(VideoCategory.parent_id == source.id).first():
        raise HTTPException(422, "包含二级分类的一级分类不能直接合并")
    updated = db.query(VideoAsset).filter(
        VideoAsset.tenant_id == auth.tenant_id,
        VideoAsset.category_id == source.id,
    ).update({"category_id": target.id}, synchronize_session=False)
    db.delete(source)
    db.commit()
    return {"ok": True, "updated_assets": updated}


@router.post("/groups")
def create_group(payload: TagGroupCreate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _require_admin(auth)
    name = _name(payload.name)
    if db.query(VideoTagGroup).filter_by(tenant_id=auth.tenant_id, name=name).first():
        raise HTTPException(409, "标签组名称已存在")
    row = VideoTagGroup(
        tenant_id=auth.tenant_id,
        name=name,
        code=_code(payload.code),
        applies_to=payload.applies_to,
        inherit_to_final=payload.inherit_to_final,
        sort_order=payload.sort_order,
    )
    db.add(row)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "标签组编码已存在") from exc
    db.refresh(row)
    return _group_dict(row, 0, 0)


@router.put("/groups/{group_id}")
def update_group(group_id: int, payload: TagGroupUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _require_admin(auth)
    row = _group(db, auth, group_id)
    next_applies_to = payload.applies_to or row.applies_to
    incompatible = db.query(VideoTag).filter(
        VideoTag.tenant_id == auth.tenant_id,
        VideoTag.group_id == row.id,
        VideoTag.applies_to != next_applies_to,
    ).count() if next_applies_to != "both" else 0
    if incompatible:
        raise HTTPException(422, "标签组中存在适用范围不一致的标签")
    if payload.name is not None:
        row.name = _name(payload.name)
    if payload.code is not None:
        row.code = _code(payload.code)
    if payload.applies_to is not None:
        row.applies_to = payload.applies_to
    if payload.inherit_to_final is not None:
        row.inherit_to_final = payload.inherit_to_final
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if payload.status is not None:
        row.status = payload.status
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "标签组名称或编码已存在") from exc
    db.refresh(row)
    return _group_dict(row, 0, 0)


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _require_admin(auth)
    row = _group(db, auth, group_id)
    tag_count = db.query(VideoTag).filter(VideoTag.group_id == row.id, VideoTag.tenant_id == auth.tenant_id).count()
    if tag_count:
        raise HTTPException(409, {"message": "标签组中仍有标签", "tag_count": tag_count})
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/tags")
def create_public_tag(payload: PublicTagCreate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _require_admin(auth)
    group = _group(db, auth, payload.group_id)
    _validate_group_scope(group, payload.applies_to)
    name = _name(payload.name)
    if db.query(VideoTag).filter_by(tenant_id=auth.tenant_id, scope="public", owner_id="", name=name).first():
        raise HTTPException(409, "公共标签名称已存在")
    row = VideoTag(
        tenant_id=auth.tenant_id,
        group_id=group.id,
        name=name,
        code=_code(payload.code),
        scope="public",
        owner_id="",
        applies_to=payload.applies_to,
        sort_order=payload.sort_order,
    )
    db.add(row)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "公共标签编码已存在") from exc
    db.refresh(row)
    return _tag_dict(row, 0)


@router.post("/personal-tags")
def create_personal_tag(payload: PersonalTagCreate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    name = _name(payload.name)
    if db.query(VideoTag).filter_by(
        tenant_id=auth.tenant_id,
        scope="personal",
        owner_id=auth.user_id,
        name=name,
    ).first():
        raise HTTPException(409, "个人标签名称已存在")
    row = VideoTag(
        tenant_id=auth.tenant_id,
        group_id=None,
        name=name,
        code=uuid.uuid4().hex[:12],
        scope="personal",
        owner_id=auth.user_id,
        applies_to=payload.applies_to,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _tag_dict(row, 0)


@router.put("/tags/{tag_id}")
def update_tag(tag_id: int, payload: TagUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = _tag(db, auth, tag_id)
    if row.scope == "public":
        _require_admin(auth)
    elif row.owner_id != auth.user_id:
        raise HTTPException(403, "只能维护自己的个人标签")
    group_id = payload.group_id if "group_id" in payload.model_fields_set else row.group_id
    applies_to = payload.applies_to or row.applies_to
    if row.scope == "public":
        if group_id is None:
            raise HTTPException(422, "公共标签必须属于标签组")
        _validate_group_scope(_group(db, auth, group_id), applies_to)
    elif group_id is not None:
        raise HTTPException(422, "个人标签不能加入公共标签组")
    if payload.name is not None:
        name = _name(payload.name)
        duplicate = db.query(VideoTag).filter(
            VideoTag.tenant_id == auth.tenant_id,
            VideoTag.scope == row.scope,
            VideoTag.owner_id == row.owner_id,
            VideoTag.name == name,
            VideoTag.id != row.id,
        ).first()
        if duplicate:
            raise HTTPException(409, "标签名称已存在")
        row.name = name
    if payload.code is not None:
        row.code = _code(payload.code)
    row.group_id = group_id
    if payload.applies_to is not None:
        row.applies_to = payload.applies_to
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if payload.status is not None:
        row.status = payload.status
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "标签名称或编码已存在") from exc
    db.refresh(row)
    return _tag_dict(row, 0)


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = _tag(db, auth, tag_id)
    if row.scope == "public":
        _require_admin(auth)
    usage = db.query(VideoAssetTag).filter(
        VideoAssetTag.tenant_id == auth.tenant_id,
        VideoAssetTag.tag_id == row.id,
    ).count()
    if row.scope == "public" and usage:
        raise HTTPException(409, {"message": "公共标签正在使用中 请先停用或合并", "usage_count": usage})
    db.query(VideoAssetTag).filter(
        VideoAssetTag.tenant_id == auth.tenant_id,
        VideoAssetTag.tag_id == row.id,
    ).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/tags/{tag_id}/merge")
def merge_tag(tag_id: int, payload: MergePayload, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    _require_admin(auth)
    source = _tag(db, auth, tag_id)
    target = _tag(db, auth, payload.target_id)
    if source.scope != "public" or target.scope != "public" or source.id == target.id:
        raise HTTPException(422, "只能合并不同的公共标签")
    source_links = db.query(VideoAssetTag).filter(
        VideoAssetTag.tenant_id == auth.tenant_id,
        VideoAssetTag.tag_id == source.id,
    ).all()
    target_assets = {
        row.asset_id for row in db.query(VideoAssetTag).filter(
            VideoAssetTag.tenant_id == auth.tenant_id,
            VideoAssetTag.tag_id == target.id,
        ).all()
    }
    updated = 0
    for link in source_links:
        if link.asset_id in target_assets:
            db.delete(link)
        else:
            link.tag_id = target.id
            updated += 1
    db.delete(source)
    db.commit()
    return {"ok": True, "updated_assets": updated}


@router.put("/assets/batch")
def update_assets(payload: AssetTaxonomyUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    asset_ids = list(dict.fromkeys(payload.asset_ids))
    assets = db.query(VideoAsset).filter(
        VideoAsset.tenant_id == auth.tenant_id,
        VideoAsset.id.in_(asset_ids),
    ).all()
    if len(assets) != len(asset_ids):
        raise HTTPException(404, "部分视频不存在或无权访问")
    _require_asset_access(auth, assets)

    category = None
    if payload.set_category and payload.category_id is not None:
        category = _category(db, auth, payload.category_id)
        if category.status != "active":
            raise HTTPException(422, "不能使用已停用分类")
        if category.parent_id is None:
            raise HTTPException(422, "素材只能绑定二级分类")
        parent = _category(db, auth, category.parent_id)
        if parent.status != "active":
            raise HTTPException(422, "不能使用父分类已停用的分类")
    if payload.set_category:
        for asset in assets:
            if category is not None and category.object_type != object_type_for_asset(asset):
                raise HTTPException(422, "分类与视频对象类型不匹配")
            asset.category_id = category.id if category else None

    requested_tag_ids = set(payload.add_tag_ids) | set(payload.remove_tag_ids)
    tags = db.query(VideoTag).filter(
        VideoTag.tenant_id == auth.tenant_id,
        VideoTag.id.in_(requested_tag_ids),
    ).all() if requested_tag_ids else []
    accessible_tags = {
        row.id: row for row in tags
        if row.scope == "public" or row.owner_id == auth.user_id
    }
    if len(accessible_tags) != len(requested_tag_ids):
        raise HTTPException(422, "部分标签不存在或无权使用")
    for tag_id in payload.add_tag_ids:
        if accessible_tags[tag_id].status != "active":
            raise HTTPException(422, "不能添加已停用标签")
        tag = accessible_tags[tag_id]
        if tag.scope == "public":
            group = _group(db, auth, tag.group_id)
            if group.status != "active":
                raise HTTPException(422, "不能添加所属标签组已停用的标签")
    for asset in assets:
        for tag_id in payload.add_tag_ids:
            if not applies_to_object(accessible_tags[tag_id].applies_to, object_type_for_asset(asset)):
                raise HTTPException(422, "标签与视频对象类型不匹配")

    if payload.remove_tag_ids:
        db.query(VideoAssetTag).filter(
            VideoAssetTag.tenant_id == auth.tenant_id,
            VideoAssetTag.asset_id.in_(asset_ids),
            VideoAssetTag.tag_id.in_(payload.remove_tag_ids),
        ).delete(synchronize_session=False)

    existing = {
        (row.asset_id, row.tag_id)
        for row in db.query(VideoAssetTag).filter(
            VideoAssetTag.tenant_id == auth.tenant_id,
            VideoAssetTag.asset_id.in_(asset_ids),
            VideoAssetTag.tag_id.in_(payload.add_tag_ids),
        ).all()
    } if payload.add_tag_ids else set()
    added = 0
    for asset in assets:
        for tag_id in payload.add_tag_ids:
            if (asset.id, tag_id) in existing:
                continue
            db.add(VideoAssetTag(
                tenant_id=auth.tenant_id,
                asset_id=asset.id,
                tag_id=tag_id,
                source="manual",
                confidence=1.0,
                confirmed=True,
                created_by=auth.user_id,
            ))
            added += 1
    db.commit()
    return {"updated_asset_ids": asset_ids, "added_assignments": added}


@router.get("/presets")
def list_presets(object_type: ObjectType, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    rows = db.query(VideoFilterPreset).filter(
        VideoFilterPreset.tenant_id == auth.tenant_id,
        VideoFilterPreset.owner_id == auth.user_id,
        VideoFilterPreset.object_type == object_type,
    ).order_by(VideoFilterPreset.updated_at.desc()).all()
    return [_preset_dict(row) for row in rows]


@router.post("/presets")
def create_preset(payload: PresetCreate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    name = _name(payload.name)
    if db.query(VideoFilterPreset).filter_by(
        tenant_id=auth.tenant_id,
        owner_id=auth.user_id,
        object_type=payload.object_type,
        name=name,
    ).first():
        raise HTTPException(409, "筛选预设名称已存在")
    row = VideoFilterPreset(
        tenant_id=auth.tenant_id,
        owner_id=auth.user_id,
        object_type=payload.object_type,
        name=name,
        filters=payload.filters,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _preset_dict(row)


@router.put("/presets/{preset_id}")
def update_preset(preset_id: int, payload: PresetUpdate, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoFilterPreset).filter(
        VideoFilterPreset.id == preset_id,
        VideoFilterPreset.tenant_id == auth.tenant_id,
        VideoFilterPreset.owner_id == auth.user_id,
    ).first()
    if not row:
        raise HTTPException(404, "筛选预设不存在")
    if payload.name is not None:
        row.name = _name(payload.name)
    if payload.filters is not None:
        row.filters = payload.filters
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "筛选预设名称已存在") from exc
    db.refresh(row)
    return _preset_dict(row)


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    row = db.query(VideoFilterPreset).filter(
        VideoFilterPreset.id == preset_id,
        VideoFilterPreset.tenant_id == auth.tenant_id,
        VideoFilterPreset.owner_id == auth.user_id,
    ).first()
    if not row:
        raise HTTPException(404, "筛选预设不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
