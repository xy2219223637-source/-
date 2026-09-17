from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth, hash_password
from app.database import get_db
from app.models import MemberGroup, Team, Tenant, TenantMember, User

platform_router = APIRouter(prefix="/platform-admin", tags=["platform-admin"])
tenant_router = APIRouter(prefix="/tenant-admin", tags=["tenant-admin"])

PHONE_PATTERN = re.compile(r"^1\d{10}$")


def platform_admin(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if not auth.is_platform_admin:
        raise HTTPException(status_code=403, detail="仅平台管理员可访问")
    return auth


def tenant_admin(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if auth.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="仅企业管理员可访问")
    return auth


def require_phone(phone: str) -> str:
    value = phone.strip()
    if not PHONE_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail="请输入正确的11位手机号")
    return value


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    owner_name: str = Field(min_length=1, max_length=200)
    owner_phone: str
    initial_password: str = Field(min_length=8, max_length=128)


class TenantUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    status: Optional[str] = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class PlatformUserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    phone: Optional[str] = None
    status: Optional[str] = None


class NamedPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class GroupCreate(NamedPayload):
    team_id: int


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str
    initial_password: str = Field(min_length=8, max_length=128)
    team_id: int
    group_id: Optional[int] = None
    is_admin: bool = False
    job_title: str = Field(default="", max_length=120)
    employee_no: str = Field(default="", max_length=100)


class MemberUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    phone: Optional[str] = None
    team_id: Optional[int] = None
    group_id: Optional[int] = None
    is_admin: Optional[bool] = None
    status: Optional[str] = None
    job_title: Optional[str] = Field(default=None, max_length=120)
    employee_no: Optional[str] = Field(default=None, max_length=100)


def member_dict(member: TenantMember, user: User, team: Optional[Team], group: Optional[MemberGroup]) -> dict:
    return {
        "id": member.id, "user_id": user.id, "name": user.name, "phone": user.phone or "",
        "job_title": user.job_title, "employee_no": user.employee_no, "status": member.status,
        "is_admin": member.role in {"owner", "admin"}, "is_owner": member.role == "owner",
        "team_id": member.team_id, "team_name": team.name if team else "",
        "group_id": member.group_id, "group_name": group.name if group else "",
        "created_at": user.created_at,
    }


