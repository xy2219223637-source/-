"""Media processing and AI helpers for the director-inspiration workflow."""

from __future__ import annotations

import asyncio
import ast
from collections import Counter
from difflib import SequenceMatcher
import ipaddress
import json
import logging
import math
import re
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import DirectorAsset, DirectorProject, DirectorSection, DirectorShot
from app.services.llm import LLMClient, resolve_director_asr_provider
from app.services.storage import get_storage


SECTION_TEMPLATES = [
    ("使用体验引入", "从真实使用感受切入, 迅速建立内容场景"),
    ("痛点与需求", "指出目标人群正在遇到的问题"),
    ("产品解决方案", "展示商品如何回应问题, 不夸大功效"),
    ("核心卖点介绍", "用画面和简短文案解释关键卖点"),
    ("使用方法演示", "展示拿取, 使用和细节表现"),
    ("体验与证据", "补充可观察的体验或商品事实"),
    ("行动引导", "给出克制明确的下一步行动"),
]

logger = logging.getLogger(__name__)


def _text(value: object, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return str(value)[:limit]
    return str(value)[:limit]


def _string_list(value: object, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item, 200) for item in value[:limit] if _text(item, 200).strip()]


def _summary_text(value: object, limit: int = 1000) -> str:
    if isinstance(value, list):
        return "，".join(_text(item, 200).strip() for item in value if _text(item, 200).strip())[:limit]
    return _text(value, limit).strip()


def _structured_items(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, dict):
        for key in ("content", "text", "summary", "name"):
            if value.get(key):
                return _structured_items(value[key])
        return list(value.values())
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
            except (ValueError, SyntaxError, json.JSONDecodeError):
                continue
            if isinstance(parsed, (list, tuple, set)):
                return list(parsed)
    return [text]


def _paragraph_text(value: object, limit: int = 2000) -> str:
    parts: list[str] = []
    for raw in _structured_items(value):
        if isinstance(raw, (list, tuple, set, dict)):
            text = _paragraph_text(raw, limit)
        else:
            text = re.sub(r"\s+", " ", str(raw or "")).strip(" \t\r\n'\"")
        if text and text not in parts:
            parts.append(text)
    return "；".join(parts)[:limit]


def _short_tags(value: object, max_items: int, item_limit: int = 24) -> list[str]:
    tags: list[str] = []
    for raw in _structured_items(value):
        values = _structured_items(raw) if isinstance(raw, (list, tuple, set, dict)) else re.split(r"[，,、;；|\n]+", str(raw or ""))
        for candidate in values:
            item = re.sub(r"\s+", " ", str(candidate or "")).strip()
            item = re.sub(r"^(?:[-*]+|\d+[.)、])\s*", "", item).strip(" \t\r\n'\"，,。.;；")
            if item and item not in tags:
                tags.append(item[:item_limit])
            if len(tags) >= max_items:
                return tags
    return tags


def _product_candidates(value: object) -> list[str]:
    candidates = _short_tags(value, 20, 96)
    unknown = {"待确认", "无法确认", "未知", "无", "none", "null", "商品", "产品"}
    return [item for item in candidates if item.lower() not in unknown]


def _primary_product_name(value: object, shots: list[dict]) -> str:
    observed = [candidate for shot in shots for candidate in _product_candidates(shot.get("product"))]
    if observed:
        counts = Counter(observed)
        return max(counts, key=lambda item: (counts[item], len(item)))[:256]
    candidates = _product_candidates(value)
    return candidates[0][:256] if candidates else ""


def _analysis_error_message(exc: Exception) -> str:
    if isinstance(exc, SQLAlchemyError):
        return "解析结果保存失败, 请重试. 如仍失败, 请联系管理员查看后台日志."
    if isinstance(exc, (ValueError, RuntimeError, TimeoutError)):
        message = re.sub(r"\s+", " ", str(exc)).strip()
        if message:
            return message[:500]
    return "视频解析未完成, 请重试. 如仍失败, 请联系管理员查看后台日志."


def _confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _fallback_sections(duration_seconds: float = 45) -> list[dict]:
    total_ms = max(21_000, int((duration_seconds or 45) * 1000))
    segment_ms = total_ms // len(SECTION_TEMPLATES)
    sections = []
    for index, (title, description) in enumerate(SECTION_TEMPLATES):
        start_ms = index * segment_ms
        end_ms = total_ms if index == len(SECTION_TEMPLATES) - 1 else (index + 1) * segment_ms
        sections.append(
            {
                "title": title,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_text": "",
                "description": description,
                "shooting_notes": "结合参考画面的景别, 主体动作和商品露出方式进行拍摄",
            }
        )
    return sections


def _public_video_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("请输入有效的 HTTP 或 HTTPS 视频直链")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("视频地址不能指向内网或保留地址")
    return url


def _download_video(url: str, target: Path) -> None:
    current = _public_video_url(url)
    max_bytes = get_settings().director_video_max_upload_mb * 1024 * 1024
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        for _ in range(4):
            with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "")
                    current = _public_video_url(str(response.url.join(location)))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if not content_type.startswith("video/"):
                    raise ValueError("该链接不是可直接读取的视频文件, 请改为上传本地视频")
                size = 0
                with target.open("wb") as output:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError(f"视频不能超过 {get_settings().director_video_max_upload_mb}MB")
                        output.write(chunk)
                return
    raise ValueError("视频链接重定向次数过多")


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _rate(value: object) -> float:
    text = str(value or "0")
    if "/" not in text:
        return _number(text)
    numerator, denominator = text.split("/", 1)
    divisor = _number(denominator)
    return _number(numerator) / divisor if divisor else 0.0


