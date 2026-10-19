"""Offline model consumption through the trusted executor and query boundaries."""
from datetime import datetime, timezone
import uuid
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading

from fastapi import HTTPException

from test_billing_management import BillingFixture


class ModelMeteringTests(BillingFixture):
    def setUp(self):
        super().setUp()
        from app.services.billing.orders import BillingOrderService
        from app.services.billing.registry import ModelRegistry, ModelRegistration, UsageContract
        self.model = ModelRegistration('offline:text', 'same-text-vision', ('TEXT', 'VISION'), True,
                                       usage_contract=UsageContract(('input_tokens', 'output_tokens'), (), 'tests/fixture'))
        self.service = BillingOrderService(self.sessions, self.settings, lambda: self.time or datetime.now(timezone.utc),
                                           ModelRegistry([self.model]))
        self.grant('1000')
        self.quota(value='100')
        self.quota('member2', '100')
        self.price()

    def price(self, rate='1.0000'):
        from app.schemas.model_pricing import PricingInput
        return self.service.put_model_price(self.auth(), self.model.model_key, str(uuid.uuid4()), PricingInput(**{
            'usage_mode': 'REAL_TOKENS', 'metrics': [
                {'metric': 'input_tokens', 'unit_size': 1000, 'credits_per_unit': rate},
                {'metric': 'output_tokens', 'unit_size': 1000, 'credits_per_unit': '3.0000'}], 'conversion': None}))

    def request(self, key='intent:entity-1:generate:1', capability='TEXT', **kwargs):
        from app.services.billing.metering import BillingContext, BoundedRequest
        return (BillingContext('a', 'member', key, 'director.access', 'MOCK'), self.model.model_key,
                BoundedRequest(capability, {'prompt': 'fixture'}, {'input_tokens': 3000, 'output_tokens': 2000},
                               'tests/controlled-token-bound', **kwargs))

    def test_text_success_uses_shared_account_member_and_unique_ledger(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        result = self.service.execute_metered(*self.request(), adapter=TextVisionMockAdapter())
        order = self.service.get_model_order(self.auth('member'), result.order_id)
        self.assertEqual(order['charged_credits'], '5.0000')
        self.assertEqual(order['execution_mode'], 'MOCK')
        self.assertEqual(order['usage']['input_tokens'], 2000)
        self.assertEqual(order['supplier_cost'], {'status': 'NOT_INCURRED', 'amount': None, 'currency': None})
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '995.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '5.0000')
        self.assertEqual(self.service.own_quota(self.auth('member2'))['used_credits'], '0.0000')
        rows = self.service.ledger(self.auth())['items']
        charges = [row for row in rows if row['event_type'] == 'CHARGE']
        self.assertEqual([(row['order_id'], row['delta_credits']) for row in charges], [(result.order_id, '-5.0000')])
        self.assertTrue(self.service.check_accounts()['ok'])

    def test_four_order_queries_enforce_self_tenant_and_domain_permissions(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        result = self.service.execute_metered(*self.request(), adapter=TextVisionMockAdapter())
        own = '/api/me/billing/model-orders'
        tenant = '/api/tenant-admin/billing/model-orders'
        response = self.client.get(own, headers=self.headers('member'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['items'][0]['id'], result.order_id)
        self.assertEqual(self.client.get(own+'/'+result.order_id, headers=self.headers('member')).json(), response.json()['items'][0])
        self.assertEqual(self.client.get(own, headers=self.headers('member2')).json()['total'], 0)
        for path, user, expected in ((own+'/'+result.order_id, 'member2', 404), (tenant+'/'+result.order_id, 'other', 404),
                                     (tenant, 'member', 403), (tenant+'/'+result.order_id, 'member', 403),
                                     (tenant+'/'+result.order_id, 'owner', 200)):
            self.assertEqual(self.client.get(path, headers=self.headers(user)).status_code, expected)
        for path in (own, tenant):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()['detail']['code'], 'UNAUTHENTICATED')
            self.assertTrue(response.json()['detail']['request_id'])
        for suffix in ('?page=0', '?page_size=101'):
            response = self.client.get(own+suffix, headers=self.headers('member'))
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['detail']['code'], 'VALIDATION_ERROR')
        self.settings.billing_enabled = False
        self.assertEqual(self.client.get(own, headers=self.headers('member')).json()['detail']['code'], 'BILLING_DISABLED')

    def test_vision_and_replay_share_model_price_but_never_reinvoke(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        class CountTransport(TextVisionMockAdapter):
            calls = 0
            def execute(adapter, request):
                adapter.calls += 1
                return super().execute(request)
        adapter = CountTransport()
        first = self.service.execute_metered(*self.request(capability='VISION'), adapter=adapter)
        replay = self.service.execute_metered(*self.request(capability='VISION'), adapter=adapter)
        order = self.service.get_model_order(self.auth('member'), first.order_id)
        self.assertEqual((first.order_id, adapter.calls), (replay.order_id, 1))
        self.assertEqual((order['charged_credits'], order['usage']['input_tokens']), ('2.5000', 1000))
        self.assertEqual(order['model_key'], self.model.model_key)
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(*self.request(), adapter=adapter)
        self.assertEqual(error.exception.detail['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 1)
        self.assertEqual(self.client.get('/api/me/billing/orders', headers=self.headers('member')).json()['total'], 0)
        with self.assertRaises(HTTPException) as conflict:
            self.service.settle(first.order_id, {'input_tokens': 1, 'output_tokens': 1}, {'changed': True})
        self.assertEqual(conflict.exception.detail['code'], 'IDEMPOTENCY_CONFLICT')

    def test_reservation_snapshot_survives_price_change_during_transport(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        class RepriceTransport(TextVisionMockAdapter):
            def execute(adapter, request):
                self.price('2.0000')
                return super().execute(request)
        old = self.service.execute_metered(*self.request(), adapter=RepriceTransport())
        new = self.service.execute_metered(*self.request(key='intent:entity-1:generate:2'), adapter=TextVisionMockAdapter())
        self.assertEqual(self.service.get_model_order(self.auth('member'), old.order_id)['charged_credits'], '5.0000')
        self.assertEqual(self.service.get_model_order(self.auth('member'), new.order_id)['charged_credits'], '7.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '12.0000')
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '988.0000')

    def test_unknown_timeout_and_over_bound_retain_reservation_no_charge(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        from app.services.billing.metering import AdapterOutcome
        class Controlled(TextVisionMockAdapter):
            def execute(adapter, request):
                if request.payload['case'] == 'timeout':
                    raise TimeoutError('synthetic transport timeout')
                if request.payload['case'] == 'over':
                    # Total charge remains below reservation, but one direction exceeds its bound.
                    return AdapterOutcome({'ok': True}, {'input_tokens': 3001, 'output_tokens': 0})
                return AdapterOutcome({'ok': True}, None)
        for case in ('unknown', 'timeout', 'over'):
            context, key, request = self.request(key='intent:fixture:'+case+':1')
            request = replace(request, payload={'case': case})
            result = self.service.execute_metered(context, key, request, adapter=Controlled())
            order = self.service.get_model_order(self.auth('member'), result.order_id)
            self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
            self.assertEqual((order['charged_credits'], order['reserved_credits']), ('0.0000', '9.0000'))
            if case != 'over':
                self.assertEqual(order['usage']['source'], 'UNKNOWN')
                self.assertIsNone(order['usage']['input_tokens'])
            before = self.service.model_orders(self.auth('member'))['total']
            self.service.execute_metered(context, key, request, adapter=Controlled())
            self.assertEqual(self.service.model_orders(self.auth('member'))['total'], before)
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '1000.0000')
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '27.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.0000')
        self.assertFalse(any(row['event_type'] == 'CHARGE' for row in self.service.ledger(self.auth())['items']))
        self.assertTrue(self.service.check_accounts()['ok'])

    def test_proven_no_consumption_cancels_zero_is_distinct_from_unknown(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        from app.services.billing.metering import AdapterOutcome, KnownNoUsage
        class NoUsage(TextVisionMockAdapter):
            def execute(adapter, request):
                raise KnownNoUsage()
        class ConfirmedZero(TextVisionMockAdapter):
            def execute(adapter, request):
                return AdapterOutcome({'ok': True}, {'input_tokens': 0, 'output_tokens': 0})
        canceled = self.service.execute_metered(*self.request(), adapter=NoUsage())
        order = self.service.get_model_order(self.auth('member'), canceled.order_id)
        self.assertEqual((order['status'], order['reserved_credits']), ('CANCELED', '0.0000'))
        zero = self.service.execute_metered(*self.request(key='intent:zero:generate:1'), adapter=ConfirmedZero())
        order = self.service.get_model_order(self.auth('member'), zero.order_id)
        self.assertEqual((order['status'], order['usage']['input_tokens']), ('SETTLED', 0))
        self.assertEqual(order['usage']['source'], 'REAL_TOKENS')
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '0.0000')
        charges = [row for row in self.service.ledger(self.auth())['items'] if row['event_type'] == 'CHARGE']
        self.assertEqual(len(charges), 1)
        self.assertEqual((charges[0]['order_id'], charges[0]['delta_credits']), (zero.order_id, '0.0000'))

    def test_missing_context_permissions_price_bound_and_credits_never_call_transport(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        from app.services.billing.registry import ModelRegistration, UsageContract
        class ForbiddenTransport(TextVisionMockAdapter):
            def execute(adapter, request):
                raise AssertionError('MUST NOT REACH TRANSPORT')
        context, key, request = self.request()
        self.service.registry.register(ModelRegistration('offline:text', 'unpriced', ('TEXT',), True,
            usage_contract=UsageContract(('input_tokens', 'output_tokens'), (), 'tests/fixture')))
        unpriced = next(item.model_key for item in self.service.registry.items.values() if item.model_id == 'unpriced')
        for ctx, model, bounded, code in (
                (None, key, request, 'BILLING_CONTEXT_REQUIRED'),
                (replace(context, user_id='limited'), key, request, 'FORBIDDEN'),
                (replace(context, execution_mode='REAL'), key, request, 'MOCK_GATE_REQUIRED'),
                (context, unpriced, request, 'UNPRICED_MODEL'),
                (context, key, replace(request, bound_evidence=''), 'USAGE_BOUND_UNKNOWN'),
                (context, key, replace(request, maximum_usage={'input_tokens': 0}), 'USAGE_BOUND_UNKNOWN')):
            with self.assertRaises(HTTPException) as error:
                self.service.execute_metered(ctx, model, bounded, adapter=ForbiddenTransport())
            self.assertEqual(error.exception.detail['code'], code)
        self.quota(value='1')
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(context, key, request, adapter=ForbiddenTransport())
        self.assertEqual(error.exception.detail['code'], 'QUOTA_EXCEEDED')
        self.quota(value='100')
        self.service.revoke(self.auth(), 'a', str(uuid.uuid4()), '999', 'fixture')
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(context, key, request, adapter=ForbiddenTransport())
        self.assertEqual(error.exception.detail['code'], 'INSUFFICIENT_CREDITS')
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)

    def test_concurrent_replay_calls_transport_once_and_returns_same_order(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        class Counter(TextVisionMockAdapter):
            calls = 0
            lock = threading.Lock()
            def execute(adapter, request):
                with adapter.lock:
                    adapter.calls += 1
                return super().execute(request)
        adapter = Counter()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.service.execute_metered(*self.request(), adapter=adapter), range(3)))
        self.assertEqual(len({result.order_id for result in results}), 1)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '995.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '5.0000')

    def test_ledger_storage_fault_rolls_back_then_recovery_uses_persisted_result(self):
        from sqlalchemy import event
        from app.services.billing.text_adapter import TextVisionMockAdapter
        class Counter(TextVisionMockAdapter):
            calls = 0
            def execute(adapter, request):
                adapter.calls += 1
                return super().execute(request)
        adapter = Counter()
        def fault(conn, cursor, statement, parameters, context, many):
            if statement.startswith('INSERT INTO credit_ledger'):
                raise RuntimeError('synthetic ledger storage failure')
        event.listen(self.engine, 'before_cursor_execute', fault)
        try:
            with self.assertRaises(RuntimeError):
                self.service.execute_metered(*self.request(), adapter=adapter)
        finally:
            event.remove(self.engine, 'before_cursor_execute', fault)
        order = self.service.model_orders(self.auth('member'))['items'][0]
        self.assertEqual((order['status'], order['reconciliation_status'], order['usage']['input_tokens']), ('RESERVED', 'NEEDS_REVIEW', 2000))
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '1000.0000')
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '9.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.0000')
        self.assertFalse(any(row['event_type'] == 'CHARGE' for row in self.service.ledger(self.auth())['items']))
        self.time = datetime.now(timezone.utc) + timedelta(hours=1)
        self.service.reconcile(recover=True)
        self.service.execute_metered(*self.request(), adapter=adapter)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.service.get_model_order(self.auth('member'), order['id'])['status'], 'SETTLED')
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '995.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '5.0000')
        self.assertTrue(self.service.check_accounts()['ok'])

    def test_internal_old_mock_entrypoint_is_retired_without_consumption(self):
        for feature in ('mock_text', 'mock_fixed'):
            with self.assertRaises(HTTPException) as error:
                self.service.run_mock(self.auth('member'), feature, str(uuid.uuid4()), 'fixture')
            self.assertEqual(error.exception.detail['code'], 'LEGACY_ENDPOINT_RETIRED')
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '1000.0000')
        self.assertEqual(self.service.own_orders(self.auth('member'))['total'], 0)

    def test_adapter_specification_validation_precedes_reservation_and_is_immutable(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        from app.services.billing.amounts import fail
        from app.models.billing import BillingOrder
        from sqlalchemy import text
        class Validated(TextVisionMockAdapter):
            def validate_request(adapter, request):
                if request.specification != 'offline-text-v1':
                    fail('PRICE_SPEC_MISMATCH', 'unsupported controlled specification', 422)
        context, model, request = self.request()
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(context, model, request, adapter=Validated())
        self.assertEqual(error.exception.detail['code'], 'PRICE_SPEC_MISMATCH')
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)
        result = self.service.execute_metered(context, model, replace(request, specification='offline-text-v1'), adapter=Validated())
        with self.sessions() as db:
            row = db.get(BillingOrder, result.order_id)
            self.assertEqual(row.pricing_snapshot_json['specification'], 'offline-text-v1')
        with self.assertRaises(Exception):
            with self.engine.begin() as db:
                db.execute(text('UPDATE billing_orders SET pricing_snapshot_json=:snapshot WHERE id=:id'),
                           {'snapshot': '{}', 'id': result.order_id})

    def test_mock_transport_and_queries_work_with_network_and_storage_forbidden(self):
        from unittest.mock import patch
        from app.services.billing.text_adapter import TextVisionMockAdapter
        # Patch real HTTP transports, preserving TestClient's in-memory ASGI transport
        # and Windows' event-loop socketpair used by the test harness itself.
        with patch('httpx.HTTPTransport.handle_request', side_effect=AssertionError('network forbidden')), \
                patch('httpx.AsyncHTTPTransport.handle_async_request', side_effect=AssertionError('network forbidden')), \
                patch('app.services.storage.get_storage', side_effect=AssertionError('storage forbidden')):
            for capability in ('TEXT', 'VISION'):
                result = self.service.execute_metered(*self.request(key='fixture:'+capability+':1', capability=capability),
                                                       adapter=TextVisionMockAdapter())
                response = self.client.get('/api/me/billing/model-orders/'+result.order_id, headers=self.headers('member'))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['status'], 'SETTLED')
            self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '7.5000')
            self.assertEqual(self.service.balance(self.auth())['balance_credits'], '992.5000')

    def test_exported_query_contract_and_scoped_errors_and_pagination_order(self):
        from unittest.mock import patch
        from sqlalchemy.exc import OperationalError
        from app.services.billing.text_adapter import TextVisionMockAdapter
        for index in range(3):
            self.service.execute_metered(*self.request(key=f'fixture:pagination:{index}'), adapter=TextVisionMockAdapter())
        first = self.client.get('/api/me/billing/model-orders?page_size=2', headers=self.headers('member')).json()
        second = self.client.get('/api/me/billing/model-orders?page_size=2&page=2', headers=self.headers('member')).json()
        self.assertEqual((first['total'], len(first['items']), len(second['items'])), (3, 2, 1))
        self.assertTrue(set(row['id'] for row in first['items']).isdisjoint(row['id'] for row in second['items']))
        ordered = first['items'] + second['items']
        self.assertEqual([(row['created_at'], row['id']) for row in ordered],
                         sorted(((row['created_at'], row['id']) for row in ordered), reverse=True))
        from pathlib import Path
        import json
        contract = json.loads((Path(__file__).resolve().parents[2]/'.scratch/model-pricing-mvp/openapi.json').read_text(encoding='utf-8'))
        exported = self.app.openapi()
        for path, item in contract['paths'].items():
            if 'model-orders' not in path:
                continue
            operation = exported['paths']['/api'+path]['get']
            self.assertEqual(operation['operationId'], item['get']['operationId'])
            self.assertEqual(operation['responses']['200']['content']['application/json']['schema'],
                             item['get']['responses']['200']['content']['application/json']['schema'])
            for code in item['get']['responses']:
                self.assertIn(code, operation['responses'])
        self.assertEqual(set(first['items'][0]), set(contract['components']['schemas']['ModelOrder']['required']))
        with patch.object(self.service, 'model_orders', side_effect=OperationalError('SENSITIVE SQL', {}, Exception('SECRET'))):
            response = self.client.get('/api/me/billing/model-orders', headers=self.headers('member'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['detail']['code'], 'SERVICE_UNAVAILABLE')
        self.assertNotIn('SENSITIVE', response.text)
        self.assertNotIn('SECRET', response.text)

    def test_tenant_namespace_and_live_permission_recheck(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        from app.models import TenantMember
        from sqlalchemy import select
        context, model, request = self.request()
        first = self.service.execute_metered(context, model, request, adapter=TextVisionMockAdapter())
        self.grant('100', tenant='b')
        self.service.set_quota(self.auth('other'), 'other', '100', 'fixture')
        other = self.service.execute_metered(replace(context, tenant_id='b', user_id='other'), model, request, adapter=TextVisionMockAdapter())
        self.assertNotEqual(first.order_id, other.order_id)
        self.assertEqual(self.service.model_orders(self.auth('other'))['total'], 1)
        with self.sessions.begin() as db:
            member = db.scalar(select(TenantMember).where(TenantMember.user_id == 'member'))
            member.status = 'disabled'
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(context, model, request, adapter=TextVisionMockAdapter())
        self.assertEqual(error.exception.detail['code'], 'UNAUTHENTICATED')
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '995.0000')

    def test_saved_price_cannot_override_missing_current_adapter_usage_contract(self):
        from app.services.billing.registry import UsageContract
        from app.services.billing.text_adapter import TextVisionMockAdapter
        self.model.usage_contract = UsageContract()
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(*self.request(), adapter=TextVisionMockAdapter())
        self.assertEqual(error.exception.detail['code'], 'UNPRICED_MODEL')
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)

    def test_one_persisted_business_intent_cannot_be_charged_again_by_another_member(self):
        from app.services.billing.text_adapter import TextVisionMockAdapter
        class Counter(TextVisionMockAdapter):
            calls = 0
            def execute(adapter, request):
                adapter.calls += 1
                return super().execute(request)
        adapter = Counter()
        context, model, request = self.request()
        first = self.service.execute_metered(context, model, request, adapter=adapter)
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(replace(context, user_id='member2'), model, request, adapter=adapter)
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(self.service.model_orders(self.auth(), enterprise=True)['total'], 1)
        self.assertEqual(self.service.get_model_order(self.auth('member'), first.order_id)['charged_credits'], '5.0000')
        self.assertEqual(self.service.own_quota(self.auth('member2'))['used_credits'], '0.0000')

    def test_text_and_vision_each_cover_rollback_unknown_replay_price_and_denial(self):
        """Both capabilities traverse the same faults, with different controlled usage."""
        from app.services.billing.text_adapter import TextVisionMockAdapter
        from app.services.billing.metering import AdapterOutcome
        from sqlalchemy import event
        for capability, expected, repriced in (('TEXT', '5.0000', '7.0000'), ('VISION', '2.5000', '3.5000')):
            with self.subTest(capability=capability):
                fixture = ModelMeteringTests()
                fixture.setUp()
                try:
                    class Transport(TextVisionMockAdapter):
                        calls = 0
                        def execute(adapter, request):
                            adapter.calls += 1
                            return super().execute(request)
                    adapter = Transport()
                    arguments = fixture.request(capability=capability)
                    # DB transaction fault must roll back every accounting projection.
                    def reject(conn, cursor, statement, parameters, context, many):
                        if statement.startswith('INSERT INTO credit_ledger'):
                            raise RuntimeError('synthetic charge insert failure')
                    event.listen(fixture.engine, 'before_cursor_execute', reject)
                    try:
                        with self.assertRaises(RuntimeError):
                            fixture.service.execute_metered(*arguments, adapter=adapter)
                    finally:
                        event.remove(fixture.engine, 'before_cursor_execute', reject)
                    pending = fixture.service.model_orders(fixture.auth('member'))['items'][0]
                    self.assertEqual((pending['status'], pending['reconciliation_status'], pending['charged_credits']),
                                     ('RESERVED', 'NEEDS_REVIEW', '0.0000'))
                    self.assertEqual(fixture.service.balance(fixture.auth())['balance_credits'], '1000.0000')
                    self.assertEqual(fixture.service.own_quota(fixture.auth('member'))['used_credits'], '0.0000')
                    self.assertFalse(any(row['event_type'] == 'CHARGE' for row in fixture.service.ledger(fixture.auth())['items']))
                    # Repricing between request and recovery does not change this snapshot.
                    fixture.price('2.0000')
                    fixture.service.settle_model(pending['id'])
                    replay = fixture.service.execute_metered(*arguments, adapter=adapter)
                    self.assertEqual((adapter.calls, replay.order_id), (1, pending['id']))
                    self.assertEqual(fixture.service.get_model_order(fixture.auth('member'), pending['id'])['charged_credits'], expected)
                    new = fixture.service.execute_metered(*fixture.request(key='fixture:new:1', capability=capability), adapter=adapter)
                    self.assertEqual(fixture.service.get_model_order(fixture.auth('member'), new.order_id)['charged_credits'], repriced)
                    charges = [row for row in fixture.service.ledger(fixture.auth())['items'] if row['event_type'] == 'CHARGE']
                    self.assertEqual({row['order_id']: row['delta_credits'] for row in charges},
                                     {pending['id']: '-'+expected, new.order_id: '-'+repriced})
                    with self.assertRaises(HTTPException) as denied:
                        fixture.service.get_model_order(fixture.auth('member2'), new.order_id)
                    self.assertEqual(denied.exception.status_code, 404)
                    class Unknown(TextVisionMockAdapter):
                        def execute(adapter, request):
                            return AdapterOutcome({'valid_business_result': True}, None)
                    unknown = fixture.service.execute_metered(*fixture.request(key='fixture:unknown:1', capability=capability), adapter=Unknown())
                    order = fixture.service.get_model_order(fixture.auth('member'), unknown.order_id)
                    self.assertEqual((order['status'], order['usage']['source'], order['reconciliation_status']),
                                     ('RESERVED', 'UNKNOWN', 'NEEDS_REVIEW'))
                    self.assertEqual(order['reserved_credits'], '12.0000')
                    before = adapter.calls
                    fixture.service.revoke(fixture.auth(), 'a', str(uuid.uuid4()), fixture.service.balance(fixture.auth())['available_credits'], 'fixture')
                    with self.assertRaises(HTTPException) as denied:
                        fixture.service.execute_metered(*fixture.request(key='fixture:insufficient:1', capability=capability), adapter=adapter)
                    self.assertEqual(denied.exception.detail['code'], 'INSUFFICIENT_CREDITS')
                    self.assertEqual(adapter.calls, before)
                    self.assertEqual(fixture.service.own_quota(fixture.auth('member2'))['used_credits'], '0.0000')
                    self.assertTrue(fixture.service.check_accounts()['ok'])
                finally:
                    fixture.tearDown()
