"""Audio MOCK accounting through the same public billing boundary."""
import base64
from datetime import datetime, timezone
from io import BytesIO
import wave
from dataclasses import replace
from pathlib import Path
import struct
from unittest.mock import patch

from fastapi import HTTPException

from test_billing_management import BillingFixture


class AudioMeteringTests(BillingFixture):
    @staticmethod
    def wav(frames=8000):
        output = BytesIO()
        with wave.open(output, 'wb') as audio:
            audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x00\x10' * frames)
        return output.getvalue()

    def setUp(self):
        super().setUp()
        from app.services.billing.orders import BillingOrderService
        from app.services.billing.registry import ModelRegistration, ModelRegistry, UsageContract
        self.models = {kind: ModelRegistration('offline:' + kind.lower(), 'local-' + kind.lower(),
            (kind,), True, usage_contract=UsageContract((), (metric,), 'tests/local-audio-v1'))
            for kind, metric in (('TTS', 'character_count'), ('ASR', 'audio_milliseconds'))}
        self.service = BillingOrderService(self.sessions, self.settings,
            lambda: self.time or datetime.now(timezone.utc), ModelRegistry(self.models.values()))
        self.grant('1000')
        self.quota(value='100')
        for kind in self.models:
            self.price(kind)

    def price(self, kind, rate='1.0000'):
        configuration = {'usage_mode': 'FOLDED_TOKENS', 'metrics': [
            {'metric': 'converted_tokens', 'unit_size': 1, 'credits_per_unit': rate}],
            'conversion': {'source_metric': 'character_count' if kind == 'TTS' else 'audio_milliseconds',
                           'source_unit_size': 1000, 'tokens_per_unit': 1}}
        response = self.client.put('/api/platform-admin/billing/price-rules/' + self.models[kind].model_key,
            json=configuration, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        return configuration

    def request(self, kind, key='1', **payload):
        from app.services.billing.metering import BillingContext, BoundedRequest
        data = {'text': ' A😀e\u0301\n', 'resource': 'local-tone'} if kind == 'TTS' else {
            'audio_base64': base64.b64encode(self.wav()).decode('ascii'), 'route': 'local'}
        data.update(payload)
        metric, quantity = ('character_count', len(data['text'])) if kind == 'TTS' else ('audio_milliseconds', 1000)
        return (BillingContext('a', 'member', f'audio:fixture:{kind}:{key}', 'creative_projects.access', 'MOCK'),
            self.models[kind].model_key, BoundedRequest(kind, data, {metric: quantity},
                'tests/bounded-local-audio', 'mock-audio:pcm8000:mono:v1'))

    def test_tts_counts_original_unicode_code_points_and_returns_decodable_audio(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        with patch('httpx.Client.send', side_effect=AssertionError('no vendor')), \
                patch('httpx.AsyncClient.send', side_effect=AssertionError('no vendor')), \
                patch('app.services.storage.get_storage', side_effect=AssertionError('no external storage')):
            result = self.service.execute_metered(*self.request('TTS'), adapter=AudioMockAdapter('TTS'))
        self.assertEqual(result.usage['raw_quantity'], 6)
        self.assertEqual(result.usage['converted_tokens'], '0.006')
        self.assertEqual(result.result['text'], ' A😀e\u0301\n')
        with wave.open(BytesIO(base64.b64decode(result.result['audio']['wav_base64'])), 'rb') as audio:
            self.assertEqual((audio.getnframes(), audio.getframerate()), (8000, 8000))
        order = self.service.get_model_order(self.auth('member'), result.order_id)
        self.assertEqual((order['status'], order['charged_credits']), ('SETTLED', '0.0060'))
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '999.9940')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.0060')

    def test_asr_uses_input_wave_frames_not_transcript_length_and_local_lifecycle(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        adapter = AudioMockAdapter('ASR')
        with patch('httpx.Client.send', side_effect=AssertionError('no vendor')), \
                patch('httpx.AsyncClient.send', side_effect=AssertionError('no vendor')), \
                patch('app.services.storage.get_storage', side_effect=AssertionError('no external storage')):
            result = self.service.execute_metered(*self.request('ASR'), adapter=adapter)
        self.assertEqual(result.usage['raw_quantity'], 1000)
        self.assertEqual(result.usage['converted_tokens'], '1')
        self.assertEqual(result.result['audio']['frames'], 8000)
        self.assertEqual(result.result['audio']['sample_rate'], 8000)
        self.assertEqual(result.result['segments'], [{'text': '本地模拟转写', 'start_ms': 0, 'end_ms': 1000}])
        self.assertEqual((adapter.submit_count, adapter.poll_count), (1, 2))
        self.assertEqual(adapter.temporary_objects, {})
        self.assertEqual(result.result['cleanup']['status'], 'CLEANED')
        order = self.service.get_model_order(self.auth('member'), result.order_id)
        self.assertEqual(order['charged_credits'], '1.0000')
        self.assertEqual(order['execution_mode'], 'MOCK')
        self.assertEqual(order['supplier_cost']['status'], 'NOT_INCURRED')

    def test_invalid_specification_audio_and_bounds_fail_before_submit_or_reservation(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind in self.models:
            context, model, request = self.request(kind)
            bad_payloads = ([{'text': 123, 'resource': 'local-tone'}, {'text': 'x', 'resource': 'real-resource'}]
                if kind == 'TTS' else [{'audio_base64': 'not base64', 'route': 'local'},
                    {'audio_base64': base64.b64encode(b'bad wav').decode(), 'route': 'local'},
                    {'audio_base64': base64.b64encode(self.wav()[:-2]).decode(), 'route': 'local'},
                    {**request.payload, 'route': 'auto'}, {**request.payload, 'route': 'openai_compatible'}])
            cases = [replace(request, specification=None), replace(request, maximum_usage={'input_tokens': 1}),
                replace(request, maximum_usage={next(iter(request.maximum_usage)): True}),
                replace(request, maximum_usage={next(iter(request.maximum_usage)): 1}),
                *[replace(request, payload=value) for value in bad_payloads]]
            for index, bad_request in enumerate(cases):
                with self.subTest(kind=kind, case=index):
                    adapter = AudioMockAdapter(kind)
                    with self.assertRaises(HTTPException) as error:
                        self.service.execute_metered(context, model, bad_request, adapter=adapter)
                    self.assertEqual(error.exception.detail['code'], 'PRICE_SPEC_MISMATCH')
                    self.assertEqual(adapter.submit_count, 0)
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '0.0000')

    def test_legacy_tts_rejects_before_network_and_output_write(self):
        from app.services import tts
        with patch.object(tts, 'get_settings', return_value=self.settings), \
                patch('httpx.Client', side_effect=AssertionError('no vendor')), \
                patch.object(Path, 'write_bytes', side_effect=AssertionError('no output write')):
            with self.assertRaises(HTTPException) as error:
                tts.synthesize_speech('must not send', Path('must-not-create.wav'))
        self.assertEqual(error.exception.detail['code'], 'BILLING_CONTEXT_REQUIRED')

    def test_partial_audio_and_transcripts_keep_confirmed_consumption_separate_from_success(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind, expected in (('TTS', '0.0060'), ('ASR', '1.0000')):
            with self.subTest(kind=kind):
                result = self.service.execute_metered(*self.request(kind), adapter=AudioMockAdapter(kind, scenario='partial'))
                self.assertEqual((result.execution_status, result.result['output_status']), ('FAILED', 'PARTIAL'))
                order = self.service.get_model_order(self.auth('member'), result.order_id)
                self.assertEqual((order['status'], order['charged_credits']), ('SETTLED', expected))
                self.assertTrue(result.result['segments'])
                if kind == 'TTS':
                    self.assertEqual(result.result['audio']['frames'], 4000)
                    self.assertEqual(result.result['audio']['duration_ms'], 500)

    def test_missing_usage_retains_reservation_for_each_audio_capability(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind, reserved in (('TTS', '0.0060'), ('ASR', '1.0000')):
            with self.subTest(kind=kind):
                result = self.service.execute_metered(*self.request(kind), adapter=AudioMockAdapter(kind, scenario='missing_usage'))
                order = self.service.get_model_order(self.auth('member'), result.order_id)
                self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
                self.assertEqual(order['usage']['source'], 'UNKNOWN')
                self.assertIsNone(order['usage']['raw_quantity'])
                self.assertEqual((order['reserved_credits'], order['charged_credits']), (reserved, '0.0000'))
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.0000')

    def test_empty_audio_output_is_not_evidence_of_zero_tts_consumption(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        result = self.service.execute_metered(*self.request('TTS'), adapter=AudioMockAdapter('TTS', scenario='empty'))
        self.assertEqual((result.execution_status, result.result['output_status']), ('FAILED', 'EMPTY'))
        order = self.service.get_model_order(self.auth('member'), result.order_id)
        self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
        self.assertIsNone(order['usage']['raw_quantity'])

    def test_empty_asr_transcript_does_not_erase_verified_input_duration(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        result = self.service.execute_metered(*self.request('ASR'), adapter=AudioMockAdapter('ASR', scenario='empty'))
        self.assertEqual((result.result['text'], result.result['segments']), ('', []))
        self.assertEqual((result.execution_status, result.usage['raw_quantity']), ('FAILED', 1000))
        self.assertEqual(self.service.get_model_order(self.auth('member'), result.order_id)['charged_credits'], '1.0000')

    def test_bounded_poll_timeout_is_unknown_and_replay_does_not_resubmit(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind, reserved in (('TTS', '0.0060'), ('ASR', '1.0000')):
            adapter = AudioMockAdapter(kind, scenario='timeout', max_polls=3)
            result = self.service.execute_metered(*self.request(kind), adapter=adapter)
            order = self.service.get_model_order(self.auth('member'), result.order_id)
            self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
            self.assertEqual((order['reserved_credits'], order['charged_credits']), (reserved, '0.0000'))
            self.assertEqual(result.execution_status, 'UNKNOWN')
            self.assertEqual((adapter.submit_count, adapter.poll_count), (1, 3))
            self.assertEqual(adapter.temporary_objects, {})
            self.assertEqual(self.service.execute_metered(*self.request(kind), adapter=adapter), result)
            self.assertEqual((adapter.submit_count, adapter.poll_count), (1, 3))

    def test_proved_no_usage_cancels_for_tts_and_asr_without_submission(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind in self.models:
            adapter = AudioMockAdapter(kind, scenario='no_usage')
            result = self.service.execute_metered(*self.request(kind), adapter=adapter)
            order = self.service.get_model_order(self.auth('member'), result.order_id)
            self.assertEqual((order['status'], order['reserved_credits'], order['charged_credits']),
                ('CANCELED', '0.0000', '0.0000'))
            self.assertEqual((adapter.submit_count, adapter.poll_count), (0, 0))
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '0.0000')

    def test_empty_input_or_no_audio_track_is_proven_before_submission(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind, payload in (('TTS', {'text': ''}), ('ASR', {'audio_base64': ''}),
                ('ASR', {'audio_base64': base64.b64encode(self.wav(0)).decode()})):
            adapter = AudioMockAdapter(kind)
            result = self.service.execute_metered(*self.request(kind, key=str(len(payload.get('audio_base64', ''))), **payload), adapter=adapter)
            self.assertEqual(self.service.get_model_order(self.auth('member'), result.order_id)['status'], 'CANCELED')
            self.assertEqual(adapter.submit_count, 0)

    def test_invalid_generated_audio_retains_reservation_instead_of_claiming_zero(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind, reserved in (('TTS', '0.0060'), ('ASR', '1.0000')):
            with self.subTest(kind=kind):
                result = self.service.execute_metered(*self.request(kind), adapter=AudioMockAdapter(kind, scenario='invalid_audio'))
                self.assertEqual((result.execution_status, result.result['output_status']), ('FAILED', 'INVALID_AUDIO'))
                self.assertIsNone(result.result['audio'])
                order = self.service.get_model_order(self.auth('member'), result.order_id)
                self.assertEqual((order['reconciliation_status'], order['reserved_credits']), ('NEEDS_REVIEW', reserved))
                self.assertIsNone(order['usage']['raw_quantity'])

    def test_cleanup_failure_is_observable_recoverable_and_does_not_repeat_model_or_charge(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind in self.models:
            adapter = AudioMockAdapter(kind, cleanup_failures=1)
            with self.assertLogs('app.services.billing.audio_adapter', level='WARNING'):
                result = self.service.execute_metered(*self.request(kind), adapter=adapter)
            self.assertEqual(result.result['cleanup']['status'], 'NEEDS_CLEANUP')
            self.assertEqual(len(adapter.temporary_objects), 1)
            self.assertEqual(adapter.retry_cleanup(), {'status': 'CLEANED', 'pending': []})
            self.assertEqual(adapter.temporary_objects, {})
            self.assertEqual(self.service.execute_metered(*self.request(kind), adapter=adapter), result)
            self.assertEqual((adapter.submit_count, adapter.poll_count), (1, 2))
        charges = [row for row in self.service.ledger(self.auth())['items'] if row['event_type'] == 'CHARGE']
        self.assertEqual(len(charges), 2)
        self.assertTrue(self.service.check_accounts()['ok'])

    def test_price_roundtrip_and_wrong_source_are_checked_for_each_capability(self):
        for kind in self.models:
            with self.subTest(kind=kind):
                configuration = self.price(kind)
                path = '/api/platform-admin/billing/price-rules/' + self.models[kind].model_key
                self.assertEqual(self.client.get(path, headers=self.headers()).json()['pricing']['configuration'], configuration)
                wrong_metric = 'audio_milliseconds' if kind == 'TTS' else 'character_count'
                body = {**configuration, 'conversion': {**configuration['conversion'], 'source_metric': wrong_metric}}
                self.assertEqual(self.client.put(path, json=body, headers=self.headers()).status_code, 422)
                self.assertEqual(self.client.put(path, json=configuration, headers=self.headers('member')).status_code, 403)

    def test_audio_price_snapshot_replay_and_member_enterprise_queries_are_consistent(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind, original, current in (('TTS', '0.0060', '0.0120'), ('ASR', '1.0000', '2.0000')):
            context, key, request = self.request(kind)
            self.service.reserve_model(context, key, request)
            self.price(kind, '2.0000')
            adapter = AudioMockAdapter(kind)
            first = self.service.execute_metered(context, key, request, adapter=adapter)
            self.assertEqual(self.service.execute_metered(context, key, request, adapter=adapter), first)
            self.assertEqual(adapter.submit_count, 1)
            next_result = self.service.execute_metered(*self.request(kind, key='2'), adapter=AudioMockAdapter(kind))
            for prefix, user in (('/api/me/billing/model-orders', 'member'),
                    ('/api/tenant-admin/billing/model-orders', 'owner')):
                old = self.client.get(prefix + '/' + first.order_id, headers=self.headers(user))
                self.assertEqual(old.status_code, 200, old.text)
                self.assertEqual(old.json()['charged_credits'], original)
                self.assertEqual(old.json()['pricing_snapshot']['metrics'][0]['credits_per_unit'], '1.0000')
                new = self.client.get(prefix + '/' + next_result.order_id, headers=self.headers(user)).json()
                self.assertEqual(new['charged_credits'], current)
            for user in ('other', 'member2'):
                self.assertEqual(self.client.get('/api/me/billing/model-orders/' + first.order_id,
                    headers=self.headers(user)).status_code, 404)
        charges = [row for row in self.service.ledger(self.auth())['items'] if row['event_type'] == 'CHARGE']
        self.assertEqual(len(charges), 4)
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '996.9820')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '3.0180')
        self.assertTrue(self.service.check_accounts()['ok'])

    def test_audio_ledger_failure_rolls_back_then_recovers_without_resubmission(self):
        from sqlalchemy import event
        from app.services.billing.audio_adapter import AudioMockAdapter
        def reject(conn, cursor, statement, parameters, context, many):
            if statement.startswith('INSERT INTO credit_ledger'):
                raise RuntimeError('synthetic audio ledger failure')
        for kind in self.models:
            adapter = AudioMockAdapter(kind)
            before = self.service.balance(self.auth())['balance_credits']
            used = self.service.own_quota(self.auth('member'))['used_credits']
            event.listen(self.engine, 'before_cursor_execute', reject)
            try:
                with self.assertRaises(RuntimeError):
                    self.service.execute_metered(*self.request(kind), adapter=adapter)
            finally:
                event.remove(self.engine, 'before_cursor_execute', reject)
            order = next(row for row in self.service.model_orders(self.auth('member'))['items']
                if row['model_key'] == self.models[kind].model_key)
            self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
            self.assertEqual(self.service.balance(self.auth())['balance_credits'], before)
            self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], used)
            self.service.settle_model(order['id'])
            self.assertEqual(self.service.execute_metered(*self.request(kind), adapter=adapter).order_id, order['id'])
            self.assertEqual(adapter.submit_count, 1)
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '998.9940')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '1.0060')
        self.assertEqual(len([row for row in self.service.ledger(self.auth())['items'] if row['event_type'] == 'CHARGE']), 2)
        self.assertTrue(self.service.check_accounts()['ok'])

    def test_fractional_millisecond_audio_is_rejected_before_charge_even_at_high_price(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        self.price('ASR', '100.0000')
        for frames in (1, 7, 9, 7999):
            adapter = AudioMockAdapter('ASR')
            with self.subTest(frames=frames):
                with self.assertRaises(HTTPException) as error:
                    self.service.execute_metered(*self.request('ASR', audio_base64=base64.b64encode(self.wav(frames)).decode()),
                        adapter=adapter)
                self.assertEqual(error.exception.detail['code'], 'PRICE_SPEC_MISMATCH')
                self.assertEqual((adapter.submit_count, adapter.poll_count), (0, 0))
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '1000.0000')
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '0.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.0000')

    def test_integral_one_millisecond_audio_uses_eight_verified_frames(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        result = self.service.execute_metered(*self.request('ASR', audio_base64=base64.b64encode(self.wav(8)).decode()),
            adapter=AudioMockAdapter('ASR'))
        self.assertEqual((result.result['audio']['frames'], result.result['audio']['sample_rate']), (8, 8000))
        self.assertEqual((result.usage['raw_quantity'], result.usage['converted_tokens']), (1, '0.001'))
        self.assertEqual(self.service.get_model_order(self.auth('member'), result.order_id)['charged_credits'], '0.0010')

    def test_odd_pcm_data_chunk_rejects_asr_before_reservation_and_submit(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        data = bytearray(self.wav())
        data += b'\x7f\x00'  # Half a PCM frame plus legal RIFF padding.
        struct.pack_into('<I', data, 4, len(data) - 8)
        struct.pack_into('<I', data, 40, 16001)
        adapter = AudioMockAdapter('ASR')
        with self.assertRaises(HTTPException) as error:
            self.service.execute_metered(*self.request('ASR', audio_base64=base64.b64encode(data).decode()), adapter=adapter)
        self.assertEqual(error.exception.detail['code'], 'PRICE_SPEC_MISMATCH')
        self.assertEqual((adapter.submit_count, adapter.poll_count), (0, 0))
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)
        self.assertEqual(self.service.balance(self.auth())['reserved_credits'], '0.0000')
        self.assertEqual(self.service.balance(self.auth())['balance_credits'], '1000.0000')
        self.assertEqual(self.service.own_quota(self.auth('member'))['used_credits'], '0.0000')

    def test_odd_pcm_tts_response_is_failed_with_unknown_usage_and_retained_reservation(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        data = bytearray(self.wav())
        data += b'\x7f\x00'
        struct.pack_into('<I', data, 4, len(data) - 8)
        struct.pack_into('<I', data, 40, 16001)
        adapter = AudioMockAdapter('TTS')
        with patch('app.services.billing.audio_adapter.local_wav', return_value=bytes(data)):
            result = self.service.execute_metered(*self.request('TTS'), adapter=adapter)
        self.assertEqual((result.execution_status, result.result['output_status']), ('FAILED', 'INVALID_AUDIO'))
        self.assertIsNone(result.result['audio'])
        self.assertIsNone(result.usage['raw_quantity'])
        order = self.service.get_model_order(self.auth('member'), result.order_id)
        self.assertEqual((order['status'], order['reconciliation_status']), ('RESERVED', 'NEEDS_REVIEW'))
        self.assertEqual((order['reserved_credits'], order['charged_credits']), ('0.0060', '0.0000'))
        self.assertEqual(adapter.submit_count, 1)
        self.assertEqual(adapter.temporary_objects, {})

    def test_audio_quota_and_business_permission_denials_do_not_submit(self):
        from app.services.billing.audio_adapter import AudioMockAdapter
        for kind in self.models:
            self.price(kind, '100000.0000')
            adapter = AudioMockAdapter(kind)
            with self.assertRaises(HTTPException) as error:
                self.service.execute_metered(*self.request(kind), adapter=adapter)
            self.assertIn(error.exception.detail['code'], ('INSUFFICIENT_CREDITS', 'QUOTA_EXCEEDED'))
            self.assertEqual(adapter.submit_count, 0)
            context, model, request = self.request(kind)
            with self.assertRaises(HTTPException) as error:
                self.service.execute_metered(replace(context, user_id='limited'), model, request, adapter=adapter)
            self.assertEqual(error.exception.detail['code'], 'FORBIDDEN')
            self.assertEqual(adapter.submit_count, 0)
        self.assertEqual(self.service.model_orders(self.auth('member'))['total'], 0)
