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


def enabled(service=Depends(get_billing_service)):
    if not service.settings.billing_enabled:
        fail("BILLING_DISABLED", "积分功能未启用", 404)
    return service


router = APIRouter(tags=["billing"], dependencies=[Depends(enabled)])


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


@router.post("/platform-admin/billing/price-rules", status_code=201)
def create_price(payload: PriceRequest, auth: AuthContext = Depends(platform_admin), service=Depends(enabled)):
    return service.create_price(auth, payload)


@router.get("/platform-admin/billing/price-rules")
def prices(auth: AuthContext = Depends(platform_admin), service=Depends(enabled),
           page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return service.list_prices(auth, page, page_size)


@router.get("/platform-admin/billing/price-rules/summary")
def price_summary(auth: AuthContext = Depends(platform_admin), service=Depends(enabled)):
    return service.price_summary(auth)


@router.post("/platform-admin/billing/price-rules/{price_id}/activate")
def activate_price(price_id: str, auth: AuthContext = Depends(platform_admin), service=Depends(enabled)):
    return service.activate_price(auth, price_id)


@router.post("/ai/mock/{kind}")
def mock_call(kind: str, payload: MockRequest, response: Response,
              auth: AuthContext = Depends(require_permission("billing.mock.use")), service=Depends(enabled),
              key: str = Header(alias="Idempotency-Key", min_length=36, max_length=36)):
    if not service.settings.billing_mock_enabled or not service.settings.billing_test_isolation or kind not in {"fixed", "text"}:
        fail("RESOURCE_NOT_FOUND", "测试功能未开放", 404)
    result, pending = service.run_mock(auth, "mock_"+kind, key, payload.text)
    response.status_code = 202 if pending else 200
    return result
