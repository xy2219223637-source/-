from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.video_editing.renderer import render_variant


class VideoEditingRendererTests(unittest.TestCase):
    def test_manual_visual_repair_renders_separate_video_and_original_audio(self):
        original = {
            "asset_id": "asset-original", "source_range": {"start": 0.0, "end": 12.0},
            "timeline_range": {"start": 0.0, "end": 12.0}, "duration_seconds": 12.0,
            "audio_strategy": "keep_original", "speech_text": "",
        }
        video = [
            {**original, "source_range": {"start": 0.0, "end": 4.0},
             "timeline_range": {"start": 0.0, "end": 4.0}, "duration_seconds": 4.0},
            {"asset_id": "asset-new", "source_range": {"start": 2.0, "end": 6.0},
             "timeline_range": {"start": 4.0, "end": 8.0}, "duration_seconds": 4.0},
            {**original, "source_range": {"start": 8.0, "end": 12.0},
             "timeline_range": {"start": 8.0, "end": 12.0}, "duration_seconds": 4.0},
        ]
        plan = {
            "edit_mode": "manual_compose", "actual_duration_seconds": 12.0,
            "video_track": video, "narrative_segments": [original],
        }
        assets = [
            SimpleNamespace(asset_id="asset-original", object_url="/original.mp4",
                            filename="original.mp4", has_audio=True),
            SimpleNamespace(asset_id="asset-new", object_url="/new.mp4",
                            filename="new.mp4", has_audio=True),
        ]
        locator = SimpleNamespace(local_path=lambda url: Path(url))
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as name, \
                patch("app.services.video_editing.renderer.get_storage_for_url", return_value=locator), \
                patch("app.services.video_editing.renderer.get_storage", return_value=locator), \
                patch("app.services.video_editing.renderer.subprocess.run", side_effect=fake_run):
            duration = render_variant(plan, assets, Path(name) / "output.mp4", "9:16")

        self.assertEqual(duration, 12.0)
        rendered = "\n".join(" ".join(str(value) for value in command) for command in commands)
        self.assertIn("new.mp4", rendered)
        audio_commands = [command for command in commands if "-vn" in command]
        self.assertEqual(len(audio_commands), 1)
        self.assertTrue(any(str(value).endswith("original.mp4") for value in audio_commands[0]))
        self.assertNotIn("aevalsrc", rendered)
        self.assertIn("-map 0:v:0 -map 1:a:0", rendered)


if __name__ == "__main__":
    unittest.main()
