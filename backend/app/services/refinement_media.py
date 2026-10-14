"""Compile and render confirmed edits against verified, immutable media files."""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path
import re
import shutil
import subprocess

from app.services.director_workflow import _ffmpeg_executable, _probe_media
from app.services.storage import get_storage_by_name


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _upload_etag(asset: dict) -> str | None:
    provider = asset.get("storage_provider", "aliyun_oss") or "aliyun_oss"
    prefix = provider + ":"
    value = asset["content_hash"]
    if value.startswith(prefix) and re.fullmatch(r"[0-9a-fA-F]{32}(?:-[1-9][0-9]*)?", value[len(prefix):].strip('"')):
        return value[len(prefix):].strip('"')
    return None


def verified_path(asset: dict) -> Path:
    storage = get_storage_by_name(asset.get("storage_provider", "aliyun_oss"))
    path = storage.local_path(asset["object_url"])
    if not path or not path.is_file() or path.stat().st_size == 0:
        raise ValueError("源片或素材文件不存在, 请检查素材库")
    etag = _upload_etag(asset)
    if etag and storage.object_etag(asset["object_url"]).lower() != etag.lower():
        raise ValueError("源片或素材的上传版本已变化, 请重新创建精修任务")
    expected = asset.get("file_sha256") or (None if etag else asset["content_hash"])
    if expected and file_hash(path) != expected:
        raise ValueError("源片或素材文件内容已变化, 请重新创建精修任务")
    return path


def verified_asset(asset: dict, plan: dict) -> dict:
    if not _upload_etag(asset):
        return asset
    pin = next((row for row in plan.get("media_verifications", []) if row["asset_id"] == asset["asset_id"] and row["content_hash"] == asset["content_hash"]), None)
    if not pin:
        raise ValueError("方案尚未记录该素材的文件校验值, 请重新分析或保存方案后再确认")
    return {**asset, "file_sha256": pin["file_sha256"]}


def pin_plan_media(snapshot: dict, plan: dict, previous: dict | None = None) -> None:
    """Keep upload identity and byte identity separate without rewriting library assets."""
    used = {snapshot["source"]["asset_id"], *used_material_ids(plan)}
    pins = []
    for asset in [snapshot["source"], *snapshot["material_catalog"]]:
        if asset["asset_id"] not in used or not _upload_etag(asset):
            continue
        old = next((row for row in (previous or {}).get("media_verifications", []) if row["asset_id"] == asset["asset_id"] and row["content_hash"] == asset["content_hash"]), None)
        descriptor = {**asset, **({"file_sha256": old["file_sha256"]} if old else {})}
        path = verified_path(descriptor)
        pins.append({"asset_id": asset["asset_id"], "content_hash": asset["content_hash"], "file_sha256": file_hash(path)})
    plan["media_verifications"] = pins


def probe(path: Path) -> dict:
    metadata = _probe_media(path)
    if metadata["has_audio"] is None:
        # Bundled FFmpeg can inspect audio even when ffprobe is not installed.
        result = subprocess.run([_ffmpeg_executable(), "-hide_banner", "-i", str(path), "-t", "0", "-f", "null", "-"], capture_output=True, text=True, errors="replace", timeout=60)
        if result.returncode:
            raise ValueError("媒体文件无法解码")
        metadata["has_audio"] = bool(re.search(r"Stream #.*Audio:", result.stderr))
    if metadata["width"] <= 0 or metadata["height"] <= 0 or metadata["fps"] <= 0:
        raise ValueError("视频媒体信息无效")
    return metadata


