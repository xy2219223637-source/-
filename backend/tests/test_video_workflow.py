import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.api.auto_edit import (
    _starts_with_dangling_anaphor,
    _manual_plan,
    _validate_plan,
    asset_eligible_for_project,
    finished_clip_ffmpeg_command,
    manual_clip_ffmpeg_command,
    finished_output_metadata,
    origin_ids_from_variant,
    remix_render_adds_public_bgm,
    remix_render_uses_system_captions,
    score_transition,
)
from app.services.video_analysis_queue import recover_interrupted_video_analyses
from app.api.video_library import _apply_reusable_status


class VideoWorkflowTests(unittest.TestCase):
    def test_reusable_status_keeps_exclusion_flag_consistent(self):
        asset = SimpleNamespace(reusable_status="excluded", excluded=True)
        _apply_reusable_status(asset, "review_required")
        self.assertEqual(asset.reusable_status, "review_required")
        self.assertFalse(asset.excluded)
        _apply_reusable_status(asset, "blocked")
        self.assertFalse(asset.excluded)
        _apply_reusable_status(asset, "excluded")
        self.assertTrue(asset.excluded)

    def test_python39_union_syntax_absent_in_changed_files(self):
        root = Path(__file__).resolve().parents[1] / "app"
        files = [
            root / "api/auto_edit.py",
            root / "api/video_library.py",
            root / "services/video_analysis_queue.py",
            root / "services/transcription.py",
            root / "services/tts.py",
            root / "services/storage.py",
            root / "database.py",
            root / "models/__init__.py",
            root / "main.py",
        ]
        pattern = re.compile(r"[A-Za-z_][\w\.\[\], ]+\s*\|\s*(None|str|int|Path)")
        for path in files:
            source = path.read_text(encoding="utf-8")
            matches = [line for line in source.splitlines() if pattern.search(line) and "pattern=" not in line and "edit_mode" not in line]
            self.assertFalse(matches, f"{path} still has PEP604 unions: {matches[:5]}")

    def test_library_and_tenant_isolation(self):
        semi = SimpleNamespace(tenant_id="t1", product_id=1, status="analyzed", excluded=False, asset_kind="material",
                               library_type="semi_finished", reusable_status="reusable", generation_depth=0)
        finished = SimpleNamespace(tenant_id="t1", product_id=1, status="analyzed", excluded=False, asset_kind="material",
                                   library_type="finished", reusable_status="reusable", generation_depth=1)
        other_tenant = SimpleNamespace(tenant_id="t2", product_id=1, status="analyzed", excluded=False, asset_kind="material",
                                       library_type="semi_finished", reusable_status="reusable", generation_depth=0)
        deep = SimpleNamespace(tenant_id="t1", product_id=1, status="analyzed", excluded=False, asset_kind="material",
                               library_type="finished", reusable_status="reusable", generation_depth=2)
        review = SimpleNamespace(tenant_id="t1", product_id=1, status="analyzed", excluded=False, asset_kind="material",
                                 library_type="finished", reusable_status="review_required", generation_depth=1)
        unanalysed = SimpleNamespace(tenant_id="t1", product_id=1, status="analysis_failed", excluded=False, asset_kind="material",
                                     library_type="finished", reusable_status="review_required", generation_depth=2)
        self.assertTrue(asset_eligible_for_project(semi, "t1", 1, "smart_compose"))
        self.assertFalse(asset_eligible_for_project(semi, "t1", 1, "manual_compose"))
        self.assertTrue(asset_eligible_for_project(finished, "t1", 1, "manual_compose"))
        self.assertTrue(asset_eligible_for_project(unanalysed, "t1", 1, "manual_compose"))
        self.assertFalse(asset_eligible_for_project(finished, "t1", 1, "smart_compose"))
        self.assertTrue(asset_eligible_for_project(finished, "t1", 1, "finished_remix"))
        self.assertFalse(asset_eligible_for_project(semi, "t1", 1, "finished_remix"))
        self.assertFalse(asset_eligible_for_project(other_tenant, "t1", 1, "smart_compose"))
        self.assertFalse(asset_eligible_for_project(deep, "t1", 1, "finished_remix"))
        self.assertFalse(asset_eligible_for_project(review, "t1", 1, "finished_remix"))
        final_output = SimpleNamespace(tenant_id="t1", product_id=1, status="analyzed", excluded=False,
                                       asset_kind="final_output", library_type="finished",
                                       reusable_status="reusable", generation_depth=1)
        self.assertFalse(asset_eligible_for_project(final_output, "t1", 1, "finished_remix"))

    def test_manual_plan_uses_whole_assets_in_selected_order(self):
        project = SimpleNamespace(id=7, structure=[
            {"id": "hook", "role": "hook", "title": "开场", "asset_ids": ["a2"]},
            {"id": "proof", "role": "proof", "title": "证明", "asset_ids": ["a1"]},
        ])
        assets = [SimpleNamespace(asset_id="a1", filename="one.mp4", duration_seconds=4.5),
                  SimpleNamespace(asset_id="a2", filename="two.mp4", duration_seconds=3.0)]
        variant = _manual_plan(project, assets)
        self.assertEqual([clip["asset_id"] for clip in variant["narrative_segments"]], ["a2", "a1"])
        self.assertEqual(variant["narrative_segments"][0]["source_range"], {"start": 0.0, "end": 3.0})
        self.assertEqual(variant["actual_duration_seconds"], 7.5)
        self.assertEqual(variant["structure_snapshot"][1]["title"], "证明")

    def test_transition_scoring_and_anaphors(self):
        role_rank = {"hook": 0, "selling_point": 1, "pain_point": 2}
        hook = SimpleNamespace(score=0.9, content_role="hook", speech_text="黄皮别错过这款素颜霜", speaker_id="s1", selling_points=["气色"])
        dangling = SimpleNamespace(score=0.9, content_role="selling_point", speech_text="所以它真的很能打", speaker_id="s2", selling_points=["气色"])
        follow = SimpleNamespace(score=0.8, content_role="selling_point", speech_text="这款素颜霜上脸自然显气色", speaker_id="s1", selling_points=["气色"])
        self.assertTrue(_starts_with_dangling_anaphor("所以它很自然"))
        opening = score_transition(None, dangling, None, "hook", role_rank)
        self.assertLess(opening["previous_transition_score"], 0.2)
        linked = score_transition(hook, follow, None, "selling_point", role_rank)
        self.assertGreater(linked["previous_transition_score"], 0.5)
        self.assertGreater(linked["voice_continuity_score"], 0.9)

    def test_validate_plan_rejects_remix_tts_and_duplicate_highlights(self):
        assets = [SimpleNamespace(asset_id="a1", duration_seconds=40)]
        clip = {
            "asset_id": "a1", "highlight_id": "h1", "audio_strategy": "keep_original",
            "source_range": {"start": 0, "end": 5}, "timeline_range": {"start": 0, "end": 5},
            "content_role": "hook", "speech_text": "黄皮别错过",
            "quality_score": 0.8, "role_match_score": 1, "previous_transition_score": 1,
            "next_transition_score": 1, "voice_continuity_score": 1, "transition_reason": "开场",
        }
        tts_plan = {"brief": {"edit_mode": "finished_remix", "objective": "live_stream"}, "variants": [{
            "variant_id": "V1", "edit_mode": "finished_remix", "speaker_count": 1,
            "actual_duration_seconds": 5, "target_duration_seconds": 5,
            "narrative_segments": [{**clip, "audio_strategy": "ai_voiceover", "audio_url": "x"}],
            "video_track": [clip],
        }]}
        self.assertTrue(any("TTS" in error for error in _validate_plan(tts_plan, assets)))
        dup_plan = {"brief": {"edit_mode": "finished_remix", "objective": "live_stream"}, "variants": [{
            "variant_id": "V1", "edit_mode": "finished_remix", "speaker_count": 1,
            "actual_duration_seconds": 10, "target_duration_seconds": 10,
            "narrative_segments": [
                clip,
                {**clip, "timeline_range": {"start": 5, "end": 10}, "source_range": {"start": 5, "end": 10}, "speech_text": "黄皮别错过"},
            ],
            "video_track": [
                clip,
                {**clip, "timeline_range": {"start": 5, "end": 10}, "source_range": {"start": 5, "end": 10}},
            ],
        }]}
        errors = _validate_plan(dup_plan, assets)
        self.assertTrue(any("重复使用 highlight" in error or "完全重复" in error for error in errors))

    def test_output_registration_depth_and_blocked_status(self):
        sources = [SimpleNamespace(generation_depth=0, has_background_music=False, burned_subtitles=False, asset_id="a1")]
        first = finished_output_metadata(sources, ["a1"])
        self.assertEqual(first["generation_depth"], 1)
        self.assertEqual(first["reusable_status"], "review_required")
        remix_sources = [SimpleNamespace(generation_depth=1, has_background_music=True, burned_subtitles=True, asset_id="f1")]
        second = finished_output_metadata(remix_sources, ["f1"])
        self.assertEqual(second["generation_depth"], 2)
        self.assertEqual(second["reusable_status"], "blocked")
        self.assertFalse(asset_eligible_for_project(
            SimpleNamespace(tenant_id="t1", product_id=1, status="analyzed", excluded=False,
                            library_type="finished", reusable_status="blocked", generation_depth=2),
            "t1", 1, "finished_remix",
        ))
        variant = {"narrative_segments": [{"asset_id": "a1"}, {"asset_id": "a1"}, {"asset_id": "a2"}], "video_track": [{"asset_id": "a2"}]}
        self.assertEqual(origin_ids_from_variant(variant), ["a1", "a2"])

    def test_finished_remix_render_flags_and_ffmpeg_keep_audio(self):
        self.assertFalse(remix_render_uses_system_captions("finished_remix"))
        self.assertFalse(remix_render_adds_public_bgm("finished_remix"))
        self.assertTrue(remix_render_uses_system_captions("smart_compose"))
        self.assertTrue(remix_render_adds_public_bgm("smart_compose"))
        command = finished_clip_ffmpeg_command("in.mp4", 1.0, 4.0, "out.mp4", 1080, 1920)
        self.assertNotIn("-an", command)
        self.assertIn("-c:a", command)
        self.assertTrue(any("loudnorm" in str(part) for part in command))
        silent = manual_clip_ffmpeg_command("in.mp4", 4.0, "out.mp4", 1080, 1920, False)
        self.assertIn("anullsrc=r=48000:cl=stereo", silent)
        self.assertIn("1:a:0", silent)

    def test_recover_interrupted_analysis_requeues_analyzing_rows(self):
        db = MagicMock()
        analysis_query = MagicMock(); asset_query = MagicMock()
        db.query.side_effect = [analysis_query, asset_query]
        analysis_query.filter.return_value.update.return_value = 1
        asset_query.filter.return_value.update.return_value = 3
        count = recover_interrupted_video_analyses(db)
        self.assertEqual(count, 3)
        db.commit.assert_called_once()

    def test_ffmpeg_finished_clip_keeps_mixed_audio(self):
        if subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode != 0:
            self.skipTest("ffmpeg not available")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            clip = Path(directory) / "clip.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=3",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
                "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first[aout]",
                "-map", "0:v", "-map", "[aout]", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(source),
            ], capture_output=True, check=True)
            subprocess.run(finished_clip_ffmpeg_command(source, 0.2, 1.5, clip, 320, 240), capture_output=True, check=True)
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                "-of", "csv=p=0", str(clip),
            ], capture_output=True, text=True, check=True)
            self.assertIn("audio", probe.stdout)


if __name__ == "__main__":
    unittest.main()
