"""Trusted model calls share the existing reservation and credits ledger."""
from dataclasses import dataclass
from datetime import timedelta
from fractions import Fraction
import uuid

from sqlalchemy import func, select

from app.auth import AuthContext
from app.models.billing import BillingModelPrice, BillingOrder, CreditLedger
from app.permissions import ALL_USER_PERMISSIONS
from app.services.billing.amounts import MAX_INT, amount, credits, fail
from app.services.billing.model_pricing import model_price_lock
from app.services.billing.service import digest, utc_iso


class KnownNoUsage(Exception):
    """A trusted transport positively proves that no consumption occurred."""


@dataclass(frozen=True)
class BillingContext:
    tenant_id: str
    user_id: str
    business_key: str
    feature_code: str
    execution_mode: str


@dataclass(frozen=True)
class BoundedRequest:
    capability: str
    payload: dict
    maximum_usage: dict
    bound_evidence: str
    specification: str | None = None


@dataclass(frozen=True)
class AdapterOutcome:
    result: dict
    usage: dict | None
    supplier_cost: dict | None = None
    provider_request_id: str | None = None
    execution_status: str = 'SUCCEEDED'


@dataclass(frozen=True)
class MeteredOutcome:
    result: dict | None
    order_id: str
    execution_status: str
    usage: dict
    supplier_cost: dict
    provider_request_id: str | None


def unknown_usage():
    return dict(source='UNKNOWN', input_tokens=None, output_tokens=None, converted_tokens=None,
                raw_metric=None, raw_quantity=None)


def reject_unmetered(settings):
    """Legacy SDK paths have no durable payer intent; they cannot consume."""
    if settings.billing_enabled:
        fail('BILLING_CONTEXT_REQUIRED', '此调用尚未绑定可信消费意图，请通过受计量执行器调用', 422)


def usage_quantities(snapshot, usage):
    """Missing directions are unknown, never invented zero quantities."""
    names = {rate['metric'] for rate in snapshot['metrics']}
    if snapshot['usage_mode'] != 'REAL_TOKENS':
        fail('INVALID_USAGE', '折算适配器尚未接入', 422)
    if not isinstance(usage, dict) or set(usage) != names:
        fail('INVALID_USAGE', '适用计量项缺失或未知', 422)
    if any(type(value) is not int or not 0 <= value <= 10**9 for value in usage.values()):
        fail('INVALID_USAGE', '用量必须为受限非负整数', 422)
    return usage


def calculate_model_charge(snapshot, usage):
    quantities = usage_quantities(snapshot, usage)
    total = sum((Fraction(quantities[rate['metric']] * amount(rate['credits_per_unit']), rate['unit_size'])
                 for rate in snapshot['metrics']), Fraction(0))
    charge = (total.numerator + total.denominator - 1) // total.denominator
    if charge > MAX_INT:
        fail('INVALID_USAGE', '计费金额超出范围', 422)
    return charge


def normalize_usage(snapshot, usage):
    if usage is None:
        return unknown_usage(), None
    charge = calculate_model_charge(snapshot, usage)
    return {**unknown_usage(), 'source': 'REAL_TOKENS', **usage}, charge


