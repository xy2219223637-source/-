from __future__ import annotations

import hashlib
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import VideoAsset, VideoRepairAttachment, VideoRepairPreview, VideoRepairTask
from app.services.storage import get_storage, get_storage_for_url
from app.services.video_editing.renderer import _ffmpeg_executable
from app.services.video_repair.rendering import _probe_output, _render_flat_visual


def _local(url: str) -> Path:
    path = get_storage_for_url(url).local_path(url)
    if not path:
        raise RuntimeError("MEDIA_UNAVAILABLE: 无法读取渲染素材")
    return Path(path)


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=900, check=True)
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc))[-2000:]
        raise RuntimeError(f"FFMPEG_RENDER_FAILED: {message}") from exc


def _verified_asset(db: Session, task: VideoRepairTask, ref: dict[str, Any]) -> VideoAsset:
    asset = db.query(VideoAsset).filter_by(
        id=ref.get("ref_id"), tenant_id=task.tenant_id, product_id=task.product_id,
        excluded=False,
    ).first()
    if not asset or asset.content_hash != ref.get("content_hash"):
        raise RuntimeError("MATERIAL_CHANGED: 视觉素材不存在或内容已经变化")
    return asset


def _verified_audio(db: Session, task: VideoRepairTask, ref: dict[str, Any]) -> VideoRepairAttachment | VideoAsset:
    if ref.get("source_type") == "VIDEO_ASSET":
        row = _verified_asset(db, task, ref)
        if not row.has_audio:
            raise RuntimeError("AUDIO_CHANGED: 素材原声不可用")
        return row
    row = db.query(VideoRepairAttachment).filter_by(
        id=ref.get("attachment_id"), tenant_id=task.tenant_id, task_id=task.id,
        status="READY",
    ).first()
    if not row or row.content_hash != ref.get("content_hash"):
        raise RuntimeError("AUDIO_CHANGED: 音频附件不存在或内容已经变化")
    return row


def _render_audio_replace(source: VideoAsset, source_path: Path, audio_path: Path,
                          action: dict[str, Any], output: Path) -> None:
    target = action["target_range"]
    start = float(target["start_ms"]) / 1000
    end = float(target["end_ms"]) / 1000
    duration = end - start
    if start < 0 or duration <= 0 or end > float(source.duration_seconds) + .05:
        raise RuntimeError("INVALID_TARGET_RANGE: 音频替换时间段无效")
    source_range = (action.get("audio") or {}).get("source_range") or {}
    audio_start = float(source_range.get("start_ms", 0)) / 1000
    audio_end = float(source_range.get("end_ms", 0)) / 1000
    trim = f"atrim=start={audio_start:.3f}"
    if audio_end > audio_start:
        trim += f":end={audio_end:.3f}"
    fade_in = min(duration, max(0, int(action.get("fade_in_ms", 80))) / 1000)
    fade_out = min(duration, max(0, int(action.get("fade_out_ms", 80))) / 1000)
    ffmpeg = _ffmpeg_executable()
    command = [ffmpeg, "-y", "-i", str(source_path), "-i", str(audio_path)]
    base_index = 0
    if not source.has_audio:
        command += ["-f", "lavfi", "-t", f"{source.duration_seconds:.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo"]
        base_index = 2
    filters = (
        f"[{base_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"volume=enable='between(t,{start:.3f},{end:.3f})':volume=0[base];"
        f"[1:a]{trim},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,"
        f"apad,atrim=duration={duration:.3f},afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f},"
        f"adelay={round(start * 1000)}|{round(start * 1000)}[replacement];"
        "[base][replacement]amix=inputs=2:duration=first:normalize=0[audio]"
    )
    command += ["-filter_complex", filters, "-map", "0:v:0", "-map", "[audio]",
                "-c:v", "copy", "-c:a", "aac", "-t", f"{source.duration_seconds:.3f}",
                "-movflags", "+faststart", str(output)]
    _run(command)


