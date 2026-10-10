from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.video_repair.rendering import _probe_output
from app.services.video_editing.renderer import _ffmpeg_executable
from app.services.video_repair.v4_renderer import _render_audio_replace, _render_insert


class VideoRepairV4RendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.ffmpeg = _ffmpeg_executable()
        self.source = self.dir / "source.mp4"
        self.visual = self.dir / "visual.mp4"
        self.audio = self.dir / "audio.wav"
        subprocess.run([
            self.ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(self.source),
        ], check=True, capture_output=True)
        subprocess.run([
            self.ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=30:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.visual),
        ], check=True, capture_output=True)
        subprocess.run([
            self.ffmpeg, "-y", "-f", "lavfi", "-i",
            "sine=frequency=880:sample_rate=48000:duration=2", str(self.audio),
        ], check=True, capture_output=True)
        self.asset = SimpleNamespace(
            duration_seconds=2.0, width=320, height=240, has_audio=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_audio_replace_keeps_duration_and_video_dimensions(self):
        output = self.dir / "audio-replace.mp4"
        _render_audio_replace(self.asset, self.source, self.audio, {
            "target_range": {"start_ms": 500, "end_ms": 1500},
            "audio": {"source_range": {"start_ms": 0, "end_ms": 1000}},
            "fade_in_ms": 50, "fade_out_ms": 50,
        }, output)
        probe = _probe_output(output)
        self.assertLessEqual(abs(probe["duration"] - 2), .1)
        self.assertEqual((probe["width"], probe["height"]), (320, 240))

    def test_content_insert_extends_timeline(self):
        output = self.dir / "insert.mp4"
        _render_insert(self.asset, self.source, self.visual, self.audio, {
            "insert_at_ms": 1000, "duration_ms": 500,
            "visual": {"source_range": {"start_ms": 0, "end_ms": 500}},
            "audio": {"source_range": {"start_ms": 0, "end_ms": 500}},
        }, output)
        probe = _probe_output(output)
        self.assertLessEqual(abs(probe["duration"] - 2.5), .15)
        self.assertEqual((probe["width"], probe["height"]), (320, 240))


if __name__ == "__main__":
    unittest.main()