def _ffprobe_executable() -> str:
    configured = get_settings().director_ffprobe_path.strip()
    if configured:
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        if Path(configured).is_file():
            return configured
        raise ValueError(f"DIRECTOR_FFPROBE_PATH 不存在: {configured}")
    resolved = shutil.which("ffprobe")
    if resolved:
        return resolved
    try:
        import imageio_ffmpeg

        ffmpeg_path = Path(imageio_ffmpeg.get_ffmpeg_exe())
        for name in ("ffprobe.exe", "ffprobe"):
            candidate = ffmpeg_path.with_name(name)
            if candidate.is_file():
                return str(candidate)
    except Exception:
        pass
    return ""


def _ffmpeg_executable() -> str:
    configured = get_settings().director_ffmpeg_path.strip()
    if configured:
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        if Path(configured).is_file():
            return configured
        raise ValueError(f"DIRECTOR_FFMPEG_PATH 不存在: {configured}")
    resolved = shutil.which("ffmpeg")
    if resolved:
        return resolved
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _probe_media(video_path: Path) -> dict:
    ffprobe = _ffprobe_executable()
    if ffprobe:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise ValueError((result.stderr or "FFprobe 无法读取视频信息")[-1000:])
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
        format_info = payload.get("format") if isinstance(payload.get("format"), dict) else {}
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
        audio = audio_streams[0] if audio_streams else {}
        duration = _number(format_info.get("duration") or video.get("duration"))
        if duration <= 0:
            raise ValueError("无法读取视频时长")
        return {
            "probe_provider": "ffprobe",
            "duration_seconds": round(duration, 3),
            "width": int(_number(video.get("width"))),
            "height": int(_number(video.get("height"))),
            "fps": round(_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")), 3),
            "video_codec": str(video.get("codec_name") or ""),
            "pixel_format": str(video.get("pix_fmt") or ""),
            "format_name": str(format_info.get("format_name") or ""),
            "bit_rate": int(_number(format_info.get("bit_rate"))),
            "has_audio": bool(audio_streams),
            "audio_track_count": len(audio_streams),
            "audio_codec": str(audio.get("codec_name") or ""),
            "audio_channels": int(_number(audio.get("channels"))),
            "audio_sample_rate": int(_number(audio.get("sample_rate"))),
        }

    import imageio_ffmpeg

    reader = imageio_ffmpeg.read_frames(str(video_path))
    try:
        metadata = next(reader)
    finally:
        reader.close()
    duration = _number(metadata.get("duration"))
    if duration <= 0:
        raise ValueError("无法读取视频时长")
    size = metadata.get("size") if isinstance(metadata.get("size"), (tuple, list)) else (0, 0)
    return {
        "probe_provider": "imageio_ffmpeg",
        "duration_seconds": round(duration, 3),
        "width": int(size[0]) if len(size) > 0 else 0,
        "height": int(size[1]) if len(size) > 1 else 0,
        "fps": round(_number(metadata.get("fps")), 3),
        "video_codec": str(metadata.get("codec") or ""),
        "pixel_format": str(metadata.get("pix_fmt") or ""),
        "format_name": "",
        "bit_rate": 0,
        "has_audio": None,
        "audio_track_count": 0,
        "audio_codec": "",
        "audio_channels": 0,
        "audio_sample_rate": 0,
    }


def _video_duration(video_path: Path) -> float:
    return float(_probe_media(video_path)["duration_seconds"])


def _normalize_shot_ranges(
    boundaries: list[float],
    duration: float,
    min_seconds: float | None = None,
) -> list[tuple[float, float]]:
    settings = get_settings()
    minimum = max(0.2, min_seconds or settings.director_min_shot_seconds)
    points = [0.0]
    for value in sorted(set(boundaries)):
        value = max(0.0, min(float(value), duration))
        if minimum <= value - points[-1] and duration - value >= minimum:
            points.append(value)
    if duration - points[-1] < minimum and len(points) > 1:
        points.pop()
    points.append(duration)
    return [(round(start, 3), round(end, 3)) for start, end in zip(points, points[1:]) if end > start]


def _detect_shot_ranges(video_path: Path, duration: float) -> list[tuple[float, float]]:
    ffmpeg = _ffmpeg_executable()
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(video_path),
            "-an",
            "-vf",
            f"select='gt(scene,{get_settings().director_scene_threshold})',showinfo",
            "-fps_mode",
            "vfr",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=max(120, int(duration * 4)),
    )
    boundaries = [float(value) for value in re.findall(r"pts_time:([0-9.]+)", result.stderr or "")]
    return _normalize_shot_ranges(boundaries, duration)


def _run_ffmpeg(command: list[str], *, timeout: int = 120) -> bool:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        logger.warning("FFmpeg failed: %s", (result.stderr or "")[-800:])
        return False
    return True


