"""Authenticated management and self-service queries. Never accepts settlement."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import AwareDatetime

from app.auth import AuthContext, current_auth
from app.api.admin import platform_admin
from app.permissions import require_permission
from app.schemas.billing import GrantRequest, QuotaRequest, PriceRequest, MockRequest
from app.services.billing.amounts import fail
from app.services.billing.service import get_billing_service
from app.services.redis_client import get_redis, redis_key
from app.schemas.model_pricing import PricingInput, Pricing, Model, ModelPage, ERROR_RESPONSES
from app.api.model_pricing_errors import ModelPricingRoute
from typing import Literal


def enabled(service=Depends(get_billing_service)):
    if not service.settings.billing_enabled:
        fail("BILLING_DISABLED", "积分功能未启用", 404)
    return service


router = APIRouter(tags=["billing"], route_class=ModelPricingRoute)


class GrantRateLimiter:
    def check(self, auth, tenant_id, limit):
        # One Lua operation sets TTL and increments. No unbounded key after crash.
        try:
            client = get_redis()
            if client is None:
                raise RuntimeError("Redis unavailable")
            script = "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n"
            count = client.eval(script, 1, redis_key(f"billing:grant:{auth.user_id}:{tenant_id}"))
        except Exception:
            fail("RATE_LIMIT_UNAVAILABLE", "管理限流服务暂不可用", 503)
        if count > limit:
            fail("RATE_LIMITED", "发放操作过于频繁", 429)


def get_grant_limiter():
    return GrantRateLimiter()


@router.post("/platform-admin/tenants/{tenant_id}/credits/grants", status_code=201)
def grant(tenant_id: str, payload: GrantRequest, response: Response,
          auth: AuthContext = Depends(platform_admin), service=Depends(enabled),
          key: str = Header(alias="Idempotency-Key", min_length=36, max_length=36), limiter=Depends(get_grant_limiter)):
    limiter.check(auth, tenant_id, service.settings.billing_grant_rate_limit)
    result, replay = service.grant(auth, tenant_id, key, payload.amount_credits, payload.reason)
    response.status_code = 200 if replay else 201
    return result


@router.get("/platform-admin/tenants/{tenant_id}/credits/grants")
def grants(tenant_id: str, auth: AuthContext = Depends(platform_admin), service=Depends(enabled),
           page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
           date_from: AwareDatetime | None = None, date_to: AwareDatetime | None = None, actor_id: str | None = Query(None, max_length=64)):
    return service.ledger(auth, tenant_id, grants=True, page=page, page_size=page_size, date_from=date_from, date_to=date_to, actor_id=actor_id)


@router.post("/platform-admin/tenants/{tenant_id}/credits/revocations", status_code=201)
def revoke(tenant_id: str, payload: GrantRequest, response: Response,
           auth: AuthContext = Depends(platform_admin), service=Depends(enabled),
           key: str = Header(alias="Idempotency-Key", min_length=36, max_length=36), limiter=Depends(get_grant_limiter)):
    limiter.check(auth, tenant_id, service.settings.billing_grant_rate_limit)
    result, replay = service.revoke(auth, tenant_id, key, payload.amount_credits, payload.reason)
    response.status_code = 200 if replay else 201
    return result


@router.get("/platform-admin/tenants/{tenant_id}/credits")
def platform_balance(tenant_id: str, auth: AuthContext = Depends(platform_admin), service=Depends(enabled)):
    return service.balance(auth, tenant_id)


@router.get("/platform-admin/tenants/{tenant_id}/credits/adjustments")
def adjustments(tenant_id: str, auth: AuthContext = Depends(platform_admin), service=Depends(enabled),
                page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return service.ledger(auth, tenant_id, grants=True, adjustments=True, page=page, page_size=page_size)


@router.get("/tenant-admin/billing/credits")
def balance(auth: AuthContext = Depends(require_permission("billing.view")), service=Depends(enabled)):
    return service.balance(auth)


@router.put("/tenant-admin/billing/members/{user_id}/quota")
def quota(user_id: str, payload: QuotaRequest, auth: AuthContext = Depends(require_permission("billing.quota.manage")), service=Depends(enabled)):
    return service.set_quota(auth, user_id, payload.total_limit_credits, payload.reason)


@router.get("/tenant-admin/billing/members/usage")
def members(auth: AuthContext = Depends(require_permission("billing.view")), service=Depends(enabled),
            page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return service.members(auth, page, page_size)


@router.get("/tenant-admin/billing/ledger")
def ledger(auth: AuthContext = Depends(require_permission("billing.view")), service=Depends(enabled),
           page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
           date_from: AwareDatetime | None = None, date_to: AwareDatetime | None = None):
    return service.ledger(auth, page=page, page_size=page_size, date_from=date_from, date_to=date_to)


@router.get("/me/billing/quota")
def own_quota(auth: AuthContext = Depends(current_auth), service=Depends(enabled)):
    return service.own_quota(auth)


@router.get("/me/billing/config")
def configuration(auth: AuthContext = Depends(current_auth), service=Depends(enabled)):
    return {"mock_enabled": service.settings.billing_mock_enabled and service.settings.billing_test_isolation,
            "test_isolation": service.settings.billing_test_isolation}


@router.get("/me/billing/orders")
def own_orders(auth: AuthContext = Depends(current_auth), service=Depends(enabled),
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return service.own_orders(auth, page, page_size)


@router.post("/platform-admin/billing/price-rules", status_code=410, responses=ERROR_RESPONSES, operation_id='retiredCreatePrice')
def create_price(auth: AuthContext = Depends(platform_admin), service=Depends(enabled)):
    fail('LEGACY_ENDPOINT_RETIRED', '版本创建入口已停用，请保存模型当前费率', 410)


@router.get("/platform-admin/billing/price-rules", response_model=ModelPage, responses=ERROR_RESPONSES, operation_id='listModelPrices')
def prices(auth: AuthContext = Depends(platform_admin), service=Depends(enabled),
           page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
           model_type: Literal['TEXT', 'VISION', 'IMAGE', 'EMBEDDING', 'TTS', 'ASR', 'VIDEO'] | None = None):
    return service.list_model_prices(auth, page, page_size, model_type)


@router.get("/platform-admin/billing/price-rules/summary", status_code=410, responses=ERROR_RESPONSES, operation_id='retiredPriceSummary')
def price_summary(auth: AuthContext = Depends(platform_admin), service=Depends(enabled)):
    fail('LEGACY_ENDPOINT_RETIRED', '版本汇总入口已停用', 410)


@router.post("/platform-admin/billing/price-rules/{price_id}/activate", status_code=410, responses=ERROR_RESPONSES, operation_id='retiredActivatePrice')
def activate_price(price_id: str, auth: AuthContext = Depends(platform_admin), service=Depends(enabled)):
    fail('LEGACY_ENDPOINT_RETIRED', '版本启用入口已停用', 410)


@router.get('/platform-admin/billing/price-rules/{model_key}', response_model=Model, responses=ERROR_RESPONSES, operation_id='getModelPrice')
def model_price(model_key: str, auth: AuthContext = Depends(platform_admin), service=Depends(enabled)):
    return service.get_model_price(auth, model_key)


@router.put('/platform-admin/billing/price-rules/{model_key}', response_model=Pricing, responses=ERROR_RESPONSES, operation_id='putModelPrice')
def save_model_price(model_key: str, payload: PricingInput, response: Response,
                     auth: AuthContext = Depends(platform_admin), service=Depends(enabled),
                     key: str = Header(alias='Idempotency-Key', min_length=36, max_length=36)):
    result, replay = service.put_model_price(auth, model_key, key, payload)
    if replay:
        response.headers['Idempotency-Replayed'] = 'true'
    return result


@router.post("/ai/mock/{kind}", status_code=410, responses=ERROR_RESPONSES, operation_id='retiredMockCall')
def mock_call(kind: str, auth: AuthContext = Depends(require_permission("billing.mock.use")), service=Depends(enabled)):
    fail('LEGACY_ENDPOINT_RETIRED', '旧 mock 计价入口已停用；同模型离线验证由受控执行器运行', 410)


from app.api.model_orders import router as model_orders_router
router.include_router(model_orders_router)
