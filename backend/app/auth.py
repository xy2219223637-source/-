from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Tenant, TenantMember, TenantRole, User

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{base64.urlsafe_b64encode(salt).decode()}.{base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    salt_value, digest_value = encoded.split(".", 1)
    salt = base64.urlsafe_b64decode(salt_value)
    expected = base64.urlsafe_b64decode(digest_value)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return hmac.compare_digest(actual, expected)


def create_token(user_id: str, tenant_id: str) -> str:
    payload = {"sub": user_id, "tenant_id": tenant_id, "exp": int(time.time()) + get_settings().access_token_hours * 3600}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = hmac.new(get_settings().auth_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


@dataclass
class AuthContext:
    user_id: str
    tenant_id: str
    role: str
    email: str
    tenant_name: str
    phone: str = ""
    is_platform_admin: bool = False
    permissions: tuple[str, ...] = ()


def current_auth(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: Session = Depends(get_db)) -> AuthContext:
    if not credentials:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        body, signature = credentials.credentials.split(".", 1)
        expected = hmac.new(get_settings().auth_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected): raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload["exp"] < time.time(): raise ValueError
    except Exception as exc:
        raise HTTPException(status_code=401, detail="登录凭证无效或已过期") from exc
    member = db.query(TenantMember).filter_by(user_id=payload["sub"], tenant_id=payload["tenant_id"], status="active").first()
    user = db.query(User).filter_by(id=payload["sub"], status="active").first()
    tenant = db.query(Tenant).filter_by(id=payload["tenant_id"], status="active").first()
    if not member or not user or not tenant: raise HTTPException(status_code=401, detail="无权访问企业")
    if member.role == "owner":
        permissions = ("*",)
    else:
        assigned_role = db.query(TenantRole).filter_by(id=member.role_id, tenant_id=tenant.id, status="active").first() if member.role_id else None
        permissions = tuple(assigned_role.permissions or []) if assigned_role else ()
    auth = AuthContext(user.id, tenant.id, member.role, user.email, tenant.name, user.phone or "", user.is_platform_admin, permissions)
    if member.role != "owner":
        path = request.url.path
        route_permissions = (
            ("/api/director-projects/shooting-tasks", "shooting_tasks.access"),
            ("/api/dashboard", "dashboard.view"), ("/api/products", "products.access"),
            ("/api/knowledge", "knowledge.access"), ("/api/brand-visuals", "brand_visuals.access"),
            ("/api/design-skills", "design_skills.access"), ("/api/creative-projects", "creative_projects.access"),
            ("/api/detail-page-templates", "creative_projects.access"), ("/api/video-assets", "finished_assets.access"),
            ("/api/director-projects", "director.access"),
        )
        required = next((code for prefix, code in route_permissions if path.startswith(prefix)), None)
        if required and required not in permissions:
            raise HTTPException(status_code=403, detail="当前角色没有此功能权限")
    return auth