def _extract_audio(video_path: Path, target: Path) -> bool:
    return _run_ffmpeg(
        [
            _ffmpeg_executable(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            str(target),
        ]
    ) and target.exists()


def _keyframe_ratios(duration: float) -> list[float]:
    settings = get_settings()
    minimum = max(3, min(5, settings.director_keyframe_min_count))
    maximum = max(minimum, min(5, settings.director_keyframe_max_count))
    if duration <= 2.5:
        count = minimum
    elif duration <= 5:
        count = min(maximum, minimum + 1)
    else:
        count = maximum
    if count == 1:
        return [0.5]
    return [round(0.12 + 0.76 * index / (count - 1), 4) for index in range(count)]


def _extract_shot_media(
    video_path: Path,
    output_dir: Path,
    ranges: list[tuple[float, float]],
) -> list[dict]:
    ffmpeg = _ffmpeg_executable()
    output: list[dict] = []
    for index, (start, end) in enumerate(ranges):
        duration = max(0.05, end - start)
        keyframes: list[Path] = []
        for frame_index, ratio in enumerate(_keyframe_ratios(duration)):
            target = output_dir / f"shot-{index:03d}-frame-{frame_index}.jpg"
            timestamp = start + duration * ratio
            ok = _run_ffmpeg(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=720:-2",
                    str(target),
                ],
                timeout=45,
            )
            if ok and target.exists():
                keyframes.append(target)
        if not keyframes:
            raise ValueError(f"第 {index + 1} 个子分镜关键帧提取失败")
        thumbnail = keyframes[len(keyframes) // 2]
        clip = output_dir / f"shot-{index:03d}.mp4"
        clip_ok = _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(video_path),
                "-t",
                f"{duration:.3f}",
                "-an",
                "-vf",
                "scale=720:-2,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "25",
                "-movflags",
                "+faststart",
                str(clip),
            ],
            timeout=max(90, int(duration * 10)),
        )
        output.append(
            {
                "index": index,
                "start_ms": int(start * 1000),
                "end_ms": int(end * 1000),
                "keyframes": keyframes,
                "thumbnail": thumbnail,
                "clip": clip if clip_ok and clip.exists() else None,
            }
        )
    return output


def _best_range_index(start_ms: int, end_ms: int, ranges: list[tuple[int, int]]) -> int | None:
    if not ranges:
        return None
    if end_ms <= start_ms:
        return next((index for index, (start, end) in enumerate(ranges) if start <= start_ms < end), None)
    overlaps = [max(0, min(end_ms, end) - max(start_ms, start)) for start, end in ranges]
    best = max(range(len(overlaps)), key=overlaps.__getitem__)
    return best if overlaps[best] > 0 else None


def _join_asr_tokens(values: list[str]) -> str:
    output = ""
    for value in values:
        token = re.sub(r"\s+", " ", str(value or "")).strip()
        if not token:
            continue
        if output and re.search(r"[A-Za-z0-9]$", output) and re.match(r"^[A-Za-z0-9]", token):
            output += " "
        output += token
    return output.strip()


def _transcripts_for_ranges(segments: list[dict], ranges: list[tuple[int, int]]) -> list[str]:
    values: list[list[str]] = [[] for _ in ranges]
    for segment in segments:
        words = segment.get("words") if isinstance(segment.get("words"), list) else []
        if words:
            tokens_by_range: dict[int, list[str]] = {}
            for word in words:
                if not isinstance(word, dict):
                    continue
                index = _best_range_index(
                    int(word.get("start_ms") or 0),
                    int(word.get("end_ms") or word.get("start_ms") or 0),
                    ranges,
                )
                text = str(word.get("text") or "").strip()
                if index is not None and text:
                    tokens_by_range.setdefault(index, []).append(text)
            for index, tokens in tokens_by_range.items():
                text = _join_asr_tokens(tokens)
                if text:
                    values[index].append(text)
            continue
        index = _best_range_index(
            int(segment.get("start_ms") or 0),
            int(segment.get("end_ms") or segment.get("start_ms") or 0),
            ranges,
        )
        text = str(segment.get("text") or "").strip()
        if index is not None and text and (not values[index] or values[index][-1] != text):
            values[index].append(text)
    return [" ".join(items) for items in values]


def _fallback_shot_analysis(shot: dict) -> dict:
    return {
        "shot_index": shot["index"],
        "story_role": "transition",
        "subject": "待确认",
        "action": "待确认",
        "scene": "待确认",
        "shot_size": "待确认",
        "camera": "待确认",
        "product": "待确认",
        "props": "待确认",
        "emotion": "待确认",
        "director_intent": "待确认",
        "description": "已完成镜头切分, 开启多模态模型后可生成画面语义分析.",
        "confidence": 0.0,
        "evidence": [],
    }