def _render_insert(source: VideoAsset, source_path: Path, visual_path: Path, audio_path: Path,
                   action: dict[str, Any], output: Path) -> None:
    insert_at = float(action["insert_at_ms"]) / 1000
    duration = float(action["duration_ms"]) / 1000
    source_duration = float(source.duration_seconds)
    if insert_at < 0 or insert_at > source_duration + .05 or duration <= 0:
        raise RuntimeError("INVALID_INSERT_RANGE: 内容插入位置或时长无效")
    visual_range = (action.get("visual") or {}).get("source_range") or {}
    visual_start = float(visual_range.get("start_ms", 0)) / 1000
    audio_range = (action.get("audio") or {}).get("source_range") or {}
    audio_start = float(audio_range.get("start_ms", 0)) / 1000
    audio_end = float(audio_range.get("end_ms", 0)) / 1000
    audio_trim = f"atrim=start={audio_start:.3f}"
    if audio_end > audio_start:
        audio_trim += f":end={audio_end:.3f}"
    ffmpeg = _ffmpeg_executable()
    command = [ffmpeg, "-y", "-i", str(source_path), "-i", str(visual_path),
               "-i", str(audio_path)]
    source_audio_index = 0
    if not source.has_audio:
        command += ["-f", "lavfi", "-t", f"{source_duration:.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo"]
        source_audio_index = 3
    parts: list[str] = []
    concat_inputs: list[str] = []
    if insert_at > .001:
        parts += [
            f"[0:v]trim=start=0:end={insert_at:.3f},setpts=PTS-STARTPTS,fps=30,format=yuv420p[v0]",
            f"[{source_audio_index}:a]atrim=start=0:end={insert_at:.3f},asetpts=PTS-STARTPTS,"
            "aformat=sample_rates=48000:channel_layouts=stereo[a0]",
        ]
        concat_inputs.append("[v0][a0]")
    parts += [
        f"[1:v]trim=start={visual_start:.3f}:duration={duration:.3f},setpts=PTS-STARTPTS,"
        f"scale={int(source.width)}:{int(source.height)}:force_original_aspect_ratio=increase,"
        f"crop={int(source.width)}:{int(source.height)},fps=30,format=yuv420p,"
        f"tpad=stop_mode=clone:stop_duration={duration:.3f},trim=duration={duration:.3f}[vi]",
        f"[2:a]{audio_trim},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,"
        f"apad,atrim=duration={duration:.3f}[ai]",
    ]
    concat_inputs.append("[vi][ai]")
    if insert_at < source_duration - .001:
        parts += [
            f"[0:v]trim=start={insert_at:.3f}:end={source_duration:.3f},setpts=PTS-STARTPTS,"
            "fps=30,format=yuv420p[v2]",
            f"[{source_audio_index}:a]atrim=start={insert_at:.3f}:end={source_duration:.3f},"
            "asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[a2]",
        ]
        concat_inputs.append("[v2][a2]")
    parts.append(f"{''.join(concat_inputs)}concat=n={len(concat_inputs)}:v=1:a=1[video][audio]")
    command += ["-filter_complex", ";".join(parts), "-map", "[video]", "-map", "[audio]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac",
                "-movflags", "+faststart", str(output)]
    _run(command)


def render_preview(db: Session, task: VideoRepairTask, preview: VideoRepairPreview) -> VideoRepairPreview:
    source = db.query(VideoAsset).filter_by(
        id=task.source_asset_id, tenant_id=task.tenant_id, asset_kind="final_output").first()
    snapshot = preview.source_snapshot_json or {}
    if not source or source.content_hash != snapshot.get("content_hash"):
        raise RuntimeError("SOURCE_CHANGED: 源成片不存在或内容已经变化")
    plan = preview.plan_snapshot_json or {}
    action = plan.get("action") or {}
    source_path = _local(source.object_url)
    expected_duration = float(source.duration_seconds)
    with tempfile.TemporaryDirectory(prefix=f"repair-preview-{task.id}-") as name:
        output = Path(name) / f"preview-{preview.id}.mp4"
        if action.get("type") == "VISUAL_REPLACE":
            material = _verified_asset(db, task, action["material"])
            target, source_range = action["target_range"], action["material"]["source_range"]
            _render_flat_visual(
                source_path, _local(material.object_url), output,
                target_start=float(target["start_ms"]) / 1000,
                target_end=float(target["end_ms"]) / 1000,
                candidate_start=float(source_range["start_ms"]) / 1000,
                source_duration=expected_duration, width=int(source.width), height=int(source.height),
                source_has_audio=bool(source.has_audio),
            )
        elif action.get("type") == "AUDIO_REPLACE":
            audio = _verified_audio(db, task, action["audio"])
            _render_audio_replace(source, source_path, _local(audio.object_url), action, output)
        elif action.get("type") == "CONTENT_INSERT":
            visual = _verified_asset(db, task, action["visual"])
            audio = _verified_audio(db, task, action["audio"])
            _render_insert(source, source_path, _local(visual.object_url),
                           _local(audio.object_url), action, output)
            expected_duration += float(action["duration_ms"]) / 1000
        else:
            raise RuntimeError("UNSUPPORTED_ACTION: 预览动作不受支持")
        probe = _probe_output(output)
        if abs(probe["duration"] - expected_duration) > .15:
            raise RuntimeError("OUTPUT_VALIDATION_FAILED: 预览时长不符合方案")
        if (probe["width"], probe["height"]) != (source.width, source.height):
            raise RuntimeError("OUTPUT_VALIDATION_FAILED: 预览尺寸发生变化")
        rendered = output.read_bytes()
        preview.object_url = get_storage().save_bytes(
            rendered, output.name, f"video-repair/{task.id}/previews")
    preview.content_hash = hashlib.sha256(rendered).hexdigest()
    preview.duration_seconds = probe["duration"]
    preview.width, preview.height = probe["width"], probe["height"]
    preview.status = "READY"
    preview.error_code = preview.error_message = ""
    preview.completed_at = datetime.utcnow()
    return preview
