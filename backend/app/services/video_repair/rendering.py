from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import EditingPlanVersion, VideoAsset, VideoEditProject, VideoRepairTask
from app.services.storage import get_storage, get_storage_for_url
from app.services.tts import synthesize_speech
from app.services.video_analysis_queue import enqueue_video_analysis
from app.services.video_editing.plan_patch import apply_repair_plan
from app.services.video_editing.plan_normalization import ensure_video_track
from app.services.video_editing.renderer import _ffmpeg_executable, render_variant
from app.services.video_lineage import record_render_lineage
from app.services.video_repair.candidates import forbidden_lineage_ranges, resolve_candidate
from app.services.video_taxonomy import inherit_public_tags_to_final


def _probe_output(path: Path) -> dict:
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError("OUTPUT_VALIDATION_FAILED: 输出文件不存在或为空")
    executable = shutil.which("ffprobe")
    if executable:
        completed = subprocess.run([
            executable, "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,width,height", "-of", "json", str(path),
        ], capture_output=True, text=True, timeout=60, check=True)
        payload = json.loads(completed.stdout or "{}")
        streams = payload.get("streams") or []
        video = next((row for row in streams if row.get("codec_type") == "video"), None)
        audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
        if not video or not audio:
            raise RuntimeError("OUTPUT_VALIDATION_FAILED: 输出缺少画面或音轨")
        return {
            "duration": float((payload.get("format") or {}).get("duration") or 0),
            "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
        }
    try:
        import av

        with av.open(str(path)) as container:
            video_stream = next((row for row in container.streams if row.type == "video"), None)
            audio_stream = next((row for row in container.streams if row.type == "audio"), None)
            if not video_stream or not audio_stream:
                raise RuntimeError("OUTPUT_VALIDATION_FAILED: 输出缺少画面或音轨")
            duration = float(container.duration or 0) / 1_000_000
            return {"duration": duration, "width": int(video_stream.width or 0),
                    "height": int(video_stream.height or 0)}
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("OUTPUT_VALIDATION_FAILED: 无法读取输出媒体信息") from exc


def _validate_rendered_output(path: Path, planned_duration: float, aspect_ratio: str) -> dict:
    probe = _probe_output(path)
    # The contract allows an inclusive 100 ms container/codec rounding delta.
    if probe["duration"] <= 0 or round(abs(probe["duration"] - planned_duration), 3) > 0.1:
        raise RuntimeError(
            f"OUTPUT_VALIDATION_FAILED: 实际时长 {probe['duration']:.3f}s 与计划 {planned_duration:.3f}s 不一致")
    expected = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}[aspect_ratio]
    if (probe["width"], probe["height"]) != expected:
        raise RuntimeError("OUTPUT_VALIDATION_FAILED: 输出宽高与项目比例不一致")
    return probe


def _render_flat_visual(
    source_path: Path,
    candidate_path: Path,
    output: Path,
    *,
    target_start: float,
    target_end: float,
    candidate_start: float,
    source_duration: float,
    width: int,
    height: int,
    source_has_audio: bool,
) -> None:
    """Replace only picture pixels while preserving the flattened V1 audio track."""
    duration = target_end - target_start
    if duration <= 0 or target_start < 0 or target_end > source_duration + .05:
        raise RuntimeError("FLAT_RENDER_INVALID_RANGE: 替换时间段无效")
    ffmpeg = _ffmpeg_executable()
    command = [
        ffmpeg, "-y", "-i", str(source_path),
        "-ss", f"{candidate_start:.3f}", "-t", f"{duration:.3f}", "-i", str(candidate_path),
    ]
    if not source_has_audio:
        command += ["-f", "lavfi", "-t", f"{source_duration:.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo"]
    filters = (
        f"[0:v]fps=30,format=yuv420p[base];"
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps=30,format=yuv420p,"
        f"setpts=PTS-STARTPTS+{target_start:.3f}/TB[replacement];"
        f"[base][replacement]overlay=0:0:eof_action=pass:"
        f"enable='gte(t,{target_start:.3f})*lt(t,{target_end:.3f})'[video]"
    )
    command += [
        "-filter_complex", filters, "-map", "[video]",
        "-map", "0:a:0" if source_has_audio else "2:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "copy" if source_has_audio else "aac",
        "-t", f"{source_duration:.3f}", "-movflags", "+faststart", str(output),
    ]
    subprocess.run(command, capture_output=True, text=True, timeout=900, check=True)