async def _analyze_shots(shots: list[dict]) -> list[dict]:
    if get_settings().llm_mock_mode:
        return [_fallback_shot_analysis(shot) for shot in shots]
    client = LLMClient()
    batch_size = max(1, min(8, get_settings().director_vision_batch_size))
    results: dict[int, dict] = {}
    for offset in range(0, len(shots), batch_size):
        batch = shots[offset : offset + batch_size]
        image_sources: list[str] = []
        mapping = []
        for shot in batch:
            frame_names = []
            for frame in shot["keyframes"]:
                image_sources.append(str(frame))
                frame_names.append(len(image_sources))
            mapping.append(
                {
                    "shot_index": shot["index"],
                    "start_ms": shot["start_ms"],
                    "end_ms": shot["end_ms"],
                    "transcript": shot.get("transcript", ""),
                    "images": frame_names,
                }
            )
        prompt = f"""你是短视频编导分析师. 按映射分析每个子分镜的连续关键帧, 只返回 JSON.
镜头映射: {mapping}
输出 shots 数组, 必须逐个覆盖 shot_index. 每项包含 shot_index, story_role, subject, action, scene, shot_size, camera, product, props, emotion, director_intent, description, confidence, evidence.
story_role 只能取 hook, pain_point, product_intro, feature, demo, proof, benefit, trust, cta, transition.
product 只填写画面中可识别的商品或品类. props 填写除商品外与表演或拍摄相关的可见道具. emotion 概括人物情绪或镜头营造的情绪.
director_intent 说明当前镜头在留人, 建立痛点, 展示卖点, 提供证据或转化中的作用. description 要说明人物身份或主体, 动作和画面内容. camera 包含摄影角度, 机位或运动方式. confidence 为 0 到 1.
只依据连续关键帧和给定 ASR 台词判断, 不虚构产品功效. 无法确认的字段用空字符串. evidence 是简短依据数组."""
        response = await client.chat_vision(
            prompt,
            image_sources,
            temperature=0.1,
            max_tokens=7000,
            image_limit=len(image_sources),
        )
        items = response.get("shots") if isinstance(response, dict) else None
        for item in items or []:
            if isinstance(item, dict) and str(item.get("shot_index", "")).isdigit():
                results[int(item["shot_index"])] = item
    return [results.get(shot["index"], _fallback_shot_analysis(shot)) for shot in shots]


def _text_similarity(left: object, right: object) -> float:
    left_text = re.sub(r"[\W_]", "", str(left or ""), flags=re.UNICODE).lower()
    right_text = re.sub(r"[\W_]", "", str(right or ""), flags=re.UNICODE).lower()
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def _should_merge_shots(left: dict, right: dict) -> bool:
    if int(right.get("start_ms") or 0) - int(left.get("end_ms") or 0) > 250:
        return False
    if int(right.get("end_ms") or 0) - int(left.get("start_ms") or 0) > 18_000:
        return False
    left_role = str(left.get("story_role") or "")
    right_role = str(right.get("story_role") or "")
    if left_role and right_role and left_role != right_role:
        return False
    scores = [
        _text_similarity(left.get("subject"), right.get("subject")),
        _text_similarity(left.get("action"), right.get("action")),
        _text_similarity(left.get("scene"), right.get("scene")),
        _text_similarity(left.get("product"), right.get("product")),
    ]
    return sum(score >= 0.68 for score in scores) >= 2 and max(scores) >= 0.78


def _group_similar_shots(shots: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for shot in shots:
        if groups and _should_merge_shots(groups[-1][-1], shot):
            groups[-1].append(shot)
        else:
            groups.append([shot])
    return groups


def _longest_field(shots: list[dict], key: str) -> str:
    values = [str(shot.get(key) or "").strip() for shot in shots]
    return max((value for value in values if value), key=len, default="")


def _combine_shot_group(shots: list[dict], index: int) -> dict:
    transcripts = list(dict.fromkeys(str(shot.get("transcript") or "").strip() for shot in shots if str(shot.get("transcript") or "").strip()))
    evidence = list(
        dict.fromkeys(
            str(item).strip()
            for shot in shots
            for item in (shot.get("evidence") if isinstance(shot.get("evidence"), list) else [])
            if str(item).strip()
        )
    )
    confidence_values = [_confidence(shot.get("confidence")) for shot in shots]
    return {
        "index": index,
        "start_ms": int(shots[0]["start_ms"]),
        "end_ms": int(shots[-1]["end_ms"]),
        "transcript": " ".join(transcripts),
        "story_role": _longest_field(shots, "story_role"),
        "subject": _longest_field(shots, "subject"),
        "action": _longest_field(shots, "action"),
        "scene": _longest_field(shots, "scene"),
        "shot_size": _longest_field(shots, "shot_size"),
        "camera": _longest_field(shots, "camera"),
        "product": _longest_field(shots, "product"),
        "props": _longest_field(shots, "props"),
        "emotion": _longest_field(shots, "emotion"),
        "director_intent": _longest_field(shots, "director_intent"),
        "description": _longest_field(shots, "description"),
        "confidence": sum(confidence_values) / max(1, len(confidence_values)),
        "evidence": evidence[:20],
    }


def _fallback_groups(shots: list[dict]) -> list[dict]:
    if not shots:
        return []
    group_count = min(5, max(1, math.ceil(len(shots) / 4)))
    groups = []
    for index in range(group_count):
        start = math.floor(index * len(shots) / group_count)
        end = math.floor((index + 1) * len(shots) / group_count) - 1
        title, description = SECTION_TEMPLATES[min(index, len(SECTION_TEMPLATES) - 1)]
        groups.append({"title": title, "start_shot": start, "end_shot": end, "description": description})
    return groups


def _normalize_groups(raw: object, shots: list[dict]) -> list[dict]:
    if not isinstance(raw, list) or not shots:
        return _fallback_groups(shots)
    title_by_shot: dict[int, tuple[str, str]] = {}
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0, min(len(shots) - 1, int(item.get("start_shot") or 0)))
            end = max(start, min(len(shots) - 1, int(item.get("end_shot") if item.get("end_shot") is not None else start)))
        except (TypeError, ValueError):
            continue
        title = str(item.get("title") or "内容展开")[:128]
        description = str(item.get("description") or "")[:4000]
        for shot_index in range(start, end + 1):
            title_by_shot[shot_index] = (title, description)
    fallback = _fallback_groups(shots)
    for group in fallback:
        for shot_index in range(group["start_shot"], group["end_shot"] + 1):
            title_by_shot.setdefault(shot_index, (group["title"], group["description"]))
    groups: list[dict] = []
    for shot_index in range(len(shots)):
        title, description = title_by_shot[shot_index]
        if groups and groups[-1]["title"] == title:
            groups[-1]["end_shot"] = shot_index
        else:
            groups.append({"title": title, "description": description, "start_shot": shot_index, "end_shot": shot_index})
    return groups


