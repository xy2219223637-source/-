"""Unbound business paths fail before SDK, JSON retry, or storage side effects."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from app.config import Settings


class UnboundCallTests(unittest.TestCase):
    def test_legacy_llm_entrypoints_cannot_bypass_model_ledger_including_mock_json(self):
        from app.services.llm import LLMClient
        settings = Settings(_env_file=None, billing_enabled=True, llm_mock_mode=True)
        with patch('app.services.llm.get_settings', return_value=settings):
            client = LLMClient()
        with patch.object(client.client.chat.completions, 'create', new_callable=AsyncMock) as send, \
                patch.object(client, '_resolve_image_url', new_callable=AsyncMock) as resolve:
            for operation in (
                    lambda: client.chat([{'role': 'user', 'content': 'fixture'}]),
                    lambda: client.chat_json([{'role': 'user', 'content': 'fixture'}]),
                    lambda: client.chat_vision('fixture', ['https://fixture.invalid/image']),
                    lambda: client.embed(['fixture']),
                    lambda: client.transcribe('nonexistent-fixture.wav')):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(operation())
                self.assertEqual(error.exception.detail['code'], 'BILLING_CONTEXT_REQUIRED')
            send.assert_not_awaited()
            resolve.assert_not_awaited()
        asyncio.run(client.client.close())

    def test_independent_video_repair_sdk_refuses_before_storage_and_all_fallbacks(self):
        from app.services.video_repair.ai_provider import OpenAICompatibleDiagnosisProvider
        settings = Settings(_env_file=None, billing_enabled=True)
        provider = OpenAICompatibleDiagnosisProvider(api_key='synthetic-only', base_url='https://fixture.invalid',
            model='fixture', timeout_seconds=1, prompt_version='fixture')
        with patch('app.services.video_repair.ai_provider.get_settings', return_value=settings), \
                patch('app.services.video_repair.ai_provider.build_multimodal_content') as content, \
                patch.object(provider.client.chat.completions, 'create') as send:
            with self.assertRaises(HTTPException) as error:
                provider.diagnose({'video': {'source_url': 'https://fixture.invalid/video'}})
            self.assertEqual(error.exception.detail['code'], 'BILLING_CONTEXT_REQUIRED')
            content.assert_not_called()
            send.assert_not_called()
        provider.client.close()
