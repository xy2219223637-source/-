from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.database import get_db
from app.schemas.qianchuan_plans import (
    QianchuanAwemePage, QianchuanDeliveryPlan, QianchuanPlanCapabilities,
    QianchuanPlanDraftCreate, QianchuanProductPage,
)
from app.services.qianchuan.service import QianchuanService, get_qianchuan_service
from app.services.qianchuan_plan.catalog import QianchuanCatalogService
from app.services.qianchuan_plan.plans import QianchuanPlanService


router = APIRouter(prefix="/qianchuan", tags=["千川全域计划"])
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get("/plan-capabilities", response_model=QianchuanPlanCapabilities)
def plan_capabilities(
    response: Response,
    binding_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    auth_service: QianchuanService = Depends(get_qianchuan_service),
):
    response.headers.update(NO_STORE)
    return QianchuanCatalogService(auth_service).capabilities(
        db, auth, str(binding_id) if binding_id else None)


@router.get("/accounts/{binding_id}/awemes", response_model=QianchuanAwemePage)
def account_awemes(
    binding_id: UUID,
    response: Response,
    keyword: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    auth_service: QianchuanService = Depends(get_qianchuan_service),
):
    response.headers.update(NO_STORE)
    return QianchuanCatalogService(auth_service).awemes(
        db, auth, str(binding_id), keyword=keyword, page=page, page_size=page_size)


@router.get("/accounts/{binding_id}/products", response_model=QianchuanProductPage)
def account_products(
    binding_id: UUID,
    response: Response,
    aweme_id: str | None = Query(default=None, pattern=r"^[1-9][0-9]{0,19}$"),
    keyword: str = Query(default="", max_length=100),
    cursor: int | None = Query(default=None, ge=0),
    page_size: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    auth_service: QianchuanService = Depends(get_qianchuan_service),
):
    response.headers.update(NO_STORE)
    return QianchuanCatalogService(auth_service).products(
        db, auth, str(binding_id), aweme_id=aweme_id, keyword=keyword,
        cursor=cursor, page_size=page_size)


@router.post("/plans/drafts", response_model=QianchuanDeliveryPlan)
def create_plan_draft(
    payload: QianchuanPlanDraftCreate,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    auth_service: QianchuanService = Depends(get_qianchuan_service),
):
    response.headers.update(NO_STORE)
    return QianchuanPlanService(auth_service).create_draft(db, auth, payload)