def _shot_indexes(value: object, shot_count: int) -> list[int]:
    if not isinstance(value, list):
        return []
    indexes: list[int] = []
    for item in value:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < shot_count and index not in indexes:
            indexes.append(index)
    return indexes[:20]


def _insight(value: object, fallback: dict, shot_count: int, *, include_type: bool = False) -> dict:
    item = value if isinstance(value, dict) else {"content": value} if value else {}
    result = {
        "content": _paragraph_text(item.get("content"), 2000) or fallback.get("content", ""),
        "shot_indexes": _shot_indexes(item.get("shot_indexes"), shot_count) or fallback.get("shot_indexes", []),
    }
    if include_type:
        result["type"] = _paragraph_text(item.get("type"), 128) or fallback.get("type", "")
    return result


def _fallback_video_insights(shots: list[dict]) -> dict:
    def content(shot: dict) -> str:
        return _paragraph_text(shot.get("transcript") or shot.get("description"), 1000)

    hook_indexes = [shot["index"] for shot in shots if shot.get("story_role") == "hook"][:3]
    cta_indexes = [shot["index"] for shot in shots if shot.get("story_role") == "cta"][-3:]
    if not hook_indexes and shots:
        hook_indexes = [0]
    if not cta_indexes and shots:
        cta_indexes = [shots[-1]["index"]]
    chain = []
    for shot in shots:
        if shot.get("story_role") in {"product_intro", "feature", "demo", "proof", "benefit"}:
            chain.append(
                {
                    "stage": shot.get("story_role", "feature"),
                    "content": content(shot),
                    "shot_indexes": [shot["index"]],
                }
            )
    average = sum(shot["end_ms"] - shot["start_ms"] for shot in shots) / max(1, len(shots)) / 1000
    return {
        "hook": {
            "content": content(shots[hook_indexes[0]]) if hook_indexes else "",
            "shot_indexes": hook_indexes,
        },
        "selling_point_chain": chain[:12],
        "rhythm": {
            "summary": f"平均镜头时长 {average:.1f}s",
            "average_shot_seconds": round(average, 2),
            "fast_sections": [],
            "slow_sections": [],
        },
        "cta": {
            "type": "行动引导" if cta_indexes else "",
            "content": content(shots[cta_indexes[-1]]) if cta_indexes else "",
            "shot_indexes": cta_indexes,
        },
    }


def _normalize_video_insights(result: dict, shots: list[dict]) -> dict:
    fallback = _fallback_video_insights(shots)
    chain: list[dict] = []
    for item in result.get("selling_point_chain") or []:
        if not isinstance(item, dict):
            continue
        content = _paragraph_text(item.get("content"), 2000)
        if content:
            chain.append(
                {
                    "stage": _paragraph_text(item.get("stage"), 128),
                    "content": content,
                    "shot_indexes": _shot_indexes(item.get("shot_indexes"), len(shots)),
                }
            )
    rhythm = result.get("rhythm") if isinstance(result.get("rhythm"), dict) else {}
    fallback_rhythm = fallback["rhythm"]
    return {
        "hook": _insight(result.get("hook"), fallback["hook"], len(shots)),
        "selling_point_chain": chain or fallback["selling_point_chain"],
        "rhythm": {
            "summary": _paragraph_text(rhythm.get("summary"), 2000) or fallback_rhythm["summary"],
            "average_shot_seconds": round(_number(rhythm.get("average_shot_seconds"), fallback_rhythm["average_shot_seconds"]), 2),
            "fast_sections": _string_list(rhythm.get("fast_sections"), 12),
            "slow_sections": _string_list(rhythm.get("slow_sections"), 12),
        },
        "cta": _insight(result.get("cta"), fallback["cta"], len(shots), include_type=True),
    }


