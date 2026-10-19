"""One current price per server-registered model; immutable audit and replay."""
import hashlib

from sqlalchemy import select, text

from app.models.billing import BillingModelPrice, BillingIdempotency
from app.services.billing.amounts import fail
from app.services.billing.registry import ModelRegistry
from app.services.billing.service import GrantEnterpriseCreditsService, digest, key_uuid, utc_iso


def model_price_lock(db, model_key, *, try_only=False):
    if db.get_bind().dialect.name == 'postgresql':
        lock = int.from_bytes(hashlib.sha256(('model-price:' + model_key).encode()).digest()[:8], 'big', signed=True)
        if try_only:
            if not db.scalar(text('SELECT pg_try_advisory_xact_lock(:lock)'), {'lock': lock}):
                fail('REQUEST_IN_PROGRESS', '相同保存请求尚未提交', 409)
        else:
            db.execute(text('SELECT pg_advisory_xact_lock(:lock)'), {'lock': lock})


class ModelPricingService(GrantEnterpriseCreditsService):
    def __init__(self, sessions=None, settings=None, clock=None, registry=None):
        super().__init__(sessions, settings, clock)
        self.registry = registry

    def model_registry(self):
        return self.registry if self.registry is not None else ModelRegistry.from_settings(self.settings)

    @staticmethod
    def pricing_result(row):
        return {'model_key': row.model_key, 'configuration': row.current_pricing_json, 'updated_at': utc_iso(row.updated_at)} if row else None

    def list_model_prices(self, auth, page=1, page_size=20, model_type=None):
        with self.sessions() as db:
            self.authorize(db, auth, platform=True)
            items = sorted(self.model_registry().items.values(), key=lambda item: item.model_key)
            if model_type:
                items = [item for item in items if model_type in item.model_types]
            total = len(items)
            items = items[(page - 1) * page_size:page * page_size]
            prices = {row.model_key: row for row in db.scalars(select(BillingModelPrice).where(BillingModelPrice.model_key.in_([item.model_key for item in items])))}
            return {'items': [item.public(self.pricing_result(prices.get(item.model_key))) for item in items],
                    'total': total, 'page': page, 'page_size': page_size}

    def get_model_price(self, auth, model_key):
        with self.sessions() as db:
            self.authorize(db, auth, platform=True)
            item = self.model_registry().get(model_key)
            return item.public(self.pricing_result(db.get(BillingModelPrice, model_key)))

    def put_model_price(self, auth, model_key, key, payload):
        key = key_uuid(key)
        body = payload.model_dump()
        fingerprint = digest({'actor_id': auth.user_id, 'model_key': model_key, 'body': body})
        with self.transaction() as db:
            # A separate operation/key lock handles different models using one key.
            model_price_lock(db, 'idempotency:' + auth.tenant_id + ':' + key, try_only=True)
            model_price_lock(db, model_key)
            self.authorize(db, auth, platform=True)
            prior = db.scalar(select(BillingIdempotency).where(BillingIdempotency.operation == 'MODEL_PRICE_PUT',
                              BillingIdempotency.tenant_id == auth.tenant_id, BillingIdempotency.key == key))
            if prior:
                if prior.actor_id != auth.user_id or prior.request_digest != fingerprint:
                    fail('IDEMPOTENCY_CONFLICT', '幂等键已绑定其他保存意图')
                return prior.response_json, True
            item = self.model_registry().get(model_key)
            if not item.implemented:
                fail('MODEL_NOT_IMPLEMENTED', '模型尚未实现', 422)
            if not item.configured:
                fail('MODEL_NOT_CONFIGURED', '模型配置不完整', 422)
            contract = item.usage_contract
            if payload.usage_mode == 'REAL_TOKENS':
                if not contract.real_metrics or {rate.metric for rate in payload.metrics} != set(contract.real_metrics):
                    fail('INVALID_PRICE', '计量方向必须与已登记适配器契约一致', 422)
            elif payload.conversion.source_metric not in contract.raw_metrics:
                fail('INVALID_PRICE', '折算来源未登记取证协议', 422)
            row = db.get(BillingModelPrice, model_key)
            before = row.current_pricing_json if row else None
            if row is None:
                row = BillingModelPrice(model_key=model_key, provider=item.provider, model_id=item.model_id)
                db.add(row)
            elif (row.provider, row.model_id) != (item.provider, item.model_id):
                fail('SERVICE_UNAVAILABLE', '模型身份冲突', 503)
            row.current_pricing_json, row.updated_by, row.updated_at = body, auth.user_id, self.clock()
            db.flush()
            result = self.pricing_result(row)
            self.audit(db, auth.tenant_id, auth.user_id, 'MODEL_PRICE_PUT', model_key, {'before': before, 'after': body})
            db.add(BillingIdempotency(operation='MODEL_PRICE_PUT', tenant_id=auth.tenant_id, key=key,
                                     request_digest=fingerprint, actor_id=auth.user_id, response_json=result))
            return result, False
