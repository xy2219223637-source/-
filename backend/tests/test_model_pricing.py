"""Current model prices: offline public management boundary tests."""
import uuid

from test_billing_management import BillingFixture


class ModelPricingTests(BillingFixture):
    def setUp(self):
        super().setUp()
        from app.services.billing.model_pricing import ModelPricingService
        self.service = ModelPricingService(self.sessions, self.settings)

    def test_model_registry_is_redacted_deduplicated_and_paginated(self):
        response = self.client.get('/api/platform-admin/billing/price-rules', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()
        self.assertEqual((page['page'], page['page_size']), (1, 20))
        self.assertGreater(page['total'], 0)
        self.assertTrue(any({'TEXT', 'VISION'} <= set(row['model_types']) for row in page['items']))
        self.assertNotIn('api_key', response.text)
        self.assertNotIn('mock_text', response.text)
        self.assertEqual([row['model_key'] for row in page['items']], sorted(row['model_key'] for row in page['items']))

    def register_model(self, provider='offline:fixture', model='fixture-text', metrics=('input_tokens', 'output_tokens'), raw=()):
        from app.services.billing.registry import ModelRegistry, ModelRegistration, UsageContract
        if self.service.registry is None:
            self.service.registry = ModelRegistry()
        item = ModelRegistration(provider, model, ('TEXT',), True, usage_contract=UsageContract(metrics, raw, 'tests/offline-fixture-contract'))
        self.service.registry.register(item)
        return item.model_key

    def price(self, rate='1.0000'):
        return {'usage_mode': 'REAL_TOKENS', 'metrics': [
            {'metric': 'input_tokens', 'unit_size': 1000, 'credits_per_unit': rate},
            {'metric': 'output_tokens', 'unit_size': 1000, 'credits_per_unit': '3.0000'}], 'conversion': None}

    def put(self, model, body=None, key=None, uid='platform'):
        return self.client.put('/api/platform-admin/billing/price-rules/' + model,
                               json=body if body is not None else self.price(), headers=self.headers(uid, key))

    def test_current_price_save_refresh_replay_does_not_overwrite_new_intent(self):
        model = self.register_model()
        key = str(uuid.uuid4())
        first = self.put(model, key=key)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()['model_key'], model)
        self.assertEqual(first.json()['configuration'], self.price())
        latest = self.put(model, self.price('2.0000'))
        replay = self.put(model, key=key)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(replay.headers['Idempotency-Replayed'], 'true')
        detail = self.client.get('/api/platform-admin/billing/price-rules/' + model, headers=self.headers()).json()
        self.assertEqual(detail['pricing'], latest.json())
        self.assertEqual(self.put(model, self.price('9.0000'), key=key).status_code, 409)
        other = self.register_model(model='other-text')
        self.assertEqual(self.put(other, key=key).status_code, 409)

    def test_price_validation_refuses_untrusted_fields_booleans_duplicate_and_overflow(self):
        import copy
        model = self.register_model()
        cases = []
        for value in ('0', '-1', '1e2', '0.00001', '922337203685477.5808', True, 1):
            body = self.price(); body['metrics'][0]['credits_per_unit'] = value; cases.append(body)
        for value in (True, 0, -1, 1000000001, 1.5):
            body = self.price(); body['metrics'][0]['unit_size'] = value; cases.append(body)
        body = self.price(); body['metrics'][1]['metric'] = 'input_tokens'; cases.append(body)
        body = self.price(); body['metrics'] = []; cases.append(body)
        for field in ('version', 'provider', 'model_id', 'usage', 'tenant_id'):
            body = self.price(); body[field] = 'untrusted'; cases.append(body)
        body = self.price(); body['metrics'][0]['credits_per_unit'] = '99999999999999'; body['metrics'][0]['unit_size'] = 1; cases.append(body)
        for body in cases:
            response = self.put(model, body)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertTrue(response.json()['detail']['request_id'])
        self.assertIsNone(self.service.get_model_price(self.auth(), model)['pricing'])

    def test_folded_contract_and_input_only_models(self):
        model = self.register_model(model='image', metrics=(), raw=('image_count',))
        body = {'usage_mode': 'FOLDED_TOKENS', 'metrics': [{'metric': 'converted_tokens', 'unit_size': 1000, 'credits_per_unit': '2.0000'}],
                'conversion': {'source_metric': 'image_count', 'source_unit_size': 1, 'tokens_per_unit': 1000}}
        self.assertEqual(self.put(model, body).status_code, 200)
        for field, value in (('source_metric', 'character_count'), ('source_unit_size', 3), ('tokens_per_unit', True)):
            invalid = {**body, 'conversion': {**body['conversion'], field: value}}
            self.assertEqual(self.put(model, invalid).status_code, 422)
        input_model = self.register_model(model='embedding', metrics=('input_tokens',))
        self.assertEqual(self.put(input_model).status_code, 422)
        body = self.price(); body['metrics'] = body['metrics'][:1]
        self.assertEqual(self.put(input_model, body).status_code, 200)

    def test_authentication_authorization_disabled_and_legacy_tombstones(self):
        path = '/api/platform-admin/billing/price-rules'
        for suffix in ('', '/summary', '/missing'):
            self.assertEqual(self.client.get(path + suffix).json()['detail']['code'], 'UNAUTHENTICATED')
            for uid in ('member', 'owner', 'other'):
                response = self.client.get(path + suffix, headers=self.headers(uid))
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()['detail']['code'], 'FORBIDDEN')
        for method, url in (('post', path), ('get', path+'/summary'), ('post', path+'/old/activate'), ('post', '/api/ai/mock/text')):
            response = getattr(self.client, method)(url, headers=self.headers())
            self.assertEqual(response.status_code, 410, response.text)
            self.assertEqual(response.json()['detail']['code'], 'LEGACY_ENDPOINT_RETIRED')
        self.settings.billing_enabled = False
        self.assertEqual(self.client.get(path, headers=self.headers()).json()['detail']['code'], 'BILLING_DISABLED')
        self.assertEqual(self.client.get(path).status_code, 401)

    def test_registry_unverified_protocol_cannot_be_priced_and_provider_identity_is_distinct(self):
        from app.services.billing.registry import ModelRegistry, ModelRegistration
        first = ModelRegistration('one:slot', 'same', ('TEXT',), True)
        self.service.registry = ModelRegistry([first, ModelRegistration('one:slot', 'same', ('VISION',), True),
                                               ModelRegistration('two:slot', 'same', ('TEXT',), True)])
        page = self.service.list_model_prices(self.auth(), page_size=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(len(page['items']), 1)
        self.assertEqual(self.put(first.model_key).status_code, 422)
        page = self.service.list_model_prices(self.auth(), model_type='VISION')
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['model_types'], ['TEXT', 'VISION'])

    def test_different_save_keys_serialize_and_audit_failure_rolls_back(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        from app.schemas.model_pricing import PricingInput
        model = self.register_model()
        def save(rate):
            return self.service.put_model_price(self.auth(), model, str(uuid.uuid4()), PricingInput(**self.price(rate)))[0]
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(save, ['1.0000', '2.0000', '3.0000']))
        final = self.service.get_model_price(self.auth(), model)['pricing']
        self.assertEqual(final, max(results, key=lambda result: result['updated_at']))
        with patch.object(self.service, 'audit', side_effect=RuntimeError('synthetic audit fault')):
            with self.assertRaises(RuntimeError):
                save('4.0000')
        self.assertEqual(self.service.get_model_price(self.auth(), model)['pricing'], final)

    def test_bounded_body_and_unavailable_storage_have_scoped_errors(self):
        from unittest.mock import patch
        from sqlalchemy.exc import OperationalError
        model = self.register_model()
        response = self.client.put('/api/platform-admin/billing/price-rules/' + model, content=b'x'*16385, headers=self.headers())
        self.assertEqual(response.status_code, 413)
        self.assertTrue(response.json()['detail']['request_id'])
        with patch.object(self.service, 'list_model_prices', side_effect=OperationalError('SECRET SQL', {}, Exception('SECRET'))):
            response = self.client.get('/api/platform-admin/billing/price-rules', headers=self.headers())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('SECRET', response.text)

    def test_runtime_registry_matches_auto_asr_route_without_claiming_protocol_evidence(self):
        from app.services.billing.registry import ModelRegistry
        from app.services.llm import resolve_director_asr_provider
        self.settings.llm_api_base = 'https://ark.test.volces.com/api/v3'
        self.settings.director_asr_provider = 'auto'
        self.settings.director_asr_api_key = 'synthetic-not-a-real-key'
        self.settings.embedding_api_key = 'synthetic-independent-slot'
        self.settings.embedding_mode = 'multimodal'
        registry = ModelRegistry.from_settings(self.settings)
        self.assertEqual(resolve_director_asr_provider(self.settings), 'volcengine_asr')
        asr = next(item for item in registry.items.values() if 'ASR' in item.model_types)
        self.assertEqual(asr.provider, 'volcengine:asr')
        self.assertEqual(asr.model_id, self.settings.director_asr_resource_id)
        self.assertTrue(asr.configured)
        self.assertIsNone(asr.provider_model_name)
        self.assertEqual(asr.public()['supported_usage_modes'], [])
        embedding = next(item for item in registry.items.values() if 'EMBEDDING' in item.model_types)
        self.assertEqual(embedding.provider, 'openai_compatible:embedding')
        self.settings.director_asr_api_key = ''
        self.settings.llm_api_key = 'synthetic-llm-key'
        self.assertFalse(next(item for item in ModelRegistry.from_settings(self.settings).items.values() if 'ASR' in item.model_types).configured)

    def test_standard_embedding_uses_llm_slot_even_with_unused_independent_configuration(self):
        from app.services.billing.registry import ModelRegistry
        self.settings.embedding_mode = 'standard'
        self.settings.embedding_model = self.settings.llm_model
        self.settings.embedding_base_url = 'https://unused-embedding.invalid/v1'
        self.settings.embedding_api_key = 'synthetic-independent-key'
        self.settings.llm_api_key = ''
        registry = ModelRegistry.from_settings(self.settings)
        embedding = next(item for item in registry.items.values() if 'EMBEDDING' in item.model_types)
        self.assertEqual(embedding.provider, 'openai_compatible:llm')
        self.assertIn('TEXT', embedding.model_types)
        self.assertFalse(embedding.configured)
        before = embedding.model_key
        self.settings.embedding_base_url = 'https://also-unused.invalid/v1'
        self.settings.llm_api_key = 'synthetic-effective-key'
        embedding = next(item for item in ModelRegistry.from_settings(self.settings).items.values() if 'EMBEDDING' in item.model_types)
        self.assertEqual(embedding.model_key, before)
        self.assertTrue(embedding.configured)

    def test_multimodal_embedding_uses_only_effective_independent_slot_and_key_fallback(self):
        from app.services.billing.registry import ModelRegistry
        self.settings.embedding_mode = 'multimodal'
        self.settings.llm_api_key = 'synthetic-fallback-key'
        for base, key, expected in (('', '', 'openai_compatible:llm'), ('https://embedding.invalid/v1', '', 'openai_compatible:embedding'), ('', 'synthetic-independent', 'openai_compatible:embedding')):
            with self.subTest(base=bool(base), key=bool(key)):
                self.settings.embedding_base_url, self.settings.embedding_api_key = base, key
                embedding = next(item for item in ModelRegistry.from_settings(self.settings).items.values() if 'EMBEDDING' in item.model_types)
                self.assertEqual(embedding.provider, expected)
                self.assertTrue(embedding.configured)

    def test_enabled_video_repair_diagnosis_registers_its_selected_vision_model(self):
        from app.services.billing.registry import ModelRegistry
        self.settings.video_repair_ai_enabled = True
        self.settings.video_repair_ai_model = 'fixture-diagnosis'
        self.settings.llm_api_key = 'synthetic-llm-key'
        registry = ModelRegistry.from_settings(self.settings)
        diagnosis = next((item for item in registry.items.values() if item.model_id == 'fixture-diagnosis'), None)
        self.assertIsNotNone(diagnosis)
        self.assertEqual(diagnosis.provider, 'openai_compatible:llm')
        self.assertEqual(diagnosis.model_types, ('VISION',))
        self.assertTrue(diagnosis.configured)
        self.assertEqual(diagnosis.public()['supported_usage_modes'], [])
        self.settings.video_repair_ai_model = ''
        items = list(ModelRegistry.from_settings(self.settings).items.values())
        self.assertEqual(sum(item.model_id == self.settings.llm_vision_model for item in items), 1)
        self.settings.video_repair_ai_enabled = False
        self.settings.video_repair_ai_model = 'fixture-diagnosis'
        self.assertFalse(any(item.model_id == 'fixture-diagnosis' for item in ModelRegistry.from_settings(self.settings).items.values()))

    def test_image_registry_uses_runtime_provider_default_normalization_and_gate(self):
        from app.services.billing.registry import ModelRegistry
        self.settings.image_generation_model = 'fixture-image'
        self.settings.image_product_model = 'fixture-product'
        self.settings.image_portrait_model = 'fixture-portrait'
        self.settings.llm_api_key = 'synthetic-fallback-key'
        for provider in ('', 'ark_seedream', ' ARK_SEEDREAM '):
            with self.subTest(provider=provider):
                self.settings.image_generation_provider = provider
                images = [item for item in ModelRegistry.from_settings(self.settings).items.values() if 'IMAGE' in item.model_types]
                self.assertEqual({item.model_id for item in images}, {'fixture-image', 'fixture-product', 'fixture-portrait'})
                self.assertTrue(all(item.provider == 'ark_seedream:image' and item.configured for item in images))
        for provider in ('apilio', ' APILIO '):
            with self.subTest(provider=provider):
                self.settings.image_generation_provider = provider
                self.settings.apilio_image_model = 'fixture-apilio'
                self.settings.apilio_image_api_key = 'synthetic-apilio-key'
                images = [item for item in ModelRegistry.from_settings(self.settings).items.values() if 'IMAGE' in item.model_types]
                self.assertEqual([(item.provider, item.model_id, item.configured) for item in images], [('apilio:image', 'fixture-apilio', True)])
        # Ark's override settings cannot select a remote adapter without the
        # base model and credential gate in get_image_provider.
        self.settings.image_generation_provider = ''
        self.settings.image_generation_model = ''
        self.assertFalse(any('IMAGE' in item.model_types for item in ModelRegistry.from_settings(self.settings).items.values()))
        self.settings.image_generation_model = 'fixture-image'
        self.settings.llm_api_key = ''
        self.assertFalse(any('IMAGE' in item.model_types for item in ModelRegistry.from_settings(self.settings).items.values()))

    def test_saved_model_identity_and_terminal_history_are_immutable_at_database_boundary(self):
        from sqlalchemy import text
        model = self.register_model()
        self.put(model)
        for sql in ("UPDATE billing_model_prices SET model_id='different'", 'DELETE FROM billing_model_prices', 'DELETE FROM billing_idempotency'):
            with self.assertRaises(Exception):
                with self.engine.begin() as db:
                    db.execute(text(sql))

    def test_fastapi_export_has_current_models_and_scoped_error_response_schemas(self):
        spec = self.app.openapi()
        for path, method, schema in (('/api/platform-admin/billing/price-rules', 'get', 'ModelPage'),
                                      ('/api/platform-admin/billing/price-rules/{model_key}', 'get', 'Model'),
                                      ('/api/platform-admin/billing/price-rules/{model_key}', 'put', 'Pricing')):
            operation = spec['paths'][path][method]
            self.assertEqual(operation['responses']['200']['content']['application/json']['schema']['$ref'], '#/components/schemas/' + schema)
            self.assertEqual(operation['responses']['422']['content']['application/json']['schema']['$ref'], '#/components/schemas/ModelPricingError')