async def _summarize_analysis(shots: list[dict]) -> dict:
    if get_settings().llm_mock_mode:
        return {
            "summary": "已完成场景切镜, 源视频台词映射和逐镜画面分析.",
            "tags": ["多模态拆解", "子分镜"],
            "product_name": "待确认",
            "selling_points": [],
            "selling_points_detail": "待确认",
            "target_audience": [],
            "target_audience_detail": "待确认",
            "target_pain_points": "待确认",
            "language": "中文",
            "groups": _fallback_groups(shots),
            **_fallback_video_insights(shots),
        }
    compact = [
        {
            "shot_index": shot["index"],
            "time": [shot["start_ms"], shot["end_ms"]],
            "transcript": shot.get("transcript", "")[:500],
            "story_role": shot.get("story_role", ""),
            "product": shot.get("product", "")[:300],
            "director_intent": shot.get("director_intent", "")[:500],
            "description": shot.get("description", "")[:800],
        }
        for shot in shots
    ]
    prompt = f"""汇总短视频子分镜分析, 只返回 JSON.
子分镜: {compact}
输出 summary, tags, product_name, selling_points, selling_points_detail, target_audience, target_audience_detail, target_pain_points, language, hook, selling_point_chain, rhythm, cta, groups.
hook 包含 content 和 shot_indexes. selling_point_chain 是数组, 每项包含 stage, content, shot_indexes.
rhythm 包含 summary, average_shot_seconds, fast_sections, slow_sections. cta 包含 type, content, shot_indexes.
groups 为连续脚本结构组, 每项包含 title, start_shot, end_shot, description. shot 下标从 0 开始, 必须无遗漏覆盖所有子分镜.
product_name 只能填写一个在视频中出现频率最高的主要产品全名, 禁止列出候选名称或使用顿号连接多个名称.
selling_points 是 3 到 5 个简短标签, target_audience 是 2 到 4 个简短标签, 每个标签只表达一个信息点且不写完整长句.
selling_points_detail 和 target_audience_detail 分别使用完整自然段落详细说明产品买点和受众人群, 不得输出数组或标签. target_pain_points 是一段简短总结文字.
只依据输入总结, 不虚构商品名称, 功效, 人群或痛点. 无法确认时用空字符串或空数组."""
    result = await LLMClient().chat_json([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=6000)
    return result if isinstance(result, dict) else {}


def _normalize_sections(raw: object, duration_seconds: float) -> list[dict]:
    if not isinstance(raw, list) or len(raw) < 2:
        return _fallback_sections(duration_seconds)
    normalized: list[dict] = []
    for index, item in enumerate(raw[:16]):
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": str(item.get("title") or f"镜头段落 {index + 1}")[:128],
                "start_ms": max(0, int(item.get("start_ms") or 0)),
                "end_ms": max(0, int(item.get("end_ms") or 0)),
                "source_text": str(item.get("source_text") or "")[:4000],
                "description": str(item.get("description") or "")[:4000],
                "shooting_notes": str(item.get("shooting_notes") or "")[:4000],
            }
        )
    return normalized if len(normalized) >= 2 else _fallback_sections(duration_seconds)


def _replace_analysis(db: Session, project: DirectorProject, groups: list[dict], shots: list[dict]) -> None:
    section_ids = [row.id for row in db.query(DirectorSection.id).filter_by(project_id=project.id).all()]
    if section_ids:
        db.query(DirectorShot).filter(DirectorShot.section_id.in_(section_ids)).delete(synchronize_session=False)
        db.query(DirectorAsset).filter(DirectorAsset.section_id.in_(section_ids)).delete(synchronize_session=False)
        db.query(DirectorSection).filter(DirectorSection.id.in_(section_ids)).delete(synchronize_session=False)
        db.flush()
    section_by_shot: dict[int, DirectorSection] = {}
    for index, group in enumerate(groups):
        grouped_shots = shots[group["start_shot"] : group["end_shot"] + 1]
        source_text = " ".join(
            dict.fromkeys(_text(shot.get("transcript"), 8000) for shot in grouped_shots if _text(shot.get("transcript"), 8000))
        )
        section = DirectorSection(
            tenant_id=project.tenant_id,
            project_id=project.id,
            sort_order=index,
            title=group["title"],
            start_ms=grouped_shots[0]["start_ms"],
            end_ms=grouped_shots[-1]["end_ms"],
            source_text=source_text,
            script_lines=[{"id": uuid.uuid4().hex, "text": source_text}] if source_text else [],
            description=group.get("description", ""),
            shooting_notes="参考子分镜中的主体, 动作, 景别和运镜进行复拍",
        )
        db.add(section)
        db.flush()
        for shot_index in range(group["start_shot"], group["end_shot"] + 1):
            section_by_shot[shot_index] = section
    for shot in shots:
        section = section_by_shot[shot["index"]]
        db.add(
            DirectorShot(
                tenant_id=project.tenant_id,
                project_id=project.id,
                section_id=section.id,
                sort_order=shot["index"],
                start_ms=shot["start_ms"],
                end_ms=shot["end_ms"],
                transcript=_text(shot.get("transcript"), 8000),
                story_role=_text(shot.get("story_role"), 64),
                subject=_text(shot.get("subject"), 4000),
                action=_text(shot.get("action"), 4000),
                scene=_text(shot.get("scene"), 4000),
                shot_size=_text(shot.get("shot_size"), 64),
                camera=_text(shot.get("camera"), 4000),
                description=_text(shot.get("description"), 8000),
                clip_url=_text(shot.get("clip_url"), 1024),
                thumbnail_url=_text(shot.get("thumbnail_url"), 1024),
                confidence=_confidence(shot.get("confidence")),
                analysis={
                    "evidence": _string_list(shot.get("evidence"), 20),
                    "product": _text(shot.get("product"), 1000),
                    "props": _text(shot.get("props"), 2000),
                    "emotion": _text(shot.get("emotion"), 1000),
                    "director_intent": _text(shot.get("director_intent"), 2000),
                    "keyframe_count": len(shot.get("keyframes") or []),
                },
            )
        )
        reference_url = shot.get("clip_url") or shot.get("thumbnail_url")
        if reference_url:
            db.add(
                DirectorAsset(
                    tenant_id=project.tenant_id,
                    project_id=project.id,
                    section_id=section.id,
                    role="reference",
                    media_kind="video" if shot.get("clip_url") else "image",
                    source="extracted",
                    url=reference_url,
                    thumbnail_url=shot.get("thumbnail_url", ""),
                    description=f"原视频子分镜 {shot['index'] + 1}",
                    duration_seconds=(shot["end_ms"] - shot["start_ms"]) / 1000,
                    sort_order=shot["index"],
                )
            )