class ModelMeteringMixin:
    def _context_auth(self, context):
        if not isinstance(context, BillingContext) or not all(isinstance(value, str) and value for value in
                (context.tenant_id, context.user_id, context.business_key, context.feature_code)):
            fail('BILLING_CONTEXT_REQUIRED', '模型调用缺少可信消费上下文', 422)
        if len(context.business_key) > 200 or context.feature_code not in ALL_USER_PERMISSIONS or context.feature_code == 'billing.mock.use':
            fail('FORBIDDEN', '未登记原业务权限或持久消费意图', 403)
        if not self.settings.billing_enabled:
            fail('BILLING_DISABLED', '积分功能未启用', 404)
        if context.execution_mode == 'REAL':
            # Ticket 06 will supply a verified report and durable real-attempt gate.
            fail('MOCK_GATE_REQUIRED', '完整 MOCK 验证报告尚未通过，禁止真实调用', 422)
        if context.execution_mode != 'MOCK' or not self.settings.billing_mock_enabled or not self.settings.billing_test_isolation:
            fail('FORBIDDEN', '模拟执行只能在受控隔离环境使用', 403)
        return AuthContext(context.user_id, context.tenant_id, '', '', '')

    def reserve_model(self, context, model_key, request):
        auth = self._context_auth(context)
        if not isinstance(request, BoundedRequest) or not request.bound_evidence:
            fail('USAGE_BOUND_UNKNOWN', '缺少可证明的用量上界', 422)
        request_id = uuid.uuid5(uuid.NAMESPACE_URL, 'model-billing:' + digest({
            'tenant': auth.tenant_id, 'business_key': context.business_key})).hex
        fingerprint = digest({'user': auth.user_id, 'model_key': model_key, 'mode': context.execution_mode, 'feature': context.feature_code,
                              'capability': request.capability, 'payload': request.payload, 'maximum': request.maximum_usage,
                              'bound_evidence': request.bound_evidence, 'specification': request.specification})
        with self.transaction() as db:
            model_price_lock(db, model_key)
            self.tenant_lock(db, auth.tenant_id)
            self.authorize(db, auth, context.feature_code)
            prior = db.scalar(select(BillingOrder).where(BillingOrder.request_id == request_id))
            if prior:
                if prior.user_id != auth.user_id:
                    fail('RESOURCE_NOT_FOUND', '业务消费意图不可见', 404)
                if prior.request_digest != fingerprint:
                    fail('IDEMPOTENCY_CONFLICT', '业务意图已绑定其他请求内容')
                return self.model_order_result(prior)
            item = self.model_registry().get(model_key)
            if not item.implemented:
                fail('MODEL_NOT_IMPLEMENTED', '模型尚未实现', 422)
            if not item.configured:
                fail('MODEL_NOT_CONFIGURED', '模型配置不完整', 422)
            if request.capability not in item.model_types:
                fail('PRICE_SPEC_MISMATCH', '模型不支持此能力', 422)
            price = db.get(BillingModelPrice, model_key)
            if price is None:
                fail('UNPRICED_MODEL', '模型尚未配置当前费率', 422)
            snapshot = price.current_pricing_json
            contract = item.usage_contract
            if (snapshot['usage_mode'] == 'REAL_TOKENS' and
                    (not contract.real_metrics or {rate['metric'] for rate in snapshot['metrics']} != set(contract.real_metrics))) or (
                    snapshot['usage_mode'] == 'FOLDED_TOKENS' and snapshot['conversion']['source_metric'] not in contract.raw_metrics):
                fail('UNPRICED_MODEL', '当前费率缺少匹配的可信适配器计量契约', 422)
            if (price.provider, price.model_id) != (item.provider, item.model_id):
                fail('SERVICE_UNAVAILABLE', '模型身份与费率不一致', 503)
            try:
                reservation = calculate_model_charge(snapshot, request.maximum_usage)
            except Exception:
                fail('USAGE_BOUND_UNKNOWN', '无法按当前计量契约确定用量上界', 422)
            account = self.account(db, auth.tenant_id)
            quota = self.quota(db, auth.tenant_id, auth.user_id)
            if quota is None:
                fail('QUOTA_NOT_CONFIGURED', '成员尚未配置消费额度')
            if account.balance_microcredits - account.reserved_microcredits < reservation:
                fail('INSUFFICIENT_CREDITS', '企业可用积分不足')
            if quota.limit_microcredits - quota.used_microcredits - quota.reserved_microcredits < reservation:
                fail('QUOTA_EXCEEDED', '成员本月剩余额度不足')
            account.reserved_microcredits += reservation
            quota.reserved_microcredits += reservation
            row = BillingOrder(request_id=request_id, tenant_id=auth.tenant_id, user_id=auth.user_id,
                quota_id=quota.id, period_key=quota.period_key, feature_code=context.feature_code,
                model_price_id=model_key, execution_mode=context.execution_mode,
                pricing_snapshot_json={'configuration': snapshot, 'provider': item.provider, 'model_id': item.model_id,
                                       'capability': request.capability, 'specification': request.specification,
                                       'business_key': context.business_key},
                request_digest=fingerprint, reserved_microcredits=reservation, max_usage_json=request.maximum_usage,
                metering_json={'provider': item.provider, 'model_id': item.model_id, 'capability': request.capability,
                    'usage': unknown_usage(), 'supplier_cost': {'status': 'NOT_INCURRED', 'amount': None, 'currency': None},
                    'provider_request_id': None, 'execution_status': 'NOT_STARTED'},
                expires_at=self.clock() + timedelta(seconds=self.settings.billing_reservation_seconds))
            db.add(row); db.flush()
            self.audit(db, auth.tenant_id, auth.user_id, 'MODEL_RESERVE', row.id,
                       {'reserved': reservation, 'model_key': model_key, 'mode': context.execution_mode})
            return self.model_order_result(row)

    @staticmethod
    def model_order_result(row):
        meter = row.metering_json
        return {'id': row.id, 'request_id': row.request_id, 'tenant_id': row.tenant_id, 'user_id': row.user_id,
                'model_key': row.model_price_id, 'provider': row.pricing_snapshot_json['provider'],
                'model_id': row.pricing_snapshot_json['model_id'],
                'execution_mode': row.execution_mode, 'status': row.status, 'reconciliation_status': row.reconciliation_status,
                'usage': meter['usage'], 'supplier_cost': meter['supplier_cost'],
                'charged_credits': credits(row.charged_microcredits),
                'reserved_credits': credits(row.reserved_microcredits if row.status == 'RESERVED' else 0),
                'pricing_snapshot': row.pricing_snapshot_json['configuration'], 'provider_request_id': meter['provider_request_id'],
                'created_at': utc_iso(row.created_at)}

    def record_model_outcome(self, order_id, outcome):
        with self.transaction() as db:
            row, _, _ = self.locked_order(db, order_id)
            if row.status != 'RESERVED' or row.execution_status != 'STARTED':
                fail('INVALID_ORDER_STATE', '模型订单不可记录执行结果')
            if not isinstance(outcome, AdapterOutcome):
                fail('INVALID_USAGE', '适配器响应不符合计量契约', 422)
            usage, charge = normalize_usage(row.pricing_snapshot_json['configuration'], outcome.usage)
            if outcome.execution_status not in ('SUCCEEDED', 'FAILED', 'UNKNOWN'):
                fail('INVALID_USAGE', '适配器任务状态无效', 422)
            # MOCK is a separate execution dimension, never a vendor invoice.
            cost = {'status': 'NOT_INCURRED', 'amount': None, 'currency': None}
            reference = outcome.provider_request_id
            if reference is not None and (not isinstance(reference, str) or len(reference) > 200):
                fail('INVALID_USAGE', '厂商请求标识无效', 422)
            row.metering_json = {**row.metering_json, 'usage': usage, 'supplier_cost': cost,
                                 'provider_request_id': reference, 'execution_status': outcome.execution_status}
            row.usage_json = outcome.usage or {}
            row.result_json = outcome.result
            row.execution_status = 'RESULT_READY'
            if charge is None:
                row.reconciliation_status, row.error_code = 'NEEDS_REVIEW', 'USAGE_UNKNOWN'
            self.audit(db, row.tenant_id, 'billing', 'MODEL_RESULT_READY', row.id,
                       {'usage_source': usage['source']}, internal=True)

    def settle_model(self, order_id):
        with self.transaction() as db:
            row, account, quota = self.locked_order(db, order_id)
            if row.status in ('SETTLED', 'CANCELED'):
                return self.model_order_result(row)
            if row.execution_status != 'RESULT_READY':
                fail('INVALID_ORDER_STATE', '模型结果尚未持久化')
            if row.metering_json['usage']['source'] == 'UNKNOWN':
                return self.model_order_result(row)
            charge = calculate_model_charge(row.pricing_snapshot_json['configuration'], row.usage_json)
            if charge > row.reserved_microcredits or any(value > row.max_usage_json[name] for name, value in row.usage_json.items()):
                row.reconciliation_status, row.error_code = 'NEEDS_REVIEW', 'USAGE_EXCEEDS_RESERVATION'
                self.audit(db, row.tenant_id, 'billing', 'MODEL_USAGE_EXCEEDS_BOUND', row.id, internal=True)
                return self.model_order_result(row)
            before = account.balance_microcredits
            account.balance_microcredits -= charge
            account.reserved_microcredits -= row.reserved_microcredits
            quota.used_microcredits += charge
            quota.reserved_microcredits -= row.reserved_microcredits
            row.status, row.reconciliation_status = 'SETTLED', 'RESOLVED'
            row.charged_microcredits, row.settled_at = charge, self.clock()
            row.settlement_digest = digest({'usage': row.usage_json, 'result': row.result_json})
            db.add(CreditLedger(tenant_id=row.tenant_id, account_id=account.id, order_id=row.id, event_type='CHARGE',
                delta_microcredits=-charge, balance_before_microcredits=before, balance_after_microcredits=account.balance_microcredits,
                business_key='CHARGE:' + row.id, actor_type='USER', actor_id=row.user_id, reason=row.feature_code))
            self.audit(db, row.tenant_id, 'billing', 'MODEL_SETTLE', row.id, {'charge': charge}, internal=True)
            return self.model_order_result(row)

    def execute_metered(self, context, model_key, request, *, adapter):
        self._context_auth(context)
        if not isinstance(request, BoundedRequest):
            fail('USAGE_BOUND_UNKNOWN', '缺少可证明的用量上界', 422)
        if getattr(adapter, 'execution_mode', None) != context.execution_mode or request.capability not in getattr(adapter, 'capabilities', ()):
            fail('PRICE_SPEC_MISMATCH', '适配器与执行模式或能力不一致', 422)
        validator = getattr(adapter, 'validate_request', None)
        if validator is not None:
            validator(request)
        order = self.reserve_model(context, model_key, request)
        if order['status'] == 'RESERVED' and self.claim(order['id']):
            try:
                outcome = adapter.execute(request)
            except KnownNoUsage:
                self.model_execution_failed(order['id'], 'FAILED')
                self.cancel(order['id'], no_usage_proven=True)
            except Exception:
                self.model_execution_failed(order['id'], 'UNKNOWN')
                self.mark_review(order['id'], 'EXECUTION_UNCERTAIN')
            else:
                try:
                    self.record_model_outcome(order['id'], outcome)
                    self.settle_model(order['id'])
                except Exception:
                    self.mark_review(order['id'], 'SETTLEMENT_PENDING')
                    raise
        with self.sessions() as db:
            row = db.get(BillingOrder, order['id'])
            meter = row.metering_json
            return MeteredOutcome(row.result_json or None, row.id, meter.get('execution_status', row.execution_status),
                                  meter['usage'], meter['supplier_cost'], meter['provider_request_id'])

    def model_execution_failed(self, order_id, execution_status):
        with self.transaction() as db:
            row, _, _ = self.locked_order(db, order_id)
            if row.status == 'RESERVED':
                row.metering_json = {**row.metering_json, 'execution_status': execution_status}

    def model_orders(self, auth, page=1, page_size=20, *, enterprise=False):
        if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
            fail('VALIDATION_ERROR', '分页参数无效', 422)
        with self.sessions() as db:
            self.authorize(db, auth, 'billing.view' if enterprise else None)
            query = select(BillingOrder).where(BillingOrder.tenant_id == auth.tenant_id, BillingOrder.model_price_id.is_not(None))
            if not enterprise:
                query = query.where(BillingOrder.user_id == auth.user_id)
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            rows = db.scalars(query.order_by(BillingOrder.created_at.desc(), BillingOrder.id.desc())
                              .offset((page - 1) * page_size).limit(page_size)).all()
            return dict(items=[self.model_order_result(row) for row in rows], total=total, page=page, page_size=page_size)

    def get_model_order(self, auth, order_id, *, enterprise=False):
        with self.sessions() as db:
            self.authorize(db, auth, 'billing.view' if enterprise else None)
            query = select(BillingOrder).where(BillingOrder.id == order_id, BillingOrder.tenant_id == auth.tenant_id,
                                                BillingOrder.model_price_id.is_not(None))
            if not enterprise:
                query = query.where(BillingOrder.user_id == auth.user_id)
            row = db.scalar(query)
            if row is None:
                fail('RESOURCE_NOT_FOUND', '模型订单不存在', 404)
            return self.model_order_result(row)
