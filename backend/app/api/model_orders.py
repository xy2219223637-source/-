"""Four read-only model order queries, with live member/tenant authorization."""
from fastapi import APIRouter, Depends, Path, Query

from app.auth import AuthContext, current_auth
from app.api.model_pricing_errors import ModelPricingRoute
from app.schemas.model_orders import ModelOrder, ModelOrderPage
from app.schemas.model_pricing import ERROR_RESPONSES
from app.services.billing.amounts import fail
from app.services.billing.service import get_billing_service


def enabled(auth: AuthContext = Depends(current_auth), service=Depends(get_billing_service)):
    if not service.settings.billing_enabled:
        fail('BILLING_DISABLED', '积分功能未启用', 404)
    return service


router = APIRouter(route_class=ModelPricingRoute, tags=['billing'])


@router.get('/me/billing/model-orders', response_model=ModelOrderPage, responses=ERROR_RESPONSES, operation_id='listOwnModelOrders')
def own_orders(auth: AuthContext = Depends(current_auth), service=Depends(enabled),
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return service.model_orders(auth, page, page_size)


@router.get('/me/billing/model-orders/{order_id}', response_model=ModelOrder, responses=ERROR_RESPONSES, operation_id='getOwnModelOrder')
def own_order(order_id: str = Path(pattern='^[0-9a-f]{32}$'), auth: AuthContext = Depends(current_auth), service=Depends(enabled)):
    return service.get_model_order(auth, order_id)


@router.get('/tenant-admin/billing/model-orders', response_model=ModelOrderPage, responses=ERROR_RESPONSES, operation_id='listTenantModelOrders')
def tenant_orders(auth: AuthContext = Depends(current_auth), service=Depends(enabled),
                  page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return service.model_orders(auth, page, page_size, enterprise=True)


@router.get('/tenant-admin/billing/model-orders/{order_id}', response_model=ModelOrder, responses=ERROR_RESPONSES, operation_id='getTenantModelOrder')
def tenant_order(order_id: str = Path(pattern='^[0-9a-f]{32}$'), auth: AuthContext = Depends(current_auth), service=Depends(enabled)):
    return service.get_model_order(auth, order_id, enterprise=True)