@platform_router.get("/tenants")
def list_tenants(_: AuthContext = Depends(platform_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    result = []
    for tenant in rows:
        members = db.query(TenantMember).filter_by(tenant_id=tenant.id).all()
        owner = next((item for item in members if item.role == "owner"), None)
        owner_user = db.query(User).filter_by(id=owner.user_id).first() if owner else None
        result.append({"id": tenant.id, "name": tenant.name, "code": tenant.code, "status": tenant.status,
                       "member_count": len(members), "owner_name": owner_user.name if owner_user else "",
                       "owner_phone": owner_user.phone if owner_user else "", "owner_user_id": owner_user.id if owner_user else "",
                       "owner_status": owner_user.status if owner_user else "",
                       "created_at": tenant.created_at})
    return result


@platform_router.post("/tenants")
def create_tenant(payload: TenantCreate, _: AuthContext = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    phone = require_phone(payload.owner_phone)
    if db.query(Tenant).filter_by(code=payload.code).first():
        raise HTTPException(status_code=409, detail="企业代码已存在")
    if db.query(User).filter_by(phone=phone).first():
        raise HTTPException(status_code=409, detail="手机号已被使用")
    tenant = Tenant(id=uuid.uuid4().hex, name=payload.name.strip(), code=payload.code, status="active")
    user = User(id=uuid.uuid4().hex, email=f"{phone}@phone.local", phone=phone, name=payload.owner_name.strip(),
                password_hash=hash_password(payload.initial_password), must_change_password=True)
    db.add_all([tenant, user]); db.flush()
    db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner", status="active"))
    db.commit()
    return {"id": tenant.id, "owner_user_id": user.id}


@platform_router.patch("/tenants/{tenant_id}")
def update_tenant(tenant_id: str, payload: TenantUpdate, _: AuthContext = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    row = db.query(Tenant).filter_by(id=tenant_id).first()
    if not row: raise HTTPException(404, "企业不存在")
    if payload.name is not None: row.name = payload.name.strip()
    if payload.status is not None:
        if payload.status not in {"active", "disabled"}: raise HTTPException(422, "状态无效")
        row.status = payload.status
    db.commit()
    return {"ok": True}


@platform_router.post("/users/{user_id}/reset-password")
def platform_reset_password(user_id: str, payload: PasswordReset, _: AuthContext = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter_by(id=user_id).first()
    if not user: raise HTTPException(404, "用户不存在")
    user.password_hash = hash_password(payload.new_password); user.must_change_password = True
    db.commit()
    return {"ok": True}


@platform_router.patch("/users/{user_id}")
def update_platform_user(user_id: str, payload: PlatformUserUpdate, auth: AuthContext = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter_by(id=user_id).first()
    if not user: raise HTTPException(404, "用户不存在")
    if payload.phone is not None:
        phone = require_phone(payload.phone)
        if db.query(User).filter(User.phone == phone, User.id != user.id).first(): raise HTTPException(409, "手机号已被使用")
        user.phone = phone
    if payload.name is not None: user.name = payload.name.strip()
    if payload.status is not None:
        if payload.status not in {"active", "disabled"}: raise HTTPException(422, "状态无效")
        if user.id == auth.user_id and payload.status == "disabled": raise HTTPException(409, "不能停用当前登录账号")
        user.status = payload.status
    db.commit(); return {"ok": True}


@tenant_router.get("/teams")
def list_teams(auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> list[dict]:
    teams = db.query(Team).filter_by(tenant_id=auth.tenant_id, status="active").order_by(Team.id).all()
    return [{"id": row.id, "name": row.name,
             "member_count": db.query(TenantMember).filter_by(tenant_id=auth.tenant_id, team_id=row.id, status="active").count()} for row in teams]


@tenant_router.post("/teams")
def create_team(payload: NamedPayload, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    name = payload.name.strip()
    if db.query(Team).filter_by(tenant_id=auth.tenant_id, name=name, status="active").first(): raise HTTPException(409, "团队名称已存在")
    row = Team(tenant_id=auth.tenant_id, name=name); db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "name": row.name}


@tenant_router.patch("/teams/{team_id}")
def rename_team(team_id: int, payload: NamedPayload, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    row = db.query(Team).filter_by(id=team_id, tenant_id=auth.tenant_id, status="active").first()
    if not row: raise HTTPException(404, "团队不存在")
    row.name = payload.name.strip(); db.commit(); return {"ok": True}


@tenant_router.delete("/teams/{team_id}")
def delete_team(team_id: int, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    row = db.query(Team).filter_by(id=team_id, tenant_id=auth.tenant_id, status="active").first()
    if not row: raise HTTPException(404, "团队不存在")
    if db.query(TenantMember).filter_by(tenant_id=auth.tenant_id, team_id=team_id, status="active").count(): raise HTTPException(409, "请先转移团队中的用户")
    row.status = "deleted"
    for group in db.query(MemberGroup).filter_by(tenant_id=auth.tenant_id, team_id=team_id).all(): group.status = "deleted"
    db.commit(); return {"ok": True}


@tenant_router.get("/groups")
def list_groups(team_id: Optional[int] = None, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> list[dict]:
    query = db.query(MemberGroup).filter_by(tenant_id=auth.tenant_id, status="active")
    if team_id is not None: query = query.filter_by(team_id=team_id)
    return [{"id": row.id, "team_id": row.team_id, "name": row.name,
             "member_count": db.query(TenantMember).filter_by(tenant_id=auth.tenant_id, group_id=row.id, status="active").count()} for row in query.order_by(MemberGroup.id).all()]


@tenant_router.post("/groups")
def create_group(payload: GroupCreate, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    team = db.query(Team).filter_by(id=payload.team_id, tenant_id=auth.tenant_id, status="active").first()
    if not team: raise HTTPException(404, "团队不存在")
    name = payload.name.strip()
    if db.query(MemberGroup).filter_by(team_id=team.id, name=name, status="active").first(): raise HTTPException(409, "分组名称已存在")
    row = MemberGroup(tenant_id=auth.tenant_id, team_id=team.id, name=name); db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "team_id": row.team_id, "name": row.name}


@tenant_router.patch("/groups/{group_id}")
def rename_group(group_id: int, payload: NamedPayload, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    row = db.query(MemberGroup).filter_by(id=group_id, tenant_id=auth.tenant_id, status="active").first()
    if not row: raise HTTPException(404, "分组不存在")
    row.name = payload.name.strip(); db.commit(); return {"ok": True}


@tenant_router.delete("/groups/{group_id}")
def delete_group(group_id: int, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    row = db.query(MemberGroup).filter_by(id=group_id, tenant_id=auth.tenant_id, status="active").first()
    if not row: raise HTTPException(404, "分组不存在")
    if db.query(TenantMember).filter_by(tenant_id=auth.tenant_id, group_id=group_id, status="active").count(): raise HTTPException(409, "请先转移分组中的用户")
    row.status = "deleted"; db.commit(); return {"ok": True}


@tenant_router.get("/members")
def list_members(keyword: str = "", status: str = "", team_id: Optional[int] = None, group_id: Optional[int] = None,
                 is_admin: Optional[bool] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    query = db.query(TenantMember, User).join(User, User.id == TenantMember.user_id).filter(TenantMember.tenant_id == auth.tenant_id)
    if keyword: query = query.filter(or_(User.name.contains(keyword), User.phone.contains(keyword), User.employee_no.contains(keyword)))
    if status: query = query.filter(TenantMember.status == status)
    if team_id is not None: query = query.filter(TenantMember.team_id == team_id)
    if group_id is not None: query = query.filter(TenantMember.group_id == group_id)
    if is_admin is True: query = query.filter(TenantMember.role.in_(["owner", "admin"]))
    if is_admin is False: query = query.filter(TenantMember.role == "member")
    total = query.count()
    rows = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    teams = {row.id: row for row in db.query(Team).filter_by(tenant_id=auth.tenant_id).all()}
    groups = {row.id: row for row in db.query(MemberGroup).filter_by(tenant_id=auth.tenant_id).all()}
    return {"items": [member_dict(member, user, teams.get(member.team_id), groups.get(member.group_id)) for member, user in rows],
            "total": total, "page": page, "page_size": page_size}


def validate_assignment(db: Session, tenant_id: str, team_id: int, group_id: Optional[int]) -> tuple[Team, Optional[MemberGroup]]:
    team = db.query(Team).filter_by(id=team_id, tenant_id=tenant_id, status="active").first()
    if not team: raise HTTPException(422, "请选择有效团队")
    group = None
    if group_id is not None:
        group = db.query(MemberGroup).filter_by(id=group_id, tenant_id=tenant_id, team_id=team_id, status="active").first()
        if not group: raise HTTPException(422, "请选择该团队下的有效分组")
    return team, group


@tenant_router.post("/members")
def create_member(payload: MemberCreate, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    phone = require_phone(payload.phone); validate_assignment(db, auth.tenant_id, payload.team_id, payload.group_id)
    if db.query(User).filter_by(phone=phone).first(): raise HTTPException(409, "手机号已被使用")
    user = User(id=uuid.uuid4().hex, email=f"{phone}@phone.local", phone=phone, name=payload.name.strip(),
                password_hash=hash_password(payload.initial_password), must_change_password=True,
                job_title=payload.job_title.strip(), employee_no=payload.employee_no.strip())
    db.add(user); db.flush()
    member = TenantMember(tenant_id=auth.tenant_id, user_id=user.id, role="admin" if payload.is_admin else "member",
                          status="active", team_id=payload.team_id, group_id=payload.group_id)
    db.add(member); db.commit(); db.refresh(member)
    return {"id": member.id, "user_id": user.id}


@tenant_router.patch("/members/{member_id}")
def update_member(member_id: int, payload: MemberUpdate, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    member = db.query(TenantMember).filter_by(id=member_id, tenant_id=auth.tenant_id).first()
    if not member: raise HTTPException(404, "用户不存在")
    user = db.query(User).filter_by(id=member.user_id).first()
    if payload.phone is not None:
        phone = require_phone(payload.phone)
        conflict = db.query(User).filter(User.phone == phone, User.id != user.id).first()
        if conflict: raise HTTPException(409, "手机号已被使用")
        user.phone = phone
    if payload.name is not None: user.name = payload.name.strip()
    if payload.job_title is not None: user.job_title = payload.job_title.strip()
    if payload.employee_no is not None: user.employee_no = payload.employee_no.strip()
    target_team = payload.team_id if payload.team_id is not None else member.team_id
    target_group = payload.group_id if payload.group_id is not None else member.group_id
    if target_team is not None:
        validate_assignment(db, auth.tenant_id, target_team, target_group)
        member.team_id, member.group_id = target_team, target_group
    if member.role == "owner" and (payload.is_admin is False or payload.status == "disabled"):
        raise HTTPException(409, "不能取消或停用企业主管理员")
    if payload.is_admin is not None and member.role != "owner": member.role = "admin" if payload.is_admin else "member"
    if payload.status is not None:
        if payload.status not in {"active", "disabled"}: raise HTTPException(422, "状态无效")
        member.status = payload.status
    db.commit(); return {"ok": True}


@tenant_router.post("/members/{member_id}/reset-password")
def reset_member_password(member_id: int, payload: PasswordReset, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    member = db.query(TenantMember).filter_by(id=member_id, tenant_id=auth.tenant_id).first()
    if not member: raise HTTPException(404, "用户不存在")
    user = db.query(User).filter_by(id=member.user_id).first(); user.password_hash = hash_password(payload.new_password); user.must_change_password = True
    db.commit(); return {"ok": True}


@tenant_router.delete("/members/{member_id}")
def delete_member(member_id: int, auth: AuthContext = Depends(tenant_admin), db: Session = Depends(get_db)) -> dict:
    member = db.query(TenantMember).filter_by(id=member_id, tenant_id=auth.tenant_id).first()
    if not member: raise HTTPException(404, "用户不存在")
    if member.role == "owner": raise HTTPException(409, "不能删除企业主管理员")
    if member.user_id == auth.user_id: raise HTTPException(409, "不能删除当前登录用户")
    member.status = "deleted"; db.commit(); return {"ok": True}
