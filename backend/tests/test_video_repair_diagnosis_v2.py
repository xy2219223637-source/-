from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.video_repair.ai_provider import DiagnosisProviderResult, build_multimodal_content
from app.services.video_repair.diagnosis_v2 import diagnose_video


def context(reason="6-10秒商品展示不准确", ranges=None):
    return {
        "review": {"raw_reason": reason, "suggestions": [],
                   "explicit_time_ranges": ranges if ranges is not None else [{
                       "start_ms": 6000, "end_ms": 10000, "precision": "EXACT",
                       "source": "TEXT_PARSE", "confidence": .98}],
                   "category_hint": "UNKNOWN"},
        "video": {"duration_ms": 12000, "width": 1080, "height": 1920,
                  "burned_subtitles": False},
        "timeline": {"narrative_segments": [], "video_segments": []},
        "observations": {"asr_segments": [], "ocr_segments": [], "keyframes": []},
        "product": {"product_id": 1, "known_facts": []},
        "capabilities": ["VISUAL_REPLACE", "AV_SEGMENT_REPLACE", "PROCESS_INSERT"],
    }


class FakeProvider:
    def diagnose(self, value):
        return DiagnosisProviderResult(payload={
            "schema_version": "video_repair_diagnosis.v2",
            "summary": "画面与描述不一致",
            "issues": [{
                "issue_id": "issue-1", "category": "VISUAL_MISMATCH",
                "description": "商品展示错误", "affected_modalities": ["visual"],
                "ranges": [{"start_ms": 1000, "end_ms": 2000, "precision": "APPROXIMATE",
                            "source": "MULTIMODAL", "confidence": .7}],
                "evidence": [{"source": "FRAME", "start_ms": 1000, "end_ms": 2000,
                              "description": "画面证据"}],
                "confidence": .9, "suggested_action": "VISUAL_REPLACE",
            }],
            "primary_issue_id": "issue-1", "requires_human_range_confirmation": False,
            "overall_confidence": .9,
            "meta": {"mode": "MULTIMODAL", "model": "ignored"},
        }, model="fake-model", token_usage={"total_tokens": 12})


class TimeoutProvider:
    def diagnose(self, value):
        raise TimeoutError("model timeout")


class SdkFailureProvider:
    def diagnose(self, value):
        raise RuntimeError("provider unavailable")


class VideoRepairDiagnosisV2Tests(unittest.TestCase):
    @patch("app.services.video_repair.ai_provider.get_storage_for_url")
    @patch("app.services.video_repair.ai_provider._image_data_url", return_value="data:image/jpeg;base64,abc")
    def test_multimodal_content_includes_product_reference_and_source_video(self, _image, storage):
        storage.return_value.signed_url.return_value = "https://signed.example/video.mp4"
        value = context()
        value["video"]["source_url"] = "/video.mp4"
        value["product"]["reference_images"] = [{
            "role": "product_front", "description": "商品正面", "url": "/product.jpg",
        }]
        content = build_multimodal_content(value)
        self.assertEqual([row["type"] for row in content], [
            "text", "text", "image_url", "text", "video_url",
        ])
        self.assertEqual(content[2]["image_url"]["url"], "data:image/jpeg;base64,abc")
        self.assertEqual(content[4]["video_url"]["url"], "https://signed.example/video.mp4")
        self.assertNotIn("source_url", content[0]["text"])
        self.assertNotIn("reference_images", content[0]["text"])

    def test_explicit_range_overrides_model_range(self):
        result = diagnose_video(context(), FakeProvider())
        self.assertEqual(result.issues[0].ranges[0].start_ms, 6000)
        self.assertEqual(result.meta.model, "fake-model")
        self.assertEqual(result.meta.mode, "MULTIMODAL")

    def test_timeout_uses_rule_fallback(self):
        result = diagnose_video(context(), TimeoutProvider())
        self.assertEqual(result.meta.mode, "RULE_FALLBACK")
        self.assertEqual(result.issues[0].suggested_action, "VISUAL_REPLACE")
        self.assertIn("TimeoutError", result.meta.error)

    def test_provider_specific_failure_uses_rule_fallback(self):
        result = diagnose_video(context(), SdkFailureProvider())
        self.assertEqual(result.meta.mode, "RULE_FALLBACK")
        self.assertIn("RuntimeError", result.meta.error)

    def test_unknown_without_range_cannot_be_automatically_approved(self):
        result = diagnose_video(context("内容表达不准确，请修改", []), None)
        self.assertEqual(result.issues[0].suggested_action, "UNSUPPORTED")
        self.assertTrue(result.requires_human_range_confirmation)
        self.assertEqual(result.issues[0].ranges, [])


if __name__ == "__main__":
    unittest.main()