def analyze_reference_project(project_id: int) -> None:
    db = SessionLocal()
    project = db.query(DirectorProject).filter_by(id=project_id).first()
    if not project:
        db.close()
        return
    try:
        settings = get_settings()
        project.status = "analyzing"
        project.progress_stage = "probing_media"
        project.error_message = ""
        db.commit()
        with tempfile.TemporaryDirectory(prefix="director-analysis-") as directory:
            workdir = Path(directory)
            local_path = get_storage().local_path(project.source_url)
            if not local_path:
                local_path = workdir / (Path(project.source_filename).name or "reference.mp4")
                _download_video(project.source_url, local_path)
            media_info = _probe_media(local_path)
            duration = float(media_info["duration_seconds"])
            project.source_duration_seconds = duration
            project.progress_stage = "detecting_shots"
            db.commit()
            ranges = _detect_shot_ranges(local_path, duration)

            project.progress_stage = "transcribing"
            db.commit()
            asr_segments: list[dict] = []
            warnings: list[str] = []
            asr_provider = resolve_director_asr_provider(settings)
            asr_status = "disabled" if asr_provider in {"none", "disabled"} else "pending"
            audio_path = workdir / "source-audio.mp3"
            audio_extracted = False
            if asr_status != "disabled" and media_info.get("has_audio") is not False:
                audio_extracted = _extract_audio(local_path, audio_path)
            if audio_extracted:
                try:
                    asr_segments = asyncio.run(LLMClient().transcribe(audio_path))
                    asr_status = "completed" if asr_segments else "empty"
                except Exception as exc:
                    asr_status = "failed"
                    logger.warning("Director ASR degraded for project %s: %s", project.id, exc)
                    warnings.append("ASR 识别失败, 当前结果不包含口播台词")
                    if settings.director_asr_required:
                        raise
            elif asr_status != "disabled":
                if media_info.get("has_audio") is True:
                    asr_status = "failed"
                    warnings.append("音轨提取失败, 当前结果不包含口播台词")
                    if settings.director_asr_required:
                        raise RuntimeError("FFmpeg 无法提取视频音轨")
                else:
                    asr_status = "no_audio"
            if media_info.get("has_audio") is None:
                media_info["has_audio"] = audio_extracted
                media_info["audio_track_count"] = 1 if audio_extracted else 0

            project.progress_stage = "extracting_keyframes"
            db.commit()
            shots = _extract_shot_media(local_path, workdir, ranges)
            shot_ranges_ms = [(shot["start_ms"], shot["end_ms"]) for shot in shots]
            for shot, transcript in zip(shots, _transcripts_for_ranges(asr_segments, shot_ranges_ms)):
                shot["transcript"] = transcript

            project.progress_stage = "analyzing_shots"
            db.commit()
            analyzed = asyncio.run(_analyze_shots(shots))
            for shot, result in zip(shots, analyzed):
                shot.update(result)

            similar_groups = _group_similar_shots(shots)
            if len(similar_groups) < len(shots):
                merged_dir = workdir / "merged"
                merged_dir.mkdir()
                merged_ranges = [
                    (group[0]["start_ms"] / 1000, group[-1]["end_ms"] / 1000)
                    for group in similar_groups
                ]
                merged_media = _extract_shot_media(local_path, merged_dir, merged_ranges)
                shots = []
                for index, (group, media) in enumerate(zip(similar_groups, merged_media)):
                    media.update(_combine_shot_group(group, index))
                    shots.append(media)

            project.progress_stage = "summarizing"
            db.commit()
            result = asyncio.run(_summarize_analysis(shots))
            groups = _normalize_groups(result.get("groups"), shots)
            insights = _normalize_video_insights(result, shots)

            project.progress_stage = "saving_clips"
            db.commit()
            storage = get_storage()
            for shot in shots:
                thumbnail = shot["thumbnail"]
                shot["thumbnail_url"] = storage.save_bytes(
                    thumbnail.read_bytes(), thumbnail.name, f"director/{project.id}/shots"
                )
                clip = shot.get("clip")
                shot["clip_url"] = (
                    storage.save_bytes(clip.read_bytes(), clip.name, f"director/{project.id}/shots")
                    if clip
                    else ""
                )
            _replace_analysis(db, project, groups, shots)
            project.analysis = {
                "summary": _text(result.get("summary"), 4000) or "已完成场景切镜, 源视频台词映射和逐镜画面分析.",
                "tags": _string_list(result.get("tags"), 50) or ["多模态拆解", "子分镜"],
                "product_name": _primary_product_name(result.get("product_name"), shots) or "待确认",
                "selling_points": _short_tags(result.get("selling_points"), 5),
                "selling_points_detail": _paragraph_text(result.get("selling_points_detail") or result.get("selling_points"), 4000) or "待确认",
                "target_audience": _short_tags(result.get("target_audience"), 4),
                "target_audience_detail": _paragraph_text(result.get("target_audience_detail") or result.get("target_audience"), 4000) or "待确认",
                "target_pain_points": _summary_text(result.get("target_pain_points"), 1000) or "待确认",
                "language": _text(result.get("language"), 32) or project.language,
                "frame_count": sum(len(shot["keyframes"]) for shot in shots),
                "shot_count": len(shots),
                "section_count": len(groups),
                "audio_analyzed": bool(asr_segments),
                "media": media_info,
                "asr": {
                    "provider": asr_provider,
                    "model": settings.director_asr_resource_id if asr_provider in {"volcengine", "volcengine_asr", "doubao_speech"} else settings.director_asr_model,
                    "status": asr_status,
                    "segment_count": len(asr_segments),
                },
                "warnings": warnings,
                "analysis_engine": "media-probe+scene-detection+asr+multimodal",
                **insights,
            }
            project.status = "analyzed"
            project.progress_stage = "completed"
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Director analysis failed for project %s", project_id)
        project = db.query(DirectorProject).filter_by(id=project_id).first()
        if project:
            project.status = "failed"
            project.progress_stage = "failed"
            project.error_message = _analysis_error_message(exc)
            db.commit()
    finally:
        db.close()