async def _render_flat_task(db: Session, task: VideoRepairTask, source: VideoAsset) -> VideoRepairTask:
    actions = list((task.approved_plan_json or {}).get("actions") or [])
    if len(actions) != 1 or actions[0].get("type") != "VISUAL_REPLACE":
        raise HTTPException(409, "无原剪辑方案的成片只支持单段局部画面替换")
    action = actions[0]
    selected_id = action.get("selected_candidate_id") or (
        task.selected_candidates_json or {}).get(action.get("action_id"))
    candidate = resolve_candidate(
        db, tenant_id=task.tenant_id, source_asset=source,
        candidate_value=selected_id or "", forbidden_source_ranges={})
    if not candidate or selected_id not in (action.get("candidate_ids") or []):
        raise HTTPException(409, "已批准的候选素材已失效或无权访问")
    snapshot = action.get("selected_candidate_snapshot") or {}
    if snapshot and candidate.get("content_hash") != snapshot.get("content_hash"):
        raise HTTPException(409, detail={
            "code": "CANDIDATE_CHANGED_AFTER_APPROVAL",
            "message": "候选素材内容在批准后发生变化，请重新创建修复任务",
        })
    candidate_asset = db.query(VideoAsset).filter_by(
        id=candidate["source_asset_id"], tenant_id=task.tenant_id).first()
    if not candidate_asset:
        raise HTTPException(409, "候选源文件已不可用")

    target = action["target_range"]
    target_start = float(target["start_ms"]) / 1000
    target_end = float(target["end_ms"]) / 1000
    candidate_range = snapshot.get("source_range") or {}
    candidate_start = float(candidate_range.get(
        "start_ms", round(candidate["source_range"]["start"] * 1000))) / 1000

    task.status = "RENDERING"
    from datetime import datetime
    task.started_at = datetime.utcnow()
    db.commit(); db.refresh(task)
    try:
        source_path = await asyncio.to_thread(
            get_storage_for_url(source.object_url).local_path, source.object_url)
        candidate_path = await asyncio.to_thread(
            get_storage_for_url(candidate_asset.object_url).local_path, candidate_asset.object_url)
        if not source_path or not candidate_path:
            raise RuntimeError("无法下载源成片或候选素材")
        with tempfile.TemporaryDirectory(prefix=f"video-repair-flat-{task.id}-") as name:
            output = Path(name) / f"repair-{task.id}.mp4"
            await asyncio.to_thread(
                _render_flat_visual, source_path, candidate_path, output,
                target_start=target_start, target_end=target_end,
                candidate_start=candidate_start,
                source_duration=float(source.duration_seconds),
                width=int(source.width), height=int(source.height),
                source_has_audio=bool(source.has_audio))
            probe = await asyncio.to_thread(
                _probe_output, output)
            if round(abs(probe["duration"] - float(source.duration_seconds)), 3) > .1:
                raise RuntimeError("OUTPUT_VALIDATION_FAILED: 修复后视频时长发生变化")
            if (probe["width"], probe["height"]) != (source.width, source.height):
                raise RuntimeError("OUTPUT_VALIDATION_FAILED: 修复后视频尺寸发生变化")
            rendered = output.read_bytes()
            url = await get_storage().upload(
                rendered, output.name, f"video-repair/{task.id}/outputs")
        origins = []
        for value in (source.origin_asset_ids or []) + [source.asset_id, candidate_asset.asset_id]:
            if value not in origins:
                origins.append(value)
        generated = VideoAsset(
            tenant_id=task.tenant_id, project_id=None,
            product_id=source.product_id, asset_id=f"asset-{uuid.uuid4().hex[:12]}",
            filename=f"{Path(source.filename).stem}-审核修复V2.mp4",
            source="review_repair_flat_output", library_type="finished",
            asset_kind="final_output",
            generation_depth=max(source.generation_depth, candidate_asset.generation_depth) + 1,
            origin_asset_ids=origins, reusable_status="review_required",
            burned_subtitles=source.burned_subtitles,
            has_background_music=source.has_background_music,
            object_url=url, content_hash=hashlib.sha256(rendered).hexdigest(),
            duration_seconds=probe["duration"], width=probe["width"], height=probe["height"],
            fps=30, has_audio=True, status="queued_for_analysis", excluded=False,
        )
        db.add(generated); db.flush()
        inherit_public_tags_to_final(db, generated, [source, candidate_asset], task.created_by)
        task.status = "COMPLETED"
        task.output_asset_id = generated.id
        task.output_plan_version_id = None
        task.completed_at = datetime.utcnow()
        task.error_code = ""; task.error_message = ""
        db.commit(); db.refresh(task)
        enqueue_video_analysis()
        return task
    except Exception as exc:
        db.rollback()
        task = db.get(VideoRepairTask, task.id)
        task.status = "FAILED"
        task.error_code = "RENDER_FAILED"
        task.error_message = str(exc)[:1000]
        task.completed_at = datetime.utcnow()
        db.commit(); db.refresh(task)
        return task


