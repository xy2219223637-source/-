from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Tenant, TenantMember, User

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


def current_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: Session = Depends(get_db)) -> AuthContext:
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
    return AuthContext(user.id, tenant.id, member.role, user.email, tenant.name, user.phone or "", user.is_platform_admin)
