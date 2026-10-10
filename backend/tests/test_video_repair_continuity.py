from __future__ import annotations

import unittest

from app.services.video_repair.context_window import build_repair_context
from app.services.video_repair.continuity import assess_continuity


class RepairContextTests(unittest.TestCase):
    def test_context_window_is_clamped_at_video_edges(self):
        context = build_repair_context({
            "video": {"duration_ms": 10_000, "width": 1080, "height": 1920},
            "timeline": {"narrative_segments": [
                {"start_ms": 0, "end_ms": 3000, "speech_text": "开场问题"},
                {"start_ms": 3000, "end_ms": 8000, "speech_text": "产品演示"},
                {"start_ms": 8000, "end_ms": 10_000, "speech_text": "效果结论"},
            ]},
            "observations": {},
        }, requested_range={"start_ms": 500, "end_ms": 1500}, target_intent="替换误导画面")
        self.assertEqual(context["schema_version"], "repair_context.v1")
        self.assertEqual(context["context_window"]["pre"]["start_ms"], 0)
        self.assertEqual(context["context_window"]["post"]["end_ms"], 5500)


class ContinuityScoreTests(unittest.TestCase):
    def test_rule_fallback_never_claims_recommended(self):
        candidate = {
            "description": "产品上脸涂抹并展示效果", "duration_seconds": 4,
            "asset_kind": "material", "quality_score": .95, "same_orientation": True,
        }
        context = {"narrative": {
            "target_intent": "产品上脸涂抹并展示效果", "target_summary": "上脸涂抹",
            "pre_summary": "展示皮肤问题", "post_summary": "展示产品效果",
            "entities": ["产品"], "actions": ["涂抹"],
            "required_bridge": "从问题过渡到效果",
        }}
        score = assess_continuity(candidate, context, target_duration=4)
        self.assertGreaterEqual(score["score"], .60)
        self.assertEqual(score["recommendation_level"], "SELECTABLE_WITH_WARNING")
        self.assertIn("未执行语义模型评估", " ".join(score["warnings"]))

    def test_rule_fallback_low_score_remains_manually_selectable(self):
        score = assess_continuity({
            "description": "无关远景", "duration_seconds": 30,
            "asset_kind": "derived", "quality_score": .1, "same_orientation": False,
        }, {"narrative": {
            "target_intent": "人物近景涂抹", "pre_summary": "人物近景",
            "post_summary": "人物近景", "entities": ["人物"], "actions": ["涂抹"],
        }}, target_duration=2)
        self.assertLess(score["score"], .60)
        self.assertEqual(score["recommendation_level"], "SELECTABLE_WITH_WARNING")

    def test_semantic_model_low_score_is_below_threshold(self):
        score = assess_continuity({
            "description": "无关远景", "duration_seconds": 30,
            "asset_kind": "derived", "quality_score": .1, "same_orientation": False,
        }, {"narrative": {
            "target_intent": "人物近景涂抹", "pre_summary": "人物近景",
            "post_summary": "人物近景", "entities": ["人物"], "actions": ["涂抹"],
        }}, target_duration=2, semantic_mode="SEMANTIC_MODEL")
        self.assertEqual(score["recommendation_level"], "BELOW_THRESHOLD")


if __name__ == "__main__":
    unittest.main()
