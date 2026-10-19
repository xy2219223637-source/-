"""Image MOCK consumption is measured from verified local artifacts."""
from datetime import datetime, timezone
import base64
from dataclasses import replace
from io import BytesIO
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from test_billing_management import BillingFixture


class ImageMeteringTests(BillingFixture):
    @staticmethod
    def png(size=(8, 8)):
        output = BytesIO()
        Image.new('RGB', size, 'blue').save(output, format='PNG')
        return output.getvalue()

    def setUp(self):
        super().setUp()
        from app.services.billing.orders import BillingOrderService
        from app.services.billing.registry import ModelRegistry, ModelRegistration, UsageContract
        self.model = ModelRegistration('offline:image', 'local-image', ('IMAGE',), True,
            usage_contract=UsageContract((), ('image_count',), 'tests/local-decoded-png'))
        self.service = BillingOrderService(self.sessions, self.settings,
            lambda: self.time or datetime.now(timezone.utc), ModelRegistry([self.model]))
        self.grant('1000')
        self.quota(value='100')
        self.price()

    def price(self, rate='0.0001', source_unit=10, tokens=1):
        configuration = {'usage_mode': 'FOLDED_TOKENS', 'metrics': [
            {'metric': 'converted_tokens', 'unit_size': 1, 'credits_per_unit': rate}],
            'conversion': {'source_metric': 'image_count', 'source_unit_size': source_unit,
                           'tokens_per_unit': tokens}}
        response = self.client.put('/api/platform-admin/billing/price-rules/' + self.model.model_key,
            json=configuration, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        return configuration

    def request(self, key='creative:fixture:generate:1', count=3):
        from app.services.billing.metering import BillingContext, BoundedRequest
        return (BillingContext('a', 'member', key, 'creative_projects.access', 'MOCK'), self.model.model_key,
            BoundedRequest('IMAGE', {'count': count, 'width': 8, 'height': 8, 'quality': 'local'},
                {'image_count': count}, 'tests/finite-local-images', 'mock-image:8x8:local'))

    def test_effective_images_use_exact_fraction_and_only_final_credit_rounding(self):
        from app.services.billing.metering import AdapterOutcome
        class LocalOutcome:
            execution_mode = 'MOCK'
            capabilities = ('IMAGE',)
            def execute(self, request):
                return AdapterOutcome({'images': ['one', 'two', 'three']}, {'image_count': 3})
        result = self.service.execute_metered(*self.request(), adapter=LocalOutcome())
        order = self.service.get_model_order(self.auth('member'), result.order_id)
        self.assertEqual(order['usage'], {'source': 'FOLDED_TOKENS', 'input_tokens': None,
            'output_tokens': None, 'converted_tokens': '0.3', 'raw_metric': 'image_count', 'raw_quantity': 3})
        self.assertEqual(order['charged_credits'], '0.0001')
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '999.9999')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.0001')
        self.assertEqual(order['supplier_cost']['status'], 'NOT_INCURRED')
        self.assertEqual(order['execution_mode'], 'MOCK')

    def test_local_mock_images_decode_and_never_touch_vendor_or_object_storage(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        with patch('httpx.Client.send', side_effect=AssertionError('no network')), \
                patch('httpx.AsyncClient.send', side_effect=AssertionError('no network')), \
                patch('app.services.storage.get_storage', side_effect=AssertionError('no storage')):
            result = self.service.execute_metered(*self.request(), adapter=ImageMockAdapter())
        self.assertEqual(result.usage['raw_quantity'], 3)
        self.assertEqual(result.result['output_status'], 'COMPLETE')
        for index, artifact in enumerate(result.result['images']):
            with Image.open(BytesIO(base64.b64decode(artifact['png_base64']))) as image:
                image.load()
                self.assertEqual(image.size, (8, 8))
            self.assertEqual(artifact['child_index'], index)
        order = self.client.get('/api/me/billing/model-orders/' + result.order_id,
            headers=self.headers('member')).json()
        self.assertEqual(order['usage']['raw_quantity'], 3)
        self.assertEqual(order['pricing_snapshot']['conversion']['source_unit_size'], 10)

    def test_unpriced_image_specifications_are_rejected_before_reservation_or_generation(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        context, key, request = self.request()
        for change in ({'specification': None}, {'specification': 'vendor-high-4k'},
                {'payload': {**request.payload, 'width': 16}},
                {'payload': {**request.payload, 'quality': 'high'}},
                {'payload': {**request.payload, 'count': True}},
                {'maximum_usage': {'image_count': 2}}):
            with self.subTest(change=change):
                with self.assertRaises(HTTPException) as error:
                    self.service.execute_metered(context, key, replace(request, **change), adapter=ImageMockAdapter())
                self.assertEqual(error.exception.detail['code'], 'PRICE_SPEC_MISMATCH')
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '0.0000')

    def test_partial_result_bills_only_decoded_images_not_requested_count(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        self.price('1.0000')
        result = self.service.execute_metered(*self.request(), adapter=ImageMockAdapter(
            images=(self.png(), b'not-an-image', self.png(), self.png((16, 16)))))
        order = self.service.get_model_order(self.auth('member'), result.order_id)
        self.assertEqual((result.execution_status, result.result['output_status']), ('FAILED', 'PARTIAL'))
        self.assertEqual(len(result.result['images']), 2)
        self.assertEqual(len(result.result['invalid_images']), 2)
        self.assertEqual(order['usage']['raw_quantity'], 2)
        self.assertEqual(order['charged_credits'], '0.2000')
        self.assertEqual(order['status'], 'SETTLED')

    def test_confirmed_empty_result_and_unknown_count_have_different_accounting(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        self.price('1.0000')
        empty = self.service.execute_metered(*self.request(key='creative:empty:1'), adapter=ImageMockAdapter(images=()))
        empty_order = self.service.get_model_order(self.auth('member'), empty.order_id)
        self.assertEqual((empty.execution_status, empty.result['output_status']), ('FAILED', 'EMPTY'))
        self.assertEqual((empty_order['status'], empty_order['usage']['raw_quantity']), ('SETTLED', 0))
        unknown = self.service.execute_metered(*self.request(key='creative:unknown:1'),
            adapter=ImageMockAdapter(images=(self.png(),), usage_confirmed=False))
        order = self.service.get_model_order(self.auth('member'), unknown.order_id)
        self.assertEqual(order['usage']['source'], 'UNKNOWN')
        self.assertIsNone(order['usage']['raw_quantity'])
        self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
        self.assertEqual(order['reserved_credits'], '0.3000')
        self.assertEqual(order['charged_credits'], '0.0000')

    def test_real_image_providers_and_local_demo_cannot_bypass_context(self):
        from app.services import image_generation
        settings = self.settings.model_copy(update={'apilio_image_api_key': 'synthetic-only',
            'apilio_image_model': 'fixture-image'})
        with patch.object(image_generation, 'get_settings', return_value=settings), \
                patch.object(image_generation, 'get_storage', side_effect=AssertionError('no storage')), \
                patch('httpx.Client.send', side_effect=AssertionError('no network')):
            for provider in (image_generation.ArkSeedreamImageProvider(),
                             image_generation.ApilioImageProvider(), image_generation.LocalDemoImageProvider()):
                with self.subTest(provider=provider.name):
                    with self.assertRaises(HTTPException) as error:
                        provider.generate(source_url='https://fixture.invalid/image', prompt='fixture', action='test',
                            count=1, width=8, height=8, project_id=1)
                    self.assertEqual(error.exception.detail['code'], 'BILLING_CONTEXT_REQUIRED')

    def test_replay_price_snapshot_and_accounting_remain_consistent(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        self.price('1.0000')
        context, key, request = self.request()
        self.service.reserve_model(context, key, request)
        self.price('2.0000')
        first = self.service.execute_metered(context, key, request, adapter=ImageMockAdapter())
        class ReplayTransport(ImageMockAdapter):
            calls = 0
            def execute(adapter, request):
                adapter.calls += 1
                return super().execute(request)
        adapter = ReplayTransport(images=())
        replay = self.service.execute_metered(context, key, request, adapter=adapter)
        self.assertEqual((replay, adapter.calls), (first, 0))
        old = self.service.get_model_order(self.auth('member'), first.order_id)
        new = self.service.execute_metered(*self.request(key='creative:fixture:generate:2'), adapter=ImageMockAdapter())
        current = self.service.get_model_order(self.auth('member'), new.order_id)
        self.assertEqual((old['charged_credits'], current['charged_credits']), ('0.3000', '0.6000'))
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '999.1000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.9000')
        charges = [row for row in self.service.ledger(self.auth())['items'] if row['event_type'] == 'CHARGE']
        self.assertEqual(sorted(row['delta_credits'] for row in charges), ['-0.3000', '-0.6000'])
        for prefix, user in (('/api/me/billing/model-orders', 'member'),
                             ('/api/tenant-admin/billing/model-orders', 'owner')):
            response = self.client.get(prefix, headers=self.headers(user))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['total'], 2)
            detail = self.client.get(prefix + '/' + first.order_id, headers=self.headers(user)).json()
            self.assertEqual(detail['usage']['converted_tokens'], '0.3')
            self.assertEqual(detail['pricing_snapshot']['metrics'][0]['credits_per_unit'], '1.0000')
        self.assertEqual(self.service.check_accounts()['ok'], True)

    def test_over_bound_valid_images_keep_reservation_instead_of_overcharging(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        self.price('1.0000')
        result = self.service.execute_metered(*self.request(), adapter=ImageMockAdapter(images=(self.png(),) * 4))
        order = self.service.get_model_order(self.auth('member'), result.order_id)
        self.assertEqual(order['usage']['raw_quantity'], 4)
        self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
        self.assertEqual(order['reserved_credits'], '0.3000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.0000')

    def test_six_digit_conversion_zero_unknown_and_invalid_quantities_are_distinct(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        from app.services.billing.metering import AdapterOutcome
        self.price('1.0000', source_unit=1000000)
        result = self.service.execute_metered(*self.request(), adapter=ImageMockAdapter())
        self.assertEqual(result.usage['converted_tokens'], '0.000003')
        for index, bad_usage in enumerate(({}, {'image_count': None}, {'image_count': True},
                {'image_count': -1}, {'image_count': 1.5}, {'input_tokens': 3}, {'image_count': 10**9 + 1})):
            class InvalidUsage(ImageMockAdapter):
                def execute(adapter, request):
                    return AdapterOutcome({'images': []}, bad_usage)
            with self.subTest(usage=bad_usage):
                with self.assertRaises(HTTPException) as error:
                    self.service.execute_metered(*self.request(key=f'creative:invalid:{index}'), adapter=InvalidUsage())
                self.assertEqual(error.exception.detail['code'], 'INVALID_USAGE')
        orders = self.service.model_orders(self.auth('member'))['items']
        self.assertEqual(sum(row['reconciliation_status'] == 'NEEDS_REVIEW' for row in orders), 7)

    def test_proved_no_usage_cancels_but_indeterminate_failure_keeps_reservation(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        from app.services.billing.metering import KnownNoUsage
        self.price('1.0000')
        class RejectedBeforeGeneration(ImageMockAdapter):
            def execute(adapter, request):
                raise KnownNoUsage()
        class LostResponse(ImageMockAdapter):
            def execute(adapter, request):
                raise TimeoutError('controlled response loss')
        canceled = self.service.execute_metered(*self.request(key='creative:no-usage:1'), adapter=RejectedBeforeGeneration())
        unknown = self.service.execute_metered(*self.request(key='creative:lost:1'), adapter=LostResponse())
        self.assertEqual(self.service.get_model_order(self.auth('member'), canceled.order_id)['status'], 'CANCELED')
        row = self.service.get_model_order(self.auth('member'), unknown.order_id)
        self.assertEqual((row['reconciliation_status'], row['reserved_credits']), ('NEEDS_REVIEW', '0.3000'))

    def test_image_price_roundtrip_rejects_wrong_source_and_token_mode(self):
        configuration = self.price('2.0000', 1000000, 3)
        models = self.client.get('/api/platform-admin/billing/price-rules', headers=self.headers()).json()['items']
        self.assertEqual(models[0]['pricing']['configuration'], configuration)
        path = '/api/platform-admin/billing/price-rules/' + self.model.model_key
        for body in ({**configuration, 'conversion': None},
                {**configuration, 'conversion': {**configuration['conversion'], 'source_metric': 'character_count'}},
                {'usage_mode': 'REAL_TOKENS', 'metrics': [
                    {'metric': 'input_tokens', 'unit_size': 1, 'credits_per_unit': '1.0000'}], 'conversion': None}):
            self.assertEqual(self.client.put(path, json=body, headers=self.headers()).status_code, 422)

    def test_converted_token_upper_bound_is_checked_before_generation(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        self.price('0.0001', source_unit=1, tokens=10**9)
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(*self.request(), adapter=ImageMockAdapter())
        self.assertEqual(error.exception.detail['code'], 'USAGE_BOUND_UNKNOWN')
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)

    def test_insufficient_quota_does_not_generate_or_reserve_images(self):
        from app.services.billing.image_adapter import ImageMockAdapter
        self.price('1000.0000')
        class ControlledTransport(ImageMockAdapter):
            calls = 0
            def execute(adapter, request):
                adapter.calls += 1
                return super().execute(request)
        adapter = ControlledTransport()
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(*self.request(), adapter=adapter)
        self.assertEqual(error.exception.detail['code'], 'QUOTA_EXCEEDED')
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)