def _referenced_public_ids(variant: dict) -> list[str]:
    result: list[str] = []
    for track in ("video_track", "narrative_segments"):
        for clip in variant.get(track) or []:
            value = clip.get("asset_id")
            if value and value not in result:
                result.append(str(value))
    return result


async def _regenerate_voiceovers(variant: dict, directory: Path, task_id: int) -> None:
    storage = get_storage()
    for index, clip in enumerate(variant.get("narrative_segments") or []):
        if not clip.pop("needs_tts_regeneration", False):
            continue
        local = directory / f"repair-voice-{index:03d}.mp3"
        await asyncio.to_thread(synthesize_speech, str(clip["speech_text"]), local)
        clip["audio_url"] = await storage.upload(
            local.read_bytes(), local.name, f"video-repair/{task_id}/voiceovers")


async def render_task(db: Session, task: VideoRepairTask) -> VideoRepairTask:
    """Render exactly once. A completed task always returns its existing V2."""
    task = db.query(VideoRepairTask).filter_by(
        id=task.id, tenant_id=task.tenant_id, created_by=task.created_by).with_for_update().one()
    if task.status == "COMPLETED":
        return task
    if task.status != "APPROVED" or not task.approved_plan_json:
        raise HTTPException(409, "修复方案尚未批准，不能渲染")
    source = db.query(VideoAsset).filter_by(
        id=task.source_asset_id, tenant_id=task.tenant_id, asset_kind="final_output").first()
    if not source:
        raise HTTPException(409, "源素材已不可用")
    if task.base_plan_version_id is None:
        return await _render_flat_task(db, task, source)
    base = db.query(EditingPlanVersion).filter_by(
        id=task.base_plan_version_id, tenant_id=task.tenant_id,
        validation_status="valid").first()
    if not base:
        raise HTTPException(409, "基线方案已不可用")
    variants = base.plan_json.get("variants") or []
    from app.models import VideoLineageSegment
    variant_ids = {row.variant_id for row in db.query(VideoLineageSegment).filter_by(
        tenant_id=task.tenant_id, target_asset_id=source.id).all()}
    selected_variants = [row for row in variants if str(row.get("variant_id")) in variant_ids]
    if len(selected_variants) != 1:
        raise HTTPException(409, "基线方案无法映射到唯一成片变体")
    base_plan = dict(base.plan_json)
    base_plan["variants"] = [ensure_video_track(selected_variants[0])]
    candidate_map: dict[str, dict] = {}
    issue_ranges = [action["target_range"] for action in task.approved_plan_json.get("actions") or []]
    forbidden = forbidden_lineage_ranges(
        db, tenant_id=task.tenant_id, target_asset_id=source.id, issue_ranges=issue_ranges)
    for action in task.approved_plan_json.get("actions") or []:
        selected_id = action.get("selected_candidate_id") or (
            task.selected_candidates_json or {}).get(action["action_id"])
        if not selected_id:
            continue
        candidate = resolve_candidate(
            db, tenant_id=task.tenant_id, source_asset=source, candidate_value=selected_id,
            forbidden_source_ranges=forbidden)
        if not candidate or selected_id not in (action.get("candidate_ids") or []):
            raise HTTPException(409, "已批准的候选素材已失效或无权访问")
        if action["type"] in {"AV_SEGMENT_REPLACE", "PROCESS_INSERT"} and not candidate["has_audio"]:
            raise HTTPException(409, "该修复动作要求候选素材具有完整音轨")
        snapshot = action.get("selected_candidate_snapshot")
        if snapshot:
            if candidate.get("content_hash") != snapshot.get("content_hash"):
                raise HTTPException(409, detail={
                    "code": "CANDIDATE_CHANGED_AFTER_APPROVAL",
                    "message": "候选素材内容在批准后发生变化，请重新创建修复任务",
                })
            source_range = snapshot.get("source_range") or {}
            candidate["source_range"] = {
                "start": round(float(source_range["start_ms"]) / 1000, 3),
                "end": round(float(source_range["end_ms"]) / 1000, 3),
            }
        candidate_map[action["action_id"]] = candidate
    valid_ids = {row[0] for row in db.query(VideoAsset.asset_id).filter(
        VideoAsset.tenant_id == task.tenant_id).all()}
    try:
        patched = apply_repair_plan(
            base_plan, task.approved_plan_json, candidate_map, valid_ids,
            task.id, task.base_plan_version_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    task.status = "RENDERING"
    from datetime import datetime
    task.started_at = datetime.utcnow()
    db.commit(); db.refresh(task)
    try:
        variant = patched["variants"][0]
        public_ids = _referenced_public_ids(variant)
        assets = db.query(VideoAsset).filter(
            VideoAsset.tenant_id == task.tenant_id,
            VideoAsset.asset_id.in_(public_ids)).all()
        if {row.asset_id for row in assets} != set(public_ids):
            raise RuntimeError("修复方案引用的素材不存在或无权访问")
        project = db.query(VideoEditProject).filter_by(
            id=base.project_id, tenant_id=task.tenant_id).first()
        if not project:
            raise RuntimeError("基线剪辑项目不存在")
        with tempfile.TemporaryDirectory(prefix=f"video-repair-{task.id}-") as name:
            directory = Path(name)
            await _regenerate_voiceovers(variant, directory, task.id)
            output = directory / f"repair-{task.id}.mp4"
            duration = await asyncio.to_thread(
                render_variant, variant, assets, output, project.aspect_ratio)
            probe = await asyncio.to_thread(
                _validate_rendered_output, output, float(variant["actual_duration_seconds"]),
                project.aspect_ratio)
            duration = probe["duration"]
            rendered = output.read_bytes()
            url = await get_storage().upload(
                rendered, output.name, f"video-repair/{task.id}/outputs")
        next_version = (db.query(func.max(EditingPlanVersion.version)).filter(
            EditingPlanVersion.project_id == base.project_id).scalar() or 0) + 1
        output_plan = EditingPlanVersion(
            tenant_id=task.tenant_id, project_id=base.project_id, version=next_version,
            plan_json=patched, model="video-repair-v1", validation_status="valid",
            validation_errors=[])
        db.add(output_plan); db.flush()
        max_depth = max((asset.generation_depth for asset in assets), default=0)
        origins = []
        for asset in assets:
            for value in (asset.origin_asset_ids or []) + [asset.asset_id]:
                if value not in origins:
                    origins.append(value)
        generated = VideoAsset(
            tenant_id=task.tenant_id, project_id=base.project_id,
            product_id=source.product_id, asset_id=f"asset-{uuid.uuid4().hex[:12]}",
            filename=f"{Path(source.filename).stem}-审核修复V2.mp4",
            source="review_repair_output", library_type="finished", asset_kind="final_output",
            generation_depth=max_depth + 1, origin_asset_ids=origins,
            reusable_status="review_required", burned_subtitles=source.burned_subtitles,
            has_background_music=source.has_background_music,
            object_url=url, content_hash=hashlib.sha256(rendered).hexdigest(),
            duration_seconds=duration,
            width=1080 if project.aspect_ratio != "16:9" else 1920,
            height=1920 if project.aspect_ratio == "9:16" else 1080,
            fps=30, has_audio=True, status="queued_for_analysis", excluded=False,
        )
        db.add(generated); db.flush()
        record_render_lineage(db, generated, variant, assets, output_plan.id)
        inherit_public_tags_to_final(db, generated, assets, task.created_by)
        task.status = "COMPLETED"
        task.output_asset_id = generated.id
        task.output_plan_version_id = output_plan.id
        task.completed_at = datetime.utcnow()
        task.error_code = ""; task.error_message = ""
        db.commit(); db.refresh(task)
        enqueue_video_analysis()
        return task
    except Exception as exc:
        db.rollback()
        task = db.get(VideoRepairTask, task.id)
        task.status = "FAILED"
        task.error_code = "RENDER_FAILED"
        task.error_message = str(exc)[:1000]
        task.completed_at = datetime.utcnow()
        db.commit(); db.refresh(task)
        return task
