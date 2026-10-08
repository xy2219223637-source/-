from __future__ import annotations

from copy import deepcopy
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.video_editing.plan_patch import apply_repair_plan
from app.services.video_editing.renderer import _ffmpeg_executable, render_variant
from app.services.video_repair.rendering import (
    _probe_output, _render_flat_visual, _validate_rendered_output,
)
from scripts.qa_compare_frames import compare_frames


def _base_plan() -> dict:
    clip = {
        "asset_id": "asset-original", "highlight_id": "original",
        "source_range": {"start": 0.0, "end": 2.0},
        "timeline_range": {"start": 0.0, "end": 2.0},
        "duration_seconds": 2.0, "content_role": "product_demo",
    }
    narrative = deepcopy(clip)
    narrative.update({"audio_strategy": "source", "speech_text": "", "subtitle_words": []})
    return {"variants": [{
        "variant_id": "V1", "edit_mode": "manual_compose",
        "actual_duration_seconds": 2.0, "target_duration_seconds": 2.0,
        "video_track": [clip], "narrative_segments": [narrative],
    }]}


def _candidate(duration: float) -> dict:
    return {
        "id": "asset-candidate:usage", "source_asset_public_id": "asset-candidate",
        "highlight_id": "usage", "source_range": {"start": 0.0, "end": duration},
        "content_role": "usage",
    }


class VideoRepairFfmpegIntegrationTests(unittest.TestCase):
    def test_flat_visual_replacement_keeps_duration_dimensions_and_audio(self):
        try:
            ffmpeg = _ffmpeg_executable()
        except RuntimeError:
            self.skipTest("ffmpeg not available")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            original = directory / "flat-original.mp4"
            candidate = directory / "flat-candidate.mp4"
            output = directory / "flat-output.mp4"
            for path, color, tone in ((original, "blue", 330), (candidate, "green", 660)):
                subprocess.run([
                    ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=3",
                    "-f", "lavfi", "-i", f"sine=frequency={tone}:duration=3",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(path),
                ], capture_output=True, text=True, check=True, timeout=60)
            _render_flat_visual(
                original, candidate, output, target_start=.5, target_end=1.5,
                candidate_start=.25, source_duration=3, width=320, height=240,
                source_has_audio=True)
            probe = _probe_output(output)
            self.assertLessEqual(abs(probe["duration"] - 3), .1)
            self.assertEqual((probe["width"], probe["height"]), (320, 240))
            source_checks = compare_frames(
                original, output, same=[(.2, .2)], different=[(1.0, 1.0)],
                ffmpeg=ffmpeg,
            )
            candidate_checks = compare_frames(
                candidate, output, same=[(.75, 1.0)], different=[],
                ffmpeg=ffmpeg,
            )
            self.assertTrue(all(row["passed"] for row in source_checks), source_checks)
            self.assertTrue(all(row["passed"] for row in candidate_checks), candidate_checks)

    def test_all_three_repair_actions_render_playable_audio_video(self):
        try:
            ffmpeg = _ffmpeg_executable()
        except RuntimeError:
            self.skipTest("ffmpeg not available")
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            original = directory / "original.mp4"
            candidate = directory / "candidate.mp4"
            for path, color, tone in ((original, "blue", 330), (candidate, "green", 660)):
                subprocess.run([
                    ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=3",
                    "-f", "lavfi", "-i", f"sine=frequency={tone}:duration=3",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(path),
                ], capture_output=True, text=True, check=True, timeout=60)
            assets = [
                SimpleNamespace(asset_id="asset-original", object_url=str(original),
                                filename=original.name, has_audio=True),
                SimpleNamespace(asset_id="asset-candidate", object_url=str(candidate),
                                filename=candidate.name, has_audio=True),
            ]
            locator = SimpleNamespace(local_path=lambda url: Path(url))
            cases = (
                ("VISUAL_REPLACE", {"start": .5, "end": 1.5}, _candidate(1.0), 2.0),
                ("AV_SEGMENT_REPLACE", {"start": .5, "end": 1.5}, _candidate(1.0), 2.0),
                ("PROCESS_INSERT", {"start": 1.0, "end": 1.1}, _candidate(.5), 2.5),
            )
            with patch("app.services.video_editing.renderer.get_storage_for_url",
                       return_value=locator), patch(
                           "app.services.video_editing.renderer.get_storage",
                           return_value=locator):
                for index, (action_type, target, candidate_row, duration) in enumerate(cases):
                    with self.subTest(action=action_type):
                        repair = {"actions": [{"action_id": "repair-1", "type": action_type,
                                                "target_range": target}]}
                        patched = apply_repair_plan(
                            _base_plan(), repair, {"repair-1": candidate_row},
                            {"asset-original", "asset-candidate"}, index + 1, 1)
                        output = directory / f"{action_type.lower()}.mp4"
                        render_variant(patched["variants"][0], assets, output, "1:1")
                        probe = _validate_rendered_output(output, duration, "1:1")
                        self.assertEqual((probe["width"], probe["height"]), (1080, 1080))


if __name__ == "__main__":
    unittest.main()
