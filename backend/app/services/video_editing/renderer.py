from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Union

from PIL import Image, ImageDraw, ImageFont

from app.models import VideoAsset
from app.services.storage import get_storage, get_storage_for_url
from app.services.video_editing.plan_normalization import tracks_are_source_synced


def _ffmpeg_executable() -> str:
    resolved = shutil.which("ffmpeg")
    if resolved:
        return resolved
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(bundled).is_file():
            return bundled
    except Exception:
        pass
    raise RuntimeError("环境缺少 ffmpeg，无法渲染视频")


def finished_clip_ffmpeg_command(source: Union[Path, str], start: float, duration: float,
                                 output: Union[Path, str], width: int, height: int) -> list[str]:
    return [
        _ffmpeg_executable(), "-y", "-ss", str(start), "-t", str(duration), "-i", str(source),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-af", "loudnorm=I=-16:LRA=7:TP=-1.5,aresample=async=1:first_pts=0",
        "-movflags", "+faststart", str(output),
    ]


def manual_clip_ffmpeg_command(source: Union[Path, str], duration: float, output: Union[Path, str],
                               width: int, height: int, has_audio: bool) -> list[str]:
    command = [_ffmpeg_executable(), "-y", "-i", str(source)]
    if not has_audio:
        command += ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=48000:cl=stereo"]
    command += [
        "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac",
        "-ar", "48000", "-ac", "2", "-af",
        "loudnorm=I=-16:LRA=7:TP=-1.5,aresample=async=1:first_pts=0",
        "-t", str(duration), "-movflags", "+faststart", str(output),
    ]
    return command


def _subtitle_cues(text: str, start: float, end: float, max_chars: int = 13) -> list[tuple[str, float, float]]:
    cleaned = re.sub(r"\s+", "", text or "").strip("，。！？；、 ")
    if not cleaned or end <= start:
        return []
    phrases: list[str] = []
    for clause in re.split(r"(?<=[，。！？；])", cleaned):
        clause = clause.strip()
        while len(clause) > max_chars:
            phrases.append(clause[:max_chars]); clause = clause[max_chars:]
        if clause:
            phrases.append(clause)
    weights = [max(2.0, len(re.sub(r"[，。！？；、]", "", phrase))) for phrase in phrases]
    total, available, cursor = sum(weights), end - start, start
    result = []
    for index, (phrase, weight) in enumerate(zip(phrases, weights)):
        cue_end = end if index == len(phrases) - 1 else min(end, cursor + available * weight / total)
        result.append((phrase, cursor, cue_end)); cursor = cue_end
    return result


def _caption_font(size: int) -> Union[ImageFont.FreeTypeFont, ImageFont.ImageFont]:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc", "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _write_caption_images(narrative: list[dict[str, Any]], directory: Path,
                          width: int, height: int) -> list[tuple[Path, float, float]]:
    captions: list[tuple[Path, float, float]] = []
    font_size = max(34, round(height * .029)); font = _caption_font(font_size)
    for segment in narrative:
        timeline = segment.get("timeline_range") or {}
        start, end = float(timeline.get("start", 0)), float(timeline.get("end", 0))
        timed: list[tuple[str, float, float]] = []
        buffer: list[dict[str, Any]] = []
        for word in segment.get("subtitle_words") or []:
            buffer.append(word)
            combined = "".join(str(item.get("text") or "") for item in buffer)
            if combined.endswith(tuple("，。！？；")) or len(combined) >= 13:
                timed.append((combined, float(buffer[0]["start"]), float(buffer[-1]["end"])))
                buffer = []
        if buffer:
            timed.append(("".join(str(item.get("text") or "") for item in buffer),
                          float(buffer[0]["start"]), float(buffer[-1]["end"])))
        if not timed:
            timed = _subtitle_cues(str(segment.get("speech_text") or ""), start, end)
        for phrase, cue_start, cue_end in timed:
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0)); draw = ImageDraw.Draw(image)
            box = draw.textbbox((0, 0), phrase, font=font, stroke_width=2)
            text_width, text_height = box[2] - box[0], box[3] - box[1]
            pad_x, pad_y = round(font_size * .42), round(font_size * .22)
            left = (width - text_width) // 2 - pad_x
            top = height - round(height * .105) - text_height - pad_y
            draw.rounded_rectangle(
                (left, top, left + text_width + pad_x * 2, top + text_height + pad_y * 2),
                radius=round(font_size * .28), fill=(12, 12, 12, 118))
            draw.text(((width - text_width) // 2, top + pad_y), phrase, font=font,
                      fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 175))
            path = directory / f"caption-{len(captions):03d}.png"
            image.save(path); captions.append((path, cue_start, cue_end))
    return captions


