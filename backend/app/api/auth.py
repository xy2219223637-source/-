from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import AuthContext, create_token, current_auth, hash_password, verify_password
from app.database import get_db
from app.models import Tenant, TenantMember, User
from app.schemas import AuthOut, ChangePasswordRequest, LoginRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def _response(user: User, tenant: Tenant, role: str) -> AuthOut:
    return AuthOut(access_token=create_token(user.id, tenant.id), user={"id": user.id, "name": user.name, "phone": user.phone or "", "role": role, "must_change_password": str(user.must_change_password).lower(), "is_platform_admin": str(user.is_platform_admin).lower()}, tenant={"id": tenant.id, "name": tenant.name, "code": tenant.code})


@router.post("/register", response_model=AuthOut)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> AuthOut:
    raise HTTPException(status_code=403, detail="暂不开放自主注册，请联系平台管理员创建企业账号")


@router.post("/login", response_model=AuthOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthOut:
    user = db.query(User).filter(User.phone == payload.phone.strip(), User.status == "active").first()
    if not user or not verify_password(payload.password, user.password_hash): raise HTTPException(status_code=401, detail="手机号或密码错误")
    member = db.query(TenantMember).filter_by(user_id=user.id, status="active").first()
    tenant = db.query(Tenant).filter_by(id=member.tenant_id, status="active").first() if member else None
    if not member or not tenant: raise HTTPException(status_code=403, detail="未加入可用企业")
    return _response(user, tenant, member.role)


@router.get("/me")
def me(auth: AuthContext = Depends(current_auth)) -> dict:
    return {"user_id": auth.user_id, "phone": auth.phone, "role": auth.role, "tenant_id": auth.tenant_id, "tenant_name": auth.tenant_name, "is_platform_admin": auth.is_platform_admin}


@router.post("/change-password")
def change_password(payload: ChangePasswordRequest, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter_by(id=auth.user_id).first()
    if not user or not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.commit()
    return {"ok": True}
