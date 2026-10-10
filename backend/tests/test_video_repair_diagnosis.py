from __future__ import annotations

import unittest

from app.services.video_repair.diagnosis import diagnose_feedback


def plan(audio_strategy: str = "ai_voiceover") -> dict:
    return {"variants": [{
        "actual_duration_seconds": 12,
        "narrative_segments": [{
            "speech_text": "这是最有效的商品，百分百见效",
            "audio_strategy": audio_strategy,
            "timeline_range": {"start": 2, "end": 6},
        }],
    }]}


class VideoRepairDiagnosisTests(unittest.TestCase):
    def test_provider_range_has_priority_and_is_platform_fact(self):
        result = diagnose_feedback("画面可能引发误解", [{"start": 6, "end": 10}], plan())
        self.assertEqual(result["recommended_action"], "VISUAL_REPLACE")
        self.assertEqual(result["issue_ranges"], [{
            "start": 6.0, "end": 10.0, "precision": "EXACT", "source": "INPUT",
            "confidence": 1.0, "evidence": "输入事实包含明确时间段",
        }])

    def test_copy_can_map_to_subtitle_but_never_claims_exact(self):
        result = diagnose_feedback("字幕包含“百分百”绝对化表述", [], plan())
        self.assertEqual(result["recommended_action"], "AUDIO_CAPTION_REWRITE")
        self.assertEqual(result["issue_ranges"][0]["precision"], "APPROXIMATE")
        self.assertEqual(result["issue_ranges"][0]["source"], "SUBTITLE_ASR")

    def test_no_evidence_does_not_invent_a_full_video_range(self):
        result = diagnose_feedback("内容存在未知风险", [], plan())
        self.assertEqual(result["recommended_action"], "FULL_REPLAN")
        self.assertEqual(result["issue_ranges"], [])

    def test_non_video_problem_stops_repair(self):
        result = diagnose_feedback("商品资质不完整", [{"start": 1, "end": 2}], plan())
        self.assertFalse(result["content_fixable"])
        self.assertEqual(result["recommended_action"], "NON_VIDEO_FIXABLE")
        self.assertEqual(result["issue_ranges"], [])


if __name__ == "__main__":
    unittest.main()
