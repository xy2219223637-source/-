"""Compare selected video frames without keeping extracted images.

Run from backend/: python -m scripts.qa_compare_frames left.mp4 right.mp4
    --same 2:2 --different 8:8
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from app.services.video_editing.renderer import _ffmpeg_executable


def _pair(value: str) -> tuple[float, float]:
    try:
        left, right = (float(part) for part in value.split(":"))
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("时间点格式应为 左视频秒数:右视频秒数") from None
    if not all(math.isfinite(item) and item >= 0 for item in (left, right)):
        raise argparse.ArgumentTypeError("时间点必须是非负有限数")
    return left, right


def _extract_frame(video: Path, seconds: float, target: Path, ffmpeg: str) -> None:
    subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", str(seconds), "-i", str(video), "-frames:v", "1",
         "-vf", "scale=256:256", str(target)],
        capture_output=True, text=True, timeout=90, check=True,
    )
    if not target.is_file():
        raise RuntimeError(f"无法在 {seconds:g} 秒提取画面：{video.name}")


def compare_frames(
    left: Path, right: Path, *,
    same: list[tuple[float, float]],
    different: list[tuple[float, float]],
    same_max: float = 12.0,
    different_min: float = 20.0,
    ffmpeg: str | None = None,
) -> list[dict[str, float | str | bool]]:
    """Return mean pixel differences; all extracted frames die with the temp dir."""
    if not left.is_file() or not right.is_file():
        raise FileNotFoundError("待比对的视频不存在")
    if not same and not different:
        raise ValueError("至少指定一个 --same 或 --different 时间点")
    if not all(math.isfinite(item) and 0 <= item <= 255 for item in (same_max, different_min)):
        raise ValueError("差异阈值必须在 0 到 255 之间")
    executable = ffmpeg or _ffmpeg_executable()
    results: list[dict[str, float | str | bool]] = []
    with tempfile.TemporaryDirectory(prefix="video-cut-qa-") as name:
        directory = Path(name)
        for index, (kind, left_time, right_time) in enumerate(
            [("same", *pair) for pair in same] +
            [("different", *pair) for pair in different]
        ):
            left_frame = directory / f"left-{index}.png"
            right_frame = directory / f"right-{index}.png"
            _extract_frame(left, left_time, left_frame, executable)
            _extract_frame(right, right_time, right_frame, executable)
            with Image.open(left_frame) as first, Image.open(right_frame) as second:
                difference = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
                mean = sum(ImageStat.Stat(difference).mean) / 3
            results.append({
                "check": kind,
                "left_seconds": left_time,
                "right_seconds": right_time,
                "mean_pixel_difference": round(mean, 2),
                "passed": mean <= same_max if kind == "same" else mean >= different_min,
            })
            left_frame.unlink()
            right_frame.unlink()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="即时抽帧比对视频；截图仅存系统临时目录并自动删除")
    parser.add_argument("left", type=Path, help="左侧视频")
    parser.add_argument("right", type=Path, help="右侧视频")
    parser.add_argument("--same", action="append", type=_pair, default=[], metavar="左秒数:右秒数",
                        help="两处画面应相同，可重复指定")
    parser.add_argument("--different", action="append", type=_pair, default=[], metavar="左秒数:右秒数",
                        help="两处画面应不同，可重复指定")
    parser.add_argument("--same-max", type=float, default=12.0, help="相同画面的最大平均像素差")
    parser.add_argument("--different-min", type=float, default=20.0, help="不同画面的最小平均像素差")
    arguments = parser.parse_args()
    try:
        results = compare_frames(
            arguments.left, arguments.right, same=arguments.same,
            different=arguments.different, same_max=arguments.same_max,
            different_min=arguments.different_min,
        )
    except (FileNotFoundError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"无法完成比对：{exc}\n")
    print(json.dumps({"passed": all(row["passed"] for row in results), "checks": results}, ensure_ascii=False))
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
