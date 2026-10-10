from __future__ import annotations

from copy import deepcopy
import unittest

from app.services.video_editing.plan_patch import apply_repair_plan, insert_segment, replace_range


BASE_CLIP = {
    "asset_id": "asset-v1", "highlight_id": "original-1",
    "source_range": {"start": 10.0, "end": 22.0},
    "timeline_range": {"start": 0.0, "end": 12.0},
    "duration_seconds": 12.0, "content_role": "product_demo",
}
CANDIDATE = {
    "id": "asset-new:highlight-2", "source_asset_public_id": "asset-new",
    "highlight_id": "highlight-2", "source_range": {"start": 3.0, "end": 11.0},
    "content_role": "product_demo",
}


def base_plan() -> dict:
    narrative = deepcopy(BASE_CLIP)
    narrative.update({"speech_text": "原口播", "audio_strategy": "source", "subtitle_words": []})
    return {"variants": [{
        "variant_id": "V1", "actual_duration_seconds": 12.0,
        "target_duration_seconds": 12.0,
        "video_track": [deepcopy(BASE_CLIP)], "narrative_segments": [narrative],
    }]}


class VideoRepairPlanPatchTests(unittest.TestCase):
    def test_visual_replace_splits_clip_and_preserves_audio(self):
        original = base_plan()
        repair = {"actions": [{
            "action_id": "repair-1", "type": "VISUAL_REPLACE",
            "target_range": {"start": 4.0, "end": 8.0},
        }]}
        result = apply_repair_plan(
            original, repair, {"repair-1": CANDIDATE}, {"asset-v1", "asset-new"}, 7, 5)
        video = result["variants"][0]["video_track"]
        self.assertEqual([row["asset_id"] for row in video], ["asset-v1", "asset-new", "asset-v1"])
        self.assertEqual(video[0]["source_range"], {"start": 10.0, "end": 14.0})
        self.assertEqual(video[2]["source_range"], {"start": 18.0, "end": 22.0})
        self.assertEqual(result["variants"][0]["narrative_segments"], original["variants"][0]["narrative_segments"])
        self.assertEqual(original["variants"][0]["video_track"], [BASE_CLIP])

    def test_visual_replace_normalizes_legacy_manual_plan(self):
        original = base_plan()
        original["variants"][0]["edit_mode"] = "manual_compose"
        original["variants"][0]["video_track"] = []
        repair = {"actions": [{
            "action_id": "repair-1", "type": "VISUAL_REPLACE",
            "target_range": {"start": 4.0, "end": 8.0},
        }]}
        result = apply_repair_plan(
            original, repair, {"repair-1": CANDIDATE}, {"asset-v1", "asset-new"}, 12, 5)
        variant = result["variants"][0]
        self.assertEqual([row["asset_id"] for row in variant["video_track"]],
                         ["asset-v1", "asset-new", "asset-v1"])
        self.assertEqual(variant["narrative_segments"], original["variants"][0]["narrative_segments"])

    def test_short_candidate_is_rejected_instead_of_looped(self):
        candidate = deepcopy(CANDIDATE)
        candidate["source_range"] = {"start": 1.0, "end": 2.0}
        with self.assertRaisesRegex(ValueError, "禁止无提示循环"):
            replace_range([BASE_CLIP], {"start": 4.0, "end": 8.0}, candidate)

    def test_process_insert_shifts_following_timeline(self):
        candidate = deepcopy(CANDIDATE)
        candidate["source_range"] = {"start": 3.0, "end": 5.0}
        result = insert_segment([BASE_CLIP], 4.0, candidate, audio=False)
        self.assertEqual([(row["timeline_range"]["start"], row["timeline_range"]["end"])
                          for row in result], [(0.0, 4.0), (4.0, 6.0), (6.0, 14.0)])
        self.assertEqual(result[-1]["source_range"], {"start": 14.0, "end": 22.0})

    def test_av_replace_updates_both_tracks(self):
        original = base_plan()
        repair = {"actions": [{"action_id":"repair-1","type":"AV_SEGMENT_REPLACE",
                                "target_range":{"start":4.0,"end":8.0}}]}
        result = apply_repair_plan(
            original, repair, {"repair-1":CANDIDATE}, {"asset-v1","asset-new"}, 8, 5)
        variant = result["variants"][0]
        self.assertEqual(variant["video_track"][1]["asset_id"], "asset-new")
        self.assertEqual(variant["narrative_segments"][1]["asset_id"], "asset-new")
        self.assertEqual(variant["narrative_segments"][1]["audio_strategy"], "source")
        self.assertEqual(variant["narrative_segments"][1]["subtitle_words"], [])
        self.assertEqual(variant["narrative_segments"][1]["audio_fade_seconds"], .1)

    def test_v2_millisecond_target_is_converted_once(self):
        repair = {"schema_version": "video_repair_plan.v2", "actions": [{
            "action_id": "repair-1", "type": "VISUAL_REPLACE",
            "target_range": {"start_ms": 4000, "end_ms": 8000},
        }]}
        result = apply_repair_plan(
            base_plan(), repair, {"repair-1": CANDIDATE}, {"asset-v1", "asset-new"}, 13, 5)
        self.assertEqual(result["variants"][0]["video_track"][1]["timeline_range"],
                         {"start": 4.0, "end": 8.0})

    def test_av_replace_removes_only_target_subtitles(self):
        original = base_plan()
        original["variants"][0]["narrative_segments"][0]["subtitle_words"] = [
            {"text": "前", "start": 1.0, "end": 2.0},
            {"text": "错", "start": 5.0, "end": 6.0},
            {"text": "后", "start": 9.0, "end": 10.0},
        ]
        repair = {"actions": [{"action_id": "repair-1", "type": "AV_SEGMENT_REPLACE",
                                "target_range": {"start": 4.0, "end": 8.0}}]}
        result = apply_repair_plan(
            original, repair, {"repair-1": CANDIDATE}, {"asset-v1", "asset-new"}, 14, 5)
        narrative = result["variants"][0]["narrative_segments"]
        self.assertEqual([word["text"] for word in narrative[0]["subtitle_words"]], ["前"])
        self.assertEqual(narrative[1]["subtitle_words"], [])
        self.assertEqual([word["text"] for word in narrative[2]["subtitle_words"]], ["后"])

    def test_process_insert_rejects_unknown_timeline_track(self):
        original = base_plan()
        original["variants"][0]["effect_track"] = [{
            "timeline_range": {"start": 2.0, "end": 3.0}, "type": "sparkle"}]
        candidate = deepcopy(CANDIDATE); candidate["source_range"] = {"start": 3.0, "end": 5.0}
        repair = {"actions": [{"action_id": "repair-1", "type": "PROCESS_INSERT",
                                "target_range": {"start": 4.0, "end": 4.1}}]}
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_TIMELINE_TRACK_FOR_INSERT"):
            apply_repair_plan(
                original, repair, {"repair-1": candidate}, {"asset-v1", "asset-new"}, 15, 5)

    def test_audio_caption_rewrite_only_accepts_system_voiceover(self):
        original = base_plan()
        narrative = original["variants"][0]["narrative_segments"][0]
        narrative.update({"audio_strategy":"ai_voiceover","speech_text":"百分百最好用",
                          "audio_url":"/voice.mp3"})
        repair = {"actions": [{"action_id":"rewrite-1","type":"AUDIO_CAPTION_REWRITE",
                                "target_range":{"start":0.0,"end":12.0}}]}
        result = apply_repair_plan(original,repair,{}, {"asset-v1"},9,5)
        rewritten = result["variants"][0]["narrative_segments"][0]
        self.assertEqual(rewritten["speech_text"], "用")
        self.assertTrue(rewritten["needs_tts_regeneration"])
        human = base_plan()
        human["variants"][0]["narrative_segments"][0]["speech_text"]="百分百最好用"
        with self.assertRaisesRegex(ValueError,"真人原声"):
            apply_repair_plan(human,repair,{}, {"asset-v1"},10,5)

    def test_process_insert_shifts_audio_video_and_output_duration(self):
        candidate = deepcopy(CANDIDATE);candidate["source_range"]={"start":3.0,"end":5.0}
        repair={"actions":[{"action_id":"repair-1","type":"PROCESS_INSERT",
                            "target_range":{"start":4.0,"end":4.1}}]}
        result=apply_repair_plan(base_plan(),repair,{"repair-1":candidate},{"asset-v1","asset-new"},11,5)
        variant=result["variants"][0]
        self.assertEqual(variant["actual_duration_seconds"],14.0)
        self.assertEqual(variant["video_track"][-1]["timeline_range"]["end"],14.0)
        self.assertEqual(variant["narrative_segments"][-1]["timeline_range"]["end"],14.0)


if __name__ == "__main__":
    unittest.main()