def _placeholder(path: Path, title: str, text: str) -> None:
    image = Image.new("RGB", (720, 1280), "#f3f3f0")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    title_font = ImageFont.truetype(str(font_path), 38) if font_path.exists() else ImageFont.load_default()
    body_font = ImageFont.truetype(str(font_path), 25) if font_path.exists() else ImageFont.load_default()
    draw.rectangle((48, 70, 672, 1210), outline="#d1d1cc", width=3)
    draw.text((76, 110), title[:20], font=title_font, fill="#111111")
    lines = []
    remaining = text.strip() or "等待添加视频素材"
    while remaining and len(lines) < 10:
        lines.append(remaining[:18])
        remaining = remaining[18:]
    draw.multiline_text((76, 210), "\n".join(lines), font=body_font, fill="#575752", spacing=18)
    image.save(path, "JPEG", quality=92)


def _render_ai_material_file(source: Path, output: Path, duration: float = 3.6) -> bool:
    return _run_ffmpeg(
        [
            _ffmpeg_executable(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-i",
            str(source),
            "-t",
            f"{duration:.2f}",
            "-an",
            "-vf",
            "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black,zoompan=z='min(zoom+0.0012,1.08)':d=90:s=720x1280:fps=25,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-movflags",
            "+faststart",
            str(output),
        ],
        timeout=120,
    )


def render_ai_material_video(image_url: str, project_id: int, duration: float = 3.6) -> str:
    """Turn an AI storyboard frame into a playable silent vertical material clip."""
    storage = get_storage()
    source = storage.local_path(image_url)
    if not source:
        return ""
    with tempfile.TemporaryDirectory(prefix="director-ai-material-") as directory:
        output = Path(directory) / "ai-material.mp4"
        ok = _render_ai_material_file(source, output, duration)
        if not ok or not output.exists():
            return ""
        return storage.save_bytes(output.read_bytes(), f"ai-material-{uuid.uuid4().hex}.mp4", f"director/{project_id}/materials")


def mix_silent_preview(project_id: int) -> None:
    db = SessionLocal()
    project = db.query(DirectorProject).filter_by(id=project_id).first()
    if not project:
        db.close()
        return
    try:
        project.status = "mixing"
        project.progress_stage = "building_preview"
        project.error_message = ""
        db.commit()
        sections = db.query(DirectorSection).filter_by(project_id=project.id).order_by(DirectorSection.sort_order).all()
        if not sections:
            raise ValueError("项目没有可混剪的镜头段落")
        ffmpeg = _ffmpeg_executable()
        storage = get_storage()
        with tempfile.TemporaryDirectory(prefix="director-mix-") as directory:
            workdir = Path(directory)
            segments: list[Path] = []
            for index, section in enumerate(sections):
                asset = (
                    db.query(DirectorAsset)
                    .filter_by(section_id=section.id, role="material")
                    .order_by(DirectorAsset.sort_order)
                    .first()
                )
                source = storage.local_path(asset.url) if asset else None
                media_kind = asset.media_kind if asset else "image"
                if not source:
                    source = workdir / f"placeholder-{index:02d}.jpg"
                    line_text = " ".join(str(line.get("text", "")) for line in section.script_lines)
                    _placeholder(source, section.title, line_text)
                    media_kind = "image"
                duration = max(1.5, min(8.0, (section.end_ms - section.start_ms) / 1000 or 3.0))
                segment = workdir / f"segment-{index:02d}.mp4"
                input_args = ["-loop", "1"] if media_kind == "image" else []
                command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *input_args, "-i", str(source)]
                command += [
                    "-t",
                    f"{duration:.2f}",
                    "-an",
                    "-vf",
                    "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black,fps=25,format=yuv420p",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    str(segment),
                ]
                result = subprocess.run(command, capture_output=True, text=True, timeout=120)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr[-1200:] or "镜头素材转码失败")
                segments.append(segment)
            concat_file = workdir / "segments.txt"
            concat_file.write_text("\n".join(f"file '{path.as_posix()}'" for path in segments), encoding="utf-8")
            output = workdir / "director-preview.mp4"
            result = subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output)],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-1200:] or "视频混剪失败")
            project.output_url = storage.save_bytes(output.read_bytes(), f"director-preview-{project.id}.mp4", f"director/{project.id}/outputs")
            project.status = "preview_ready"
            project.progress_stage = "completed"
            db.commit()
    except Exception as exc:
        db.rollback()
        project = db.query(DirectorProject).filter_by(id=project_id).first()
        if project:
            project.status = "failed"
            project.progress_stage = "failed"
            project.error_message = str(exc)[:2000]
            db.commit()
    finally:
        db.close()
