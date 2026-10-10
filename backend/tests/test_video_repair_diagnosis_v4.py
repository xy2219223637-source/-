from __future__ import annotations

import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

from app.services.video_repair.ai_provider import DiagnosisProviderResult, OpenAICompatibleDiagnosisProvider
from app.services.video_repair.diagnosis_v4 import (
    VideoRepairDiagnosisV4, diagnose_video_v4, rule_fallback_v4,
)
from app.services.video_repair.protocol_v4 import ACTIONS
from app.services.video_repair.worker_v4 import _v4_action


def context(reason: str = "6-10秒商品展示不准确", capabilities=None):
    return {
        "review": {"raw_reason": reason, "suggestions": [], "category_hint": "UNKNOWN",
                   "explicit_time_ranges": [{"start_ms": 6000, "end_ms": 10000,
                                             "precision": "EXACT", "source": "TEXT_PARSE",
                                             "confidence": .98}]},
        "video": {"duration_ms": 12000},
        "capabilities": capabilities or ["VISUAL_REPLACE", "AUDIO_REPLACE", "CONTENT_INSERT"],
    }


class FakeProvider:
    def __init__(self, action: str, modalities: list[str], category: str = "VISUAL_MISMATCH"):
        self.action, self.modalities, self.category = action, modalities, category
        self.schema_model = None

    def diagnose(self, _context, *, schema_model):
        self.schema_model = schema_model
        return DiagnosisProviderResult(payload={
            "schema_version": "video_repair_diagnosis.v2",
            "summary": "商品不一致",
            "issues": [{"issue_id": "issue-1", "category": self.category,
                        "description": "商品不一致", "affected_modalities": self.modalities,
                        "ranges": [{"start_ms": 6000, "end_ms": 10000,
                                    "precision": "EXACT", "source": "TEXT_PARSE", "confidence": .98}],
                        "evidence": [{"source": "REVIEW_TEXT", "description": "审核指出商品不一致"}],
                        "confidence": .98, "suggested_action": self.action}],
            "primary_issue_id": "issue-1", "requires_human_range_confirmation": False,
            "overall_confidence": .98, "meta": {"mode": "MULTIMODAL"},
        }, model="fake-model")


class V4DiagnosisTests(unittest.TestCase):
    def test_v4_contract_matches_executor(self):
        schema = VideoRepairDiagnosisV4.model_json_schema()
        issue = schema["$defs"]["V4DiagnosedIssue"]
        self.assertEqual(set(issue["properties"]["suggested_action"]["enum"]),
                         ACTIONS | {"UNSUPPORTED"})

    def test_legacy_av_suggestion_is_unsupported_without_losing_evidence(self):
        provider = FakeProvider("AV_SEGMENT_REPLACE", ["visual", "audio", "caption"])
        result = diagnose_video_v4(context(), provider)
        self.assertIs(provider.schema_model, VideoRepairDiagnosisV4)
        self.assertEqual(result.meta.mode, "MULTIMODAL")
        self.assertEqual(result.issues[0].suggested_action, "UNSUPPORTED")
        self.assertEqual(result.issues[0].affected_modalities, ["visual", "audio", "caption"])
        self.assertTrue(result.issues[0].evidence)

    def test_real_provider_sends_v4_schema_to_model(self):
        with patch("app.services.video_repair.ai_provider.OpenAI") as sdk:
            sdk.return_value.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"ok": True})))],
                usage=None)
            provider = OpenAICompatibleDiagnosisProvider(
                api_key="test", base_url="https://example.test", model="test-model",
                timeout_seconds=5, prompt_version="v4")
            provider.diagnose(context(), schema_model=VideoRepairDiagnosisV4)
            sent = sdk.return_value.chat.completions.create.call_args.kwargs
            issue_schema = sent["response_format"]["json_schema"]["schema"]["$defs"]["V4DiagnosedIssue"]
            self.assertEqual(set(issue_schema["properties"]["suggested_action"]["enum"]),
                             ACTIONS | {"UNSUPPORTED"})
            self.assertIn("UNSUPPORTED", sent["messages"][0]["content"])

    def test_audio_replacement_never_claims_to_fix_captions(self):
        result = diagnose_video_v4(context(), FakeProvider(
            "AUDIO_REPLACE", ["audio", "caption"], "COPY_AUDIO_CAPTION_RISK"))
        self.assertEqual(result.issues[0].suggested_action, "UNSUPPORTED")

    def test_rule_fallback_keeps_visual_and_rejects_caption_combo(self):
        self.assertEqual(rule_fallback_v4(context()).issues[0].suggested_action,
                         "VISUAL_REPLACE")
        self.assertEqual(rule_fallback_v4(context("6-10秒口播字幕不准确")).issues[0].suggested_action,
                         "UNSUPPORTED")

    def test_provider_failure_does_not_turn_text_rule_into_executable_action(self):
        provider = FakeProvider("VISUAL_REPLACE", ["visual"])
        provider.diagnose = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timeout"))
        result = diagnose_video_v4(context(), provider)
        self.assertEqual(result.meta.mode, "RULE_FALLBACK")
        self.assertEqual(result.issues[0].suggested_action, "UNSUPPORTED")
        self.assertEqual(result.issues[0].ranges[0].start_ms, 6000)

    def test_missing_provider_does_not_claim_multimodal_judgment(self):
        result = diagnose_video_v4(context(), None)
        self.assertEqual(result.meta.mode, "RULE_FALLBACK")
        self.assertEqual(result.issues[0].suggested_action, "UNSUPPORTED")

    def test_v4_mapping_does_not_revive_legacy_actions(self):
        with patch("app.services.video_repair.worker_v4.get_settings", return_value=SimpleNamespace(
                video_repair_audio_replace_enabled=True,
                video_repair_content_insert_enabled=True)):
            self.assertIsNone(_v4_action(SimpleNamespace(suggested_action="AV_SEGMENT_REPLACE")))
            self.assertEqual(_v4_action(SimpleNamespace(suggested_action="VISUAL_REPLACE")),
                             "VISUAL_REPLACE")


if __name__ == "__main__":
    unittest.main()