def precise_probe(path: Path) -> dict:
    from app.config import get_settings
    settings = get_settings()
    executable = settings.director_ffprobe_path or shutil.which("ffprobe")
    if not executable:
        sibling = Path(_ffmpeg_executable()).with_name("ffprobe.exe" if __import__("os").name == "nt" else "ffprobe")
        executable = str(sibling) if sibling.is_file() else None
    if not executable:
        raise ValueError("精确时间协议需要 ffprobe, 不能用显示浮点帧率推测时间基")
    process = subprocess.run([executable,"-v","error","-show_streams","-show_format","-of","json",str(path)],
                             capture_output=True,text=True,errors="replace",timeout=60)
    if process.returncode:
        raise ValueError("无法读取媒体精确时间基")
    data = json.loads(process.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("媒体缺少视频流")
    try:
        rate = Fraction(video["avg_frame_rate"])
        time_base = Fraction(video["time_base"])
        if rate <= 0 or time_base <= 0:
            raise ValueError()
        if video.get("duration_ts") is not None:
            duration = int(video["duration_ts"])*time_base
            timestamp_source = "ffprobe-stream-pts"
        else:
            packets = subprocess.run([executable,"-v","error","-select_streams","v:0","-show_packets",
                "-show_entries","packet=pts,duration","-of","json",str(path)],capture_output=True,text=True,errors="replace",timeout=180)
            if packets.returncode:
                raise ValueError()
            values = json.loads(packets.stdout)["packets"]
            if not values or any(int(packet.get("duration", 0)) <= 0 for packet in values):
                raise ValueError()
            duration = (max(int(packet["pts"])+int(packet["duration"]) for packet in values)-min(int(packet["pts"]) for packet in values))*time_base
            timestamp_source = "ffprobe-packet-pts"
        if duration <= 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("媒体缺少可信帧率或视频 PTS 时长") from exc
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    audio_duration = str(int(audio["duration_ts"])*Fraction(audio["time_base"])) if audio.get("duration_ts") is not None and audio.get("time_base") else None
    audio_offset = (str(int(audio["start_pts"])*Fraction(audio["time_base"])-int(video.get("start_pts",0))*time_base)
                    if audio.get("start_pts") is not None and audio.get("time_base") else None)
    return {"frame_rate":str(rate),"nominal_frame_rate":video.get("r_frame_rate"),"time_base":str(time_base),
            "video_duration":str(duration),"video_duration_ms":round(duration*1000),"audio_duration":audio_duration,"audio_offset":audio_offset,"timestamp_source":timestamp_source,
            "normalization":"source-pts-to-cfr","input_frame_count":int(video["nb_frames"]) if str(video.get("nb_frames", "")).isdigit() else None}


def render_recipe(snapshot: dict, plan: dict) -> list[dict]:
    if plan.get("schema_version") not in {"video_refinement_plan.v1", "video_refinement_plan.v2", "video_refinement_plan.v3"}:
        raise ValueError("不支持的精修方案版本, 禁止使用旧渲染器处理")
    if plan.get("schema_version") in {"video_refinement_plan.v2", "video_refinement_plan.v3"}:
        if plan["execution_blockers"]:
            raise ValueError("请先解决方案中的具体执行阻塞")
        recipe = {key: plan[key] for key in ("schema_version", "video_track", "narration_track", "background_track", "subtitle_track", "source_to_output_map")}
        if "timing_version" in plan:
            recipe["timing_version"] = plan["timing_version"]
        if plan["schema_version"] == "video_refinement_plan.v3":
            recipe.update(summary=plan["summary"], renderer_version=plan["renderer_version"], generation_policy=plan.get("generation_policy", {}))
        return recipe
    if snapshot["is_mock"] or plan["summary"]["is_mock"]:
        raise ValueError("旧版示例方案不能渲染, 请使用真实数据重新分析")
    if plan["execution_blockers"]:
        raise ValueError("请先处理方案中的素材或人工编辑限制, 再生成预览")
    source = snapshot["source"]
    materials = {row["asset_id"]: row for row in snapshot["material_catalog"]}
    items = [row for row in plan["items"] if row["enabled"]]
    if not items:
        raise ValueError("请至少启用一项修改")
    removed, replacements, insertions = [], [], {}
    for item in items:
        start, end = item["range"]["start_ms"], item["range"]["end_ms"]
        if item["type"] == "PACE_RECUT":
            trim = -item["duration_delta_ms"]
            if trim:
                removed.append((start, start + trim) if item.get("cut_from") == "start" else (end - trim, end))
        elif item["type"] == "VISUAL_REPLACE":
            replacements.append(item)
        elif item["type"] == "SEGMENT_INSERT":
            insertions.setdefault(start, []).append(item)
        else:
            raise ValueError("当前仅画面, 片段插入和节奏修改支持自动渲染")
    points = sorted({0, source["duration_ms"], *insertions, *(point for pair in removed for point in pair), *(point for row in replacements for point in row["range"].values())})
    recipe = []

    def segment(video, video_start, duration, audio, audio_start, item_id=None):
        recipe.append({"video_asset_id": video["asset_id"], "video_start_ms": video_start,
            "duration_ms": duration, "audio_asset_id": audio["asset_id"] if audio else None,
            "audio_start_ms": audio_start, "item_id": item_id})

    for index, start in enumerate(points):
        for item in insertions.get(start, []):
            material = materials[item["material_asset_id"]]
            segment(material, item.get("material_start_ms", 0), item["duration_delta_ms"], material, item.get("material_start_ms", 0), item["item_id"])
        if index == len(points) - 1:
            continue
        end = points[index + 1]
        if any(left <= start and end <= right for left, right in removed):
            continue
        replacement = next((item for item in replacements if item["range"]["start_ms"] <= start and end <= item["range"]["end_ms"]), None)
        if replacement:
            material = materials[replacement["material_asset_id"]]
            offset = replacement.get("material_start_ms", 0) + start - replacement["range"]["start_ms"]
            keep = replacement.get("audio_strategy") == "keep_source"
            segment(material, offset, end - start, source if keep else material, start if keep else offset, replacement["item_id"])
        else:
            segment(source, start, end - start, source, start)
    if not recipe or sum(row["duration_ms"] for row in recipe) != plan["summary"]["expected_duration_ms"]:
        raise ValueError("渲染时间轴与方案预计时长不一致")
    return recipe


def render_preview(snapshot: dict, plan: dict, output: Path) -> tuple[dict, list[dict]]:
    if plan.get("schema_version") == "video_refinement_plan.v3":
        from app.services.refinement_render import render_v3
        return render_v3(snapshot, plan, output)
    if plan.get("schema_version") == "video_refinement_plan.v2":
        from app.services.refinement_render import render_v2
        return render_v2(snapshot, plan, output)
    recipe = render_recipe(snapshot, plan)
    catalog = {row["asset_id"]: row for row in [snapshot["source"], *snapshot["material_catalog"]]}
    catalog[snapshot["source"]["asset_id"]] = {**snapshot["source"], **plan.get("analysis",{}).get("source_traits",{})}
    used = {row[key] for row in recipe for key in ("video_asset_id", "audio_asset_id") if row[key] is not None}
    paths = {asset_id: verified_path(verified_asset(catalog[asset_id], plan)) for asset_id in used}
    metadata = {asset_id: probe(path) for asset_id, path in paths.items()}
    source = snapshot["source"]
    source_meta = metadata[source["asset_id"]] if source["asset_id"] in metadata else probe(verified_path(verified_asset(source, plan)))
    if (source_meta["width"], source_meta["height"]) != (source["width"], source["height"]):
        raise ValueError("源片实际画幅与冻结信息不一致")
    has_audio = any(row["audio_asset_id"] is not None and metadata[row["audio_asset_id"]]["has_audio"] for row in recipe)
    fps = source["fps"]
    width, height = source["width"], source["height"]
    if width % 2 or height % 2:
        raise ValueError("当前编码器需要偶数宽高, 请先验证源片画幅")
    parts = []
    for index, row in enumerate(recipe):
        vid, aud, duration = row["video_asset_id"], row["audio_asset_id"], row["duration_ms"] / 1000
        start = row["video_start_ms"] / 1000
        if start + duration > metadata[vid]["duration_seconds"] + 1 / fps:
            raise ValueError("实际素材长度不足, 请修改素材起点或区间时长")
        part = output.parent / f"part-{index:03d}.mp4"
        command = [_ffmpeg_executable(), "-hide_banner", "-y", "-ss", str(start), "-i", str(paths[vid])]
        filters = [f"[0:v]trim=duration={duration},setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v]"]
        if has_audio:
            if aud is not None and metadata[aud]["has_audio"]:
                audio_start = row["audio_start_ms"] / 1000
                if audio_start + duration > metadata[aud]["duration_seconds"] + 1 / fps:
                    raise ValueError("实际音轨长度不足")
                command += ["-ss", str(audio_start), "-i", str(paths[aud])]
                filters.append(f"[1:a]atrim=duration={duration},asetpts=PTS-STARTPTS,aresample=48000,apad[a]")
            else:
                command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                filters.append(f"[1:a]atrim=duration={duration},asetpts=PTS-STARTPTS[a]")
        command += ["-filter_complex", ";".join(filters), "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2"]
        command += ["-map", "[a]", "-c:a", "aac", "-ar", "48000", "-ac", "2"] if has_audio else ["-an"]
        command += ["-t", str(duration), "-map_metadata", "-1", "-metadata:s:v:0", "rotate=0", "-movflags", "+faststart", str(part)]
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=600)
        if result.returncode:
            raise ValueError("视频片段渲染失败, 请检查文件编码和修改区间")
        parts.append(part)
    concat = output.parent / "concat.txt"
    # Generated basenames avoid quoting user paths in the concat file.
    concat.write_text("\n".join(f"file '{part.name}'" for part in parts), encoding="utf-8")
    # Complex filters already bake orientation into pixels. Clear the inherited
    # MOV display matrix on the concat input so players do not rotate it again.
    result = subprocess.run([_ffmpeg_executable(), "-hide_banner", "-y", "-f", "concat", "-safe", "1", "-display_rotation", "0", "-i", str(concat), "-c", "copy", "-map_metadata", "-1", "-movflags", "+faststart", str(output)], capture_output=True, text=True, errors="replace", timeout=600)
    if result.returncode or not output.exists() or not output.stat().st_size:
        raise ValueError("完整预览合成失败")
    measured = probe(output)
    expected = plan["summary"]["expected_duration_ms"] / 1000
    if (measured["width"], measured["height"]) != (width, height) or measured["has_audio"] != has_audio or abs(measured["duration_seconds"] - expected) > max(.15, len(recipe) / fps + .1):
        raise ValueError("预览媒体信息与确认方案不一致")
    result = subprocess.run([_ffmpeg_executable(), "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"], capture_output=True, text=True, errors="replace", timeout=600)
    if result.returncode:
        raise ValueError("预览媒体解码检查失败")
    measured.update({"passed": True, "expected_duration_seconds": expected, "decode_checked": True,
        "burned_subtitles": any(catalog[row["video_asset_id"]].get("burned_subtitles", False) for row in recipe),
        "has_background_music": any(row["audio_asset_id"] is not None and catalog[row["audio_asset_id"]].get("has_background_music", False) for row in recipe)})
    return measured, recipe


def used_material_ids(plan):
    return {asset_id for row in plan["items"] if row["enabled"] for asset_id in ([s["asset_id"] for s in row.get("material_segments",[])] or ([row["material_asset_id"]] if row.get("material_asset_id") else []))}
