"""Embedding results, input-only accounting and stable child intents: offline seams."""
from datetime import datetime, timezone

from test_billing_management import BillingFixture


class EmbeddingMeteringTests(BillingFixture):
    def setUp(self):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter
        super().setUp()
        from app.services.billing.orders import BillingOrderService
        from app.services.billing.registry import ModelRegistry, ModelRegistration
        self.model = ModelRegistration('openai_compatible:llm', 'fixture-embedding', ('EMBEDDING',), True,
                                       usage_contract=EmbeddingMockAdapter.usage_contract)
        self.service = BillingOrderService(self.sessions, self.settings,
            lambda: self.time or datetime.now(timezone.utc), ModelRegistry([self.model]))
        self.grant('1000')
        self.quota(value='100')
        self.price()

    def price(self, rate='2.0000'):
        body = {'usage_mode': 'REAL_TOKENS', 'metrics': [
            {'metric': 'input_tokens', 'unit_size': 1000, 'credits_per_unit': rate}], 'conversion': None}
        response = self.client.put('/api/platform-admin/billing/price-rules/' + self.model.model_key,
                                  json=body, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        return body

    def context(self, key='document:fixture:revision:1'):
        from app.services.billing.metering import BillingContext
        return BillingContext('a', 'member', key, 'knowledge.access', 'MOCK')

    def request(self, texts=None):
        from app.services.billing.embedding_adapter import embedding_request
        return embedding_request(texts or ['原始文本'], dimensions=3, maximum_input_tokens=2000,
                                 bound_evidence='tests/controlled-input-token-bound')

    def execute(self, adapter=None, key='document:fixture:revision:1', request=None):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter
        return self.service.execute_metered(self.context(key), self.model.model_key, request or self.request(),
            adapter=adapter or EmbeddingMockAdapter({'data': [{'index': 0, 'embedding': [1, 2, 3]}],
                                                     'usage': {'prompt_tokens': 1000}}))

    def test_single_input_only_embedding_uses_same_account_quota_and_unique_charge(self):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter
        response = {'data': [{'index': 0, 'embedding': [0.25, -0.5, 1]}], 'usage': {'prompt_tokens': 1000}}
        result = self.execute(EmbeddingMockAdapter(response))
        self.assertEqual(result.result, {'embeddings': [[0.25, -0.5, 1.0]]})
        self.assertEqual(result.execution_status, 'SUCCEEDED')
        order = self.service.get_model_order(self.auth('member'), result.order_id)
        self.assertEqual((order['charged_credits'], order['usage']['input_tokens']), ('2.0000', 1000))
        self.assertIsNone(order['usage']['output_tokens'])
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '998.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '2.0000')
        rows = [row for row in self.service.ledger(self.auth())['items'] if row['event_type'] == 'CHARGE']
        self.assertEqual([(row['order_id'], row['delta_credits']) for row in rows], [(result.order_id, '-2.0000')])
        self.assertTrue(self.service.check_accounts()['ok'])

    def test_batch_with_confirmed_usage_over_bound_does_not_report_complete_or_continue(self):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter, execute_embedding_batches
        adapters = [EmbeddingMockAdapter({'data': [{'index': 0, 'embedding': [1, 2, 3]}], 'usage': {'prompt_tokens': 2001}}),
                    EmbeddingMockAdapter(None)]
        result = execute_embedding_batches(self.service, self.context(), self.model.model_key,
                                            [self.request(), self.request()], adapters=adapters)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.children), 1)
        order = self.service.get_model_order(self.auth('member'), result.children[0].order_id)
        self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
        self.assertEqual(order['usage']['input_tokens'], 2001)
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.0000')
        self.assertTrue(self.service.check_accounts()['ok'])

    def test_malformed_vectors_fail_execution_but_preserve_known_input_consumption(self):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter
        cases = [[], [{'index': 0, 'embedding': [1, 2]}], [{'index': 1, 'embedding': [1, 2, 3]}],
            [{'index': True, 'embedding': [1, 2, 3]}], [{'index': 0, 'embedding': [True, 2, 3]}],
            [{'index': 0, 'embedding': [float('nan'), 2, 3]}],
            [{'index': 0, 'embedding': [float('inf'), 2, 3]}],
            [{'index': 0, 'embedding': ['1', 2, 3]}], [{'index': 0, 'embedding': [10**400, 2, 3]}],
            [{'index': 0, 'embedding': [1, 2, 3]}, {'index': 0, 'embedding': [4, 5, 6]}]]
        for index, data in enumerate(cases):
            with self.subTest(index=index):
                result = self.execute(EmbeddingMockAdapter({'data': data, 'usage': {'prompt_tokens': 1000}}),
                                      key=f'document:bad-vector:{index}')
                self.assertEqual(result.execution_status, 'FAILED')
                self.assertEqual(result.result, {'embeddings': None, 'error': 'INVALID_EMBEDDING_RESPONSE'})
                order = self.service.get_model_order(self.auth('member'), result.order_id)
                self.assertEqual((order['status'], order['charged_credits']), ('SETTLED', '2.0000'))
                self.assertEqual(order['usage']['input_tokens'], 1000)
                self.assertIsNone(order['usage']['output_tokens'])

    def test_missing_invalid_input_usage_stays_unknown_and_confirmed_zero_is_distinct(self):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter
        for index, usage in enumerate((None, {}, {'prompt_tokens': None}, {'prompt_tokens': True},
                                       {'prompt_tokens': -1}, {'prompt_tokens': 1.5}, {'prompt_tokens': 10**9 + 1})):
            result = self.execute(EmbeddingMockAdapter({'data': [{'index': 0, 'embedding': [1, 2, 3]}],
                                  'usage': usage}), key=f'document:unknown:{index}')
            order = self.service.get_model_order(self.auth('member'), result.order_id)
            self.assertEqual(result.execution_status, 'SUCCEEDED')
            self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
            self.assertEqual((order['charged_credits'], order['reserved_credits']), ('0.0000', '4.0000'))
            self.assertEqual(order['usage']['source'], 'UNKNOWN')
            self.assertIsNone(order['usage']['input_tokens'])
            self.assertIsNone(order['usage']['output_tokens'])
        zero = self.execute(EmbeddingMockAdapter({'data': [{'index': 0, 'embedding': [1, 2, 3]}],
                            'usage': {'prompt_tokens': 0}}), key='document:zero:1')
        order = self.service.get_model_order(self.auth('member'), zero.order_id)
        self.assertEqual((order['status'], order['charged_credits']), ('SETTLED', '0.0000'))
        self.assertEqual(order['usage']['input_tokens'], 0)
        self.assertIsNone(order['usage']['output_tokens'])
        empty = self.execute(EmbeddingMockAdapter(None), key='document:empty:1')
        self.assertEqual(empty.execution_status, 'FAILED')
        self.assertEqual(self.service.get_model_order(self.auth('member'), empty.order_id)['status'], 'RESERVED')

    def test_invalid_embedding_requests_are_rejected_before_reservation_or_transport(self):
        from dataclasses import replace
        from fastapi import HTTPException
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter, embedding_request
        class NeverTransport(EmbeddingMockAdapter):
            def execute(adapter, request):
                raise AssertionError('invalid request reached transport')
        invalid = [replace(self.request(), payload=payload) for payload in (
            {}, {'texts': [], 'dimensions': 3}, {'texts': [''], 'dimensions': 3},
            {'texts': [2], 'dimensions': 3}, {'texts': 'text', 'dimensions': 3},
            {'texts': ['text'], 'dimensions': True}, {'texts': ['text'], 'dimensions': 0},
            {'texts': ['text'], 'dimensions': 65537})]
        invalid += [replace(self.request(), maximum_usage=value) for value in (
            {}, {'input_tokens': True}, {'input_tokens': -1}, {'input_tokens': 10**9+1},
            {'input_tokens': 1000, 'output_tokens': 0})]
        for request in invalid:
            with self.assertRaises(HTTPException) as error:
                self.execute(NeverTransport(None), request=request)
            self.assertEqual(error.exception.detail['code'], 'PRICE_SPEC_MISMATCH')
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '0.0000')
        with self.assertRaises(HTTPException):
            embedding_request([], dimensions=3, maximum_input_tokens=1000, bound_evidence='tests/bound')

    def test_batches_charge_per_transport_not_per_vector_and_replay_all_child_orders(self):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter, execute_embedding_batches
        class CountingTransport(EmbeddingMockAdapter):
            calls = 0
            def execute(adapter, request):
                adapter.calls += 1
                return super().execute(request)
        first = CountingTransport({'data': [{'index': 1, 'embedding': [4, 5, 6]},
            {'index': 0, 'embedding': [1, 2, 3]}], 'usage': {'prompt_tokens': 1000}})
        second = CountingTransport({'data': [{'index': 0, 'embedding': [7, 8, 9]}], 'usage': {'prompt_tokens': 500}})
        batches = [self.request(['一', '二']), self.request(['三'])]
        result = execute_embedding_batches(self.service, self.context(), self.model.model_key, batches,
                                            adapters=[first, second])
        self.assertTrue(result.complete)
        self.assertEqual(result.embeddings, [[1., 2., 3.], [4., 5., 6.], [7., 8., 9.]])
        self.assertEqual(len(result.children), 2)
        self.assertEqual([first.calls, second.calls], [1, 1])
        replay = execute_embedding_batches(self.service, self.context(), self.model.model_key, batches,
                                            adapters=[first, second])
        self.assertEqual([child.order_id for child in replay.children], [child.order_id for child in result.children])
        self.assertEqual([first.calls, second.calls], [1, 1])
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '997.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '3.0000')
        self.assertEqual(len([row for row in self.service.ledger(self.auth())['items'] if row['event_type'] == 'CHARGE']), 2)

    def test_partial_batches_return_success_and_failed_child_orders_without_retry_or_later_calls(self):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter, execute_embedding_batches
        from fastapi import HTTPException
        class CountingTransport(EmbeddingMockAdapter):
            calls = 0
            def execute(adapter, request):
                adapter.calls += 1
                return super().execute(request)
        good = CountingTransport({'data': [{'index': 0, 'embedding': [1, 2, 3]}], 'usage': {'prompt_tokens': 1000}})
        bad = CountingTransport({'data': [], 'usage': {'prompt_tokens': 500}})
        later = CountingTransport(None)
        batches = [self.request(['一']), self.request(['二']), self.request(['三'])]
        result = execute_embedding_batches(self.service, self.context(), self.model.model_key, batches,
                                            adapters=[good, bad, later])
        self.assertFalse(result.complete)
        self.assertIsNone(result.embeddings)
        self.assertEqual([child.execution_status for child in result.children], ['SUCCEEDED', 'FAILED'])
        self.assertEqual([good.calls, bad.calls, later.calls], [1, 1, 0])
        replay = execute_embedding_batches(self.service, self.context(), self.model.model_key, batches,
                                            adapters=[good, bad, later])
        self.assertEqual([child.order_id for child in replay.children], [child.order_id for child in result.children])
        self.assertEqual([good.calls, bad.calls, later.calls], [1, 1, 0])
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '3.0000')
        changed = [batches[0], self.request(['不同内容']), batches[2]]
        with self.assertRaises(HTTPException) as error:
            execute_embedding_batches(self.service, self.context(), self.model.model_key, changed, adapters=[good, bad, later])
        self.assertEqual(error.exception.detail['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 2)

    def test_unknown_partial_batch_keeps_first_charge_and_second_reservation(self):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter, execute_embedding_batches
        batches = [self.request(['一']), self.request(['二'])]
        adapters = [EmbeddingMockAdapter({'data': [{'index': 0, 'embedding': [1, 2, 3]}], 'usage': {'prompt_tokens': 1000}}),
                    EmbeddingMockAdapter(None)]
        first = execute_embedding_batches(self.service, self.context(), self.model.model_key, batches, adapters=adapters)
        replay = execute_embedding_batches(self.service, self.context(), self.model.model_key, batches, adapters=adapters)
        self.assertFalse(first.complete)
        self.assertEqual([child.order_id for child in first.children], [child.order_id for child in replay.children])
        orders = [self.service.get_model_order(self.auth('member'), child.order_id) for child in first.children]
        self.assertEqual([order['status'] for order in orders], ['SETTLED', 'RESERVED'])
        self.assertEqual(orders[1]['reconciliation_status'], 'NEEDS_REVIEW')
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '998.0000')
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '4.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '2.0000')
        self.assertTrue(self.service.check_accounts()['ok'])

    def test_embedding_price_and_order_api_keep_independent_identity_input_only_and_mock_cost(self):
        invalid = self.price()
        invalid['metrics'].append({'metric': 'output_tokens', 'unit_size': 1000, 'credits_per_unit': '3.0000'})
        path = '/api/platform-admin/billing/price-rules/' + self.model.model_key
        self.assertEqual(self.client.put(path, json=invalid, headers=self.headers()).status_code, 422)
        result = self.execute()
        own = self.client.get('/api/me/billing/model-orders/' + result.order_id, headers=self.headers('member'))
        enterprise = self.client.get('/api/tenant-admin/billing/model-orders/' + result.order_id, headers=self.headers('owner'))
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json(), enterprise.json())
        self.assertEqual((own.json()['provider'], own.json()['model_id']), ('openai_compatible:llm', 'fixture-embedding'))
        self.assertEqual(own.json()['supplier_cost'], {'status': 'NOT_INCURRED', 'amount': None, 'currency': None})
        self.assertEqual(own.json()['execution_mode'], 'MOCK')
        self.assertIsNone(own.json()['usage']['output_tokens'])
        self.assertEqual([metric['metric'] for metric in own.json()['pricing_snapshot']['metrics']], ['input_tokens'])
        for user in ('member2', 'other'):
            self.assertEqual(self.client.get('/api/me/billing/model-orders/'+result.order_id, headers=self.headers(user)).status_code, 404)

    def test_mock_transport_and_batch_replay_never_use_network_or_object_storage(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter, execute_embedding_batches
        adapter = EmbeddingMockAdapter({'data': [{'index': 0, 'embedding': [1, 2, 3]}], 'usage': {'prompt_tokens': 1000}})
        with ExitStack() as stack:
            for target in ('socket.create_connection', 'socket.socket.connect', 'httpx.Client.send',
                           'httpx.AsyncClient.send', 'app.services.storage.get_storage',
                           'app.services.storage.StorageService.upload'):
                stack.enter_context(patch(target, side_effect=AssertionError('MOCK attempted external I/O')))
            first = execute_embedding_batches(self.service, self.context(), self.model.model_key, [self.request()], adapters=[adapter])
            replay = execute_embedding_batches(self.service, self.context(), self.model.model_key, [self.request()], adapters=[adapter])
        self.assertTrue(first.complete)
        self.assertEqual(first.children[0].order_id, replay.children[0].order_id)
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '998.0000')

    def test_two_vector_duplicate_or_missing_index_is_not_a_successful_batch(self):
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter
        for index, indexes in enumerate(((0, 0), (0, 2), (0, -1))):
            response = {'data': [{'index': number, 'embedding': [1, 2, 3]} for number in indexes],
                        'usage': {'prompt_tokens': 1000}}
            outcome = self.execute(EmbeddingMockAdapter(response), key=f'document:duplicate:{index}',
                                   request=self.request(['one', 'two']))
            self.assertEqual(outcome.execution_status, 'FAILED')
            self.assertEqual(self.service.get_model_order(self.auth('member'), outcome.order_id)['charged_credits'], '2.0000')

    def test_snapshot_repricing_and_insufficient_allowance_preserve_call_boundaries(self):
        from fastapi import HTTPException
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter
        class Reprice(EmbeddingMockAdapter):
            def execute(adapter, request):
                self.price('4.0000')
                return super().execute(request)
        response = {'data': [{'index': 0, 'embedding': [1, 2, 3]}], 'usage': {'prompt_tokens': 1000}}
        first = self.execute(Reprice(response))
        second = self.execute(EmbeddingMockAdapter(response), key='document:next:1')
        self.assertEqual(self.service.get_model_order(self.auth('member'), first.order_id)['charged_credits'], '2.0000')
        self.assertEqual(self.service.get_model_order(self.auth('member'), second.order_id)['charged_credits'], '4.0000')
        class NeverTransport(EmbeddingMockAdapter):
            def execute(adapter, request):
                raise AssertionError('insufficient allowance reached transport')
        self.quota(value='7')
        with self.assertRaises(HTTPException) as error:
            self.execute(NeverTransport(None), key='document:blocked:1')
        self.assertEqual(error.exception.detail['code'], 'QUOTA_EXCEEDED')
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 2)
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '994.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '6.0000')

    def test_all_batches_must_have_proven_bounds_before_first_consumption(self):
        from dataclasses import replace
        from fastapi import HTTPException
        from app.services.billing.embedding_adapter import EmbeddingMockAdapter, execute_embedding_batches
        good = EmbeddingMockAdapter({'data': [{'index': 0, 'embedding': [1, 2, 3]}], 'usage': {'prompt_tokens': 1000}})
        invalid = replace(self.request(), bound_evidence='')
        with self.assertRaises(HTTPException) as error:
            execute_embedding_batches(self.service, self.context(), self.model.model_key,
                                      [self.request(), invalid], adapters=[good, good])
        self.assertEqual(error.exception.detail['code'], 'USAGE_BOUND_UNKNOWN')
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '1000.0000')
