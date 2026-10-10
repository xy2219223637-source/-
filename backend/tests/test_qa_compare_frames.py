from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.video_editing.renderer import _ffmpeg_executable
from scripts.qa_compare_frames import compare_frames


class QaCompareFramesTests(unittest.TestCase):
    def test_compares_frames_and_does_not_keep_screenshots(self):
        ffmpeg = _ffmpeg_executable()
        with tempfile.TemporaryDirectory(prefix="qa-compare-test-") as name:
            directory = Path(name)
            first = directory / "red.mp4"
            second = directory / "blue.mp4"
            for path, color in ((first, "red"), (second, "blue")):
                subprocess.run(
                    [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i", f"color=c={color}:s=320x240:r=30:d=1",
                     "-c:v", "mpeg4", str(path)],
                    capture_output=True, text=True, check=True, timeout=60,
                )
            temporary_directory = tempfile.TemporaryDirectory
            with patch("scripts.qa_compare_frames.tempfile.TemporaryDirectory",
                       side_effect=lambda **kwargs: temporary_directory(dir=directory, **kwargs)):
                results = compare_frames(
                    first, second, same=[(.2, .2)], different=[(.2, .2)],
                    ffmpeg=ffmpeg,
                )
                with self.assertRaises(RuntimeError):
                    compare_frames(first, second, same=[(2.0, .2)], different=[],
                                   ffmpeg=ffmpeg)
            self.assertFalse(results[0]["passed"])
            self.assertTrue(results[1]["passed"])
            self.assertEqual(list(directory.glob("*.png")), [])
            self.assertEqual(sorted(path.name for path in directory.iterdir()),
                             ["blue.mp4", "red.mp4"])


if __name__ == "__main__":
    unittest.main()
