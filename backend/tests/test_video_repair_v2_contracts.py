from __future__ import annotations

import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from app.services.video_repair.capabilities import (
    CapabilityValidationError, validate_diagnosis_against_capabilities,
    validate_plan_against_capabilities,
)
from app.services.video_repair.diagnosis_schema import VideoRepairDiagnosisV2
from app.services.video_repair.time_extraction import extract_time_ranges, prioritized_time_ranges


def diagnosis_payload(**issue_overrides):
    issue = {
        "issue_id": "issue-1",
        "category": "VISUAL_MISMATCH",
        "description": "商品展示与描述不一致",
        "affected_modalities": ["visual"],
        "ranges": [{"start_ms": 6000, "end_ms": 10000, "precision": "EXACT",
                    "source": "TEXT_PARSE", "confidence": .98}],
        "evidence": [{"source": "REVIEW_TEXT", "description": "6-10秒商品展示不准确"}],
        "confidence": .9,
        "suggested_action": "VISUAL_REPLACE",
    }
    issue.update(issue_overrides)
    return {
        "schema_version": "video_repair_diagnosis.v2",
        "summary": "识别到一个问题",
        "issues": [issue],
        "primary_issue_id": "issue-1",
        "requires_human_range_confirmation": False,
        "overall_confidence": .9,
        "meta": {"mode": "MULTIMODAL", "model": "fake"},
    }


class TimeExtractionTests(unittest.TestCase):
    def test_supported_formats(self):
        for value in ("6-10s", "6-10秒", "6~10秒", "6到10秒", "第6秒到第10秒"):
            with self.subTest(value=value):
                self.assertEqual(extract_time_ranges(value, 20_000)[0]["start_ms"], 6000)
                self.assertEqual(extract_time_ranges(value, 20_000)[0]["end_ms"], 10000)
        self.assertEqual(extract_time_ranges("00:06-00:10", 20_000)[0]["end_ms"], 10000)

    def test_bounds_inversion_and_missing(self):
        self.assertEqual(extract_time_ranges("6-30秒", 12_000)[0]["end_ms"], 12000)
        self.assertEqual(extract_time_ranges("10-6秒", 12_000), [])
        self.assertEqual(extract_time_ranges("没有时间信息", 12_000), [])
        self.assertEqual(extract_time_ranges("20-30秒", 12_000), [])

    def test_explicit_input_has_priority(self):
        ranges = prioritized_time_ranges([{"start": 1, "end": 2}], "6-10秒", 12_000)
        self.assertEqual((ranges[0]["start_ms"], ranges[0]["end_ms"]), (1000, 2000))
        self.assertEqual(ranges[0]["source"], "INPUT")


class DiagnosisContractTests(unittest.TestCase):
    def test_valid_payload_and_duration(self):
        model = VideoRepairDiagnosisV2.model_validate(diagnosis_payload()).validate_duration(12_000)
        validate_diagnosis_against_capabilities(model)
        self.assertEqual(model.primary_issue_id, "issue-1")

    def test_unknown_action_and_missing_evidence_are_rejected(self):
        with self.assertRaises(ValidationError):
            VideoRepairDiagnosisV2.model_validate(diagnosis_payload(suggested_action="MAKE_MAGIC"))
        with self.assertRaises(ValidationError):
            VideoRepairDiagnosisV2.model_validate(diagnosis_payload(evidence=[]))

    def test_out_of_bounds_range_is_rejected(self):
        model = VideoRepairDiagnosisV2.model_validate(diagnosis_payload())
        with self.assertRaisesRegex(ValueError, "超出视频时长"):
            model.validate_duration(9000)

    def test_audio_issue_cannot_replace_visual_only(self):
        with self.assertRaises(ValidationError):
            VideoRepairDiagnosisV2.model_validate(
                diagnosis_payload(affected_modalities=["audio"], suggested_action="VISUAL_REPLACE"))
        payload = diagnosis_payload(
            affected_modalities=["audio"], suggested_action="AV_SEGMENT_REPLACE")
        model = VideoRepairDiagnosisV2.model_validate(payload)
        validate_diagnosis_against_capabilities(model)
        model.issues[0].suggested_action = "VISUAL_REPLACE"
        with self.assertRaises(CapabilityValidationError):
            validate_diagnosis_against_capabilities(model)

    def test_burned_subtitles_do_not_block_visual_replacement_approval(self):
        plan = {"actions": [{
            "type": "VISUAL_REPLACE",
            "target_range": {"start_ms": 6000, "end_ms": 10000},
            "affected_tracks": ["video_track"],
            "duration_policy": "KEEP_TOTAL_DURATION",
            "selected_candidate_id": "asset-candidate:highlight-1",
        }]}
        validate_plan_against_capabilities(
            plan, SimpleNamespace(burned_subtitles=True), None)


if __name__ == "__main__":
    unittest.main()
