from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.database import get_db
from app.models import TenantMember, TenantRole

USER_PERMISSION_GROUPS = [
    {"key": "workspace", "label": "工作台", "children": [{"code": "dashboard.view", "label": "工作台"}]},
    {"key": "product_brand", "label": "商品与品牌", "children": [
        {"code": "products.access", "label": "商品管理"}, {"code": "knowledge.access", "label": "知识库"},
        {"code": "brand_visuals.access", "label": "品牌视觉"}]},
    {"key": "content_design", "label": "内容设计", "children": [
        {"code": "design_skills.access", "label": "设计 Skill"}, {"code": "creative_projects.access", "label": "AI 创作工作台"}]},
    {"key": "video_production", "label": "视频生产", "children": [
        {"code": "finished_assets.access", "label": "成品素材库"}, {"code": "manual_compose.access", "label": "视频编排"},
        {"code": "finished_remix.access", "label": "成品混剪"}, {"code": "final_videos.access", "label": "成片库"},
        {"code": "video_repair.access", "label": "审核失败修改"},
        {"code": "director.access", "label": "编导灵感"}, {"code": "shooting_tasks.access", "label": "拍摄任务"}]},
    {"key": "operations", "label": "运营管理", "children": [
        {"code": "task_center.access", "label": "任务与费用"}]},
]

ADMIN_PERMISSION_GROUPS = [
    {"key": "tenant_admin", "label": "企业管理后台", "children": [
        {"code": "tenant_users.manage", "label": "用户管理"},
        {"code": "tenant_teams.manage", "label": "团队与分组管理"},
        {"code": "tenant_roles.manage", "label": "角色管理"},
    ]},
]

ALL_USER_PERMISSIONS = [item["code"] for group in USER_PERMISSION_GROUPS for item in group["children"]]
ALL_ADMIN_PERMISSIONS = [item["code"] for group in ADMIN_PERMISSION_GROUPS for item in group["children"]]


def permission_codes(db: Session, member: TenantMember) -> list[str]:
    if member.role == "owner":
        return ["*"]
    role = db.query(TenantRole).filter_by(id=member.role_id, tenant_id=member.tenant_id, status="active").first() if member.role_id else None
    if role:
        return list(role.permissions or [])
    return ALL_USER_PERMISSIONS + (ALL_ADMIN_PERMISSIONS if member.role == "admin" else [])


def require_permission(code: str):
    def dependency(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)) -> AuthContext:
        if auth.role == "owner":
            return auth
        member = db.query(TenantMember).filter_by(user_id=auth.user_id, tenant_id=auth.tenant_id, status="active").first()
        if not member or code not in permission_codes(db, member):
            raise HTTPException(status_code=403, detail="当前角色没有此操作权限")
        return auth
    return dependency