def render_variant(plan: dict[str, Any], assets: list[VideoAsset], output: Path,
                   aspect_ratio: str) -> float:
    """Render one already-validated variant. No path or command fragment comes from LLM text."""
    asset_map = {asset.asset_id: asset for asset in assets}
    storage = get_storage()
    ffmpeg = _ffmpeg_executable()
    width, height = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}[aspect_ratio]
    edit_mode = str(plan.get("edit_mode") or "")
    coupled_mode = edit_mode in {"finished_remix", "manual_compose"}
    if coupled_mode and tracks_are_source_synced(plan):
        segments: list[Path] = []
        for index, clip in enumerate(plan["narrative_segments"]):
            asset = asset_map[clip["asset_id"]]
            source = get_storage_for_url(asset.object_url).local_path(asset.object_url)
            if not source:
                raise RuntimeError(f"无法下载素材 {asset.filename}")
            if edit_mode == "finished_remix" and not asset.has_audio:
                raise RuntimeError(f"成品片段缺少完整音轨：{asset.filename}")
            start, end = clip["source_range"]["start"], clip["source_range"]["end"]
            segment = output.parent / f"av-{index:03d}.mp4"; segments.append(segment)
            command = (finished_clip_ffmpeg_command(source, start, end - start, segment, width, height)
                       if edit_mode == "finished_remix" else
                       manual_clip_ffmpeg_command(source, end - start, segment, width, height, asset.has_audio))
            subprocess.run(command, capture_output=True, text=True, timeout=300, check=True)
        concat = output.parent / "av-concat.txt"
        concat.write_text("\n".join(f"file '{path.as_posix()}'" for path in segments), encoding="utf-8")
        subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                        "-c", "copy", "-movflags", "+faststart", str(output)],
                       capture_output=True, text=True, timeout=600, check=True)
        return float(plan["actual_duration_seconds"])

    video_segments: list[Path] = []
    audio_segments: list[Path] = []
    for index, clip in enumerate(plan["video_track"]):
        asset = asset_map[clip["asset_id"]]
        source = get_storage_for_url(asset.object_url).local_path(asset.object_url)
        if not source:
            raise RuntimeError(f"无法下载素材 {asset.filename}")
        start, end = clip["source_range"]["start"], clip["source_range"]["end"]
        segment = output.parent / f"video-{index:03d}.mp4"; video_segments.append(segment)
        subprocess.run([
            ffmpeg, "-y", "-ss", str(start), "-t", str(end - start), "-i", str(source), "-an",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-movflags", "+faststart", str(segment),
        ], capture_output=True, text=True, timeout=300, check=True)
    for index, clip in enumerate(plan["narrative_segments"]):
        if clip.get("audio_strategy") == "ai_voiceover":
            source = storage.local_path(clip["audio_url"])
            if not source:
                raise RuntimeError("无法下载 AI 配音")
            start, end = 0.0, float(clip["duration_seconds"])
        else:
            asset = asset_map[clip["asset_id"]]
            if not asset.has_audio:
                raise RuntimeError(f"原声片段缺少音轨：{asset.filename}")
            source = get_storage_for_url(asset.object_url).local_path(asset.object_url)
            start, end = clip["source_range"]["start"], clip["source_range"]["end"]
        segment = output.parent / f"audio-{index:03d}.wav"; audio_segments.append(segment)
        audio_filter = "loudnorm=I=-16:LRA=7:TP=-1.5,aresample=async=1:first_pts=0"
        if clip.get("audio_strategy") == "ai_voiceover":
            audio_filter += f",apad=pad_dur={end - start:.3f},atrim=duration={end - start:.3f}"
        fade = min(float(clip.get("audio_fade_seconds") or 0), (end - start) / 2)
        if fade > 0:
            audio_filter += f",afade=t=in:st=0:d={fade:.3f},afade=t=out:st={max(0.0, end - start - fade):.3f}:d={fade:.3f}"
        subprocess.run([
            ffmpeg, "-y", "-ss", str(start), "-t", str(end - start), "-i", str(source), "-vn",
            "-af", audio_filter,
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(segment),
        ], capture_output=True, text=True, timeout=300, check=True)
    video_list, audio_list = output.parent / "video-concat.txt", output.parent / "audio-concat.txt"
    video_list.write_text("\n".join(f"file '{path.as_posix()}'" for path in video_segments), encoding="utf-8")
    audio_list.write_text("\n".join(f"file '{path.as_posix()}'" for path in audio_segments), encoding="utf-8")
    video_joined, audio_joined = output.parent / "video-track.mp4", output.parent / "narrative.wav"
    subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(video_list),
                    "-c", "copy", str(video_joined)], capture_output=True, text=True, timeout=600, check=True)
    subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(audio_list),
                    "-c", "copy", str(audio_joined)], capture_output=True, text=True, timeout=600, check=True)
    duration = float(plan["actual_duration_seconds"])
    if coupled_mode:
        subprocess.run([
            ffmpeg, "-y", "-i", str(video_joined), "-i", str(audio_joined),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
            "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(output),
        ], capture_output=True, text=True, timeout=600, check=True)
        return duration
    bgm = output.parent / "background-music.wav"
    source = (f"aevalsrc=0.08*sin(2*PI*220*t)+0.055*sin(2*PI*277.18*t)+"
              f"0.04*sin(2*PI*329.63*t):s=48000:d={duration:.3f}")
    subprocess.run([
        ffmpeg, "-y", "-f", "lavfi", "-i", source, "-af",
        "lowpass=f=900,afade=t=in:st=0:d=1.5,afade=t=out:st=" + str(max(0.0, duration - 2)) + ":d=2",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(bgm),
    ], capture_output=True, text=True, timeout=120, check=True)
    command = [ffmpeg, "-y", "-i", str(video_joined), "-i", str(audio_joined), "-i", str(bgm)]
    captions = _write_caption_images(plan["narrative_segments"], output.parent, width, height)
    filters = [
        "[2:a][1:a]sidechaincompress=threshold=0.025:ratio=10:attack=15:release=450[ducked]",
        "[ducked]volume=0.16[bed]", "[1:a][bed]amix=inputs=2:duration=first:dropout_transition=2[aout]",
    ]
    if captions:
        for path, _, _ in captions:
            command += ["-i", str(path)]
        current = "[0:v]"
        for index, (_, start, end) in enumerate(captions):
            target = f"[caption{index}]"
            filters.append(f"{current}[{index + 3}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'{target}")
            current = target
        command += ["-filter_complex", ";".join(filters), "-map", current, "-map", "[aout]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "192k"]
    else:
        command += ["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[aout]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    command += ["-shortest", "-movflags", "+faststart", str(output)]
    subprocess.run(command, capture_output=True, text=True, timeout=600, check=True)
    return duration
