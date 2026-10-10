from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.video_editing.plan_validation import validate_repair_variant
from app.services.video_editing.plan_normalization import ensure_video_track


RISKY_COPY = ("百分百", "100%", "永久", "根治", "最有效", "最好", "第一", "唯一", "保证")


def _range(row: dict[str, Any], key: str) -> tuple[float, float]:
    value = row.get(key) or {}
    return float(value["start"]), float(value["end"])


def _slice_clip(clip: dict[str, Any], start: float, end: float) -> dict[str, Any]:
    timeline_start, _ = _range(clip, "timeline_range")
    source_start, _ = _range(clip, "source_range")
    result = deepcopy(clip)
    result["timeline_range"] = {"start": round(start, 3), "end": round(end, 3)}
    result["source_range"] = {
        "start": round(source_start + start - timeline_start, 3),
        "end": round(source_start + end - timeline_start, 3),
    }
    result["duration_seconds"] = round(end - start, 3)
    if "subtitle_words" in result:
        words = []
        for word in result.get("subtitle_words") or []:
            word_start = float(word.get("start", start))
            word_end = float(word.get("end", word_start))
            if word_end <= start or word_start >= end:
                continue
            copied = deepcopy(word)
            copied["start"] = round(max(start, word_start), 3)
            copied["end"] = round(min(end, word_end), 3)
            words.append(copied)
        result["subtitle_words"] = words
    return result


def replace_range(clips: list[dict[str, Any]], target: dict[str, float],
                  candidate: dict[str, Any], *, audio: bool = False) -> list[dict[str, Any]]:
    start, end = float(target["start"]), float(target["end"])
    if start < 0 or end <= start:
        raise ValueError("替换目标时间段无效")
    candidate_source = candidate["source_range"]
    available = float(candidate_source["end"]) - float(candidate_source["start"])
    if available + 0.05 < end - start:
        raise ValueError("候选片段时长不足，禁止无提示循环画面")
    output: list[dict[str, Any]] = []
    covered = 0.0
    inserted = False
    for clip in clips:
        clip_start, clip_end = _range(clip, "timeline_range")
        overlap_start, overlap_end = max(start, clip_start), min(end, clip_end)
        if overlap_end <= overlap_start:
            output.append(deepcopy(clip))
            continue
        covered += overlap_end - overlap_start
        if clip_start < start:
            output.append(_slice_clip(clip, clip_start, start))
        if not inserted:
            replacement = {
                "asset_id": candidate["source_asset_public_id"],
                "highlight_id": candidate["highlight_id"],
                "source_range": {
                    "start": round(float(candidate_source["start"]), 3),
                    "end": round(float(candidate_source["start"]) + end - start, 3),
                },
                "timeline_range": {"start": round(start, 3), "end": round(end, 3)},
                "duration_seconds": round(end - start, 3),
                "content_role": candidate.get("content_role", "core_content"),
                "repair_candidate_id": candidate["id"],
            }
            if audio:
                replacement.update({
                    "audio_strategy": "source", "speech_text": "", "subtitle_words": [],
                    "audio_fade_seconds": min(0.1, (end - start) / 2),
                })
            output.append(replacement)
            inserted = True
        if clip_end > end:
            output.append(_slice_clip(clip, end, clip_end))
    if not inserted or abs(covered - (end - start)) > 0.05:
        raise ValueError("目标时间段没有被现有轨道完整覆盖")
    return output


def _shift_clip(clip: dict[str, Any], delta: float) -> dict[str, Any]:
    result = deepcopy(clip)
    start, end = _range(result, "timeline_range")
    result["timeline_range"] = {"start": round(start + delta, 3), "end": round(end + delta, 3)}
    for word in result.get("subtitle_words") or []:
        if "start" in word:
            word["start"] = round(float(word["start"]) + delta, 3)
        if "end" in word:
            word["end"] = round(float(word["end"]) + delta, 3)
    return result


def insert_segment(clips: list[dict[str, Any]], at: float, candidate: dict[str, Any],
                   *, audio: bool) -> list[dict[str, Any]]:
    source = candidate["source_range"]
    duration = float(source["end"]) - float(source["start"])
    inserted = {
        "asset_id": candidate["source_asset_public_id"],
        "highlight_id": candidate["highlight_id"],
        "source_range": deepcopy(source),
        "timeline_range": {"start": round(at, 3), "end": round(at + duration, 3)},
        "duration_seconds": round(duration, 3),
        "content_role": candidate.get("content_role", "usage"),
        "repair_candidate_id": candidate["id"],
    }
    if audio:
        inserted.update({"audio_strategy": "source", "speech_text": "", "subtitle_words": []})
    result: list[dict[str, Any]] = []
    did_insert = False
    for clip in clips:
        start, end = _range(clip, "timeline_range")
        if not did_insert and at <= start + 0.05:
            result.append(deepcopy(inserted)); did_insert = True
        if not did_insert and start < at < end:
            result.append(_slice_clip(clip, start, at))
            result.append(deepcopy(inserted)); did_insert = True
            result.append(_shift_clip(_slice_clip(clip, at, end), duration))
        else:
            result.append(_shift_clip(clip, duration) if did_insert else deepcopy(clip))
    if not did_insert:
        last_end = _range(clips[-1], "timeline_range")[1] if clips else 0.0
        if abs(at - last_end) > 0.05:
            raise ValueError("插入位置不在轨道范围内")
        result.append(inserted)
    return result


def _contains_timeline_range(value: Any) -> bool:
    if isinstance(value, dict):
        return "timeline_range" in value or any(_contains_timeline_range(row) for row in value.values())
    if isinstance(value, list):
        return any(_contains_timeline_range(row) for row in value)
    return False


def _assert_insert_tracks_supported(variant: dict[str, Any]) -> None:
    supported = {"video_track", "narrative_segments"}
    unknown = [key for key, value in variant.items()
               if key not in supported and _contains_timeline_range(value)]
    if unknown:
        raise ValueError("UNSUPPORTED_TIMELINE_TRACK_FOR_INSERT: " + ", ".join(sorted(unknown)))


def _target_seconds(action: dict[str, Any]) -> dict[str, float]:
    target = action["target_range"]
    if "start_ms" in target:
        return {"start": round(float(target["start_ms"]) / 1000, 3),
                "end": round(float(target["end_ms"]) / 1000, 3)}
    return {"start": float(target["start"]), "end": float(target["end"])}


def apply_repair_plan(base_plan: dict[str, Any], repair_plan: dict[str, Any],
                      candidates: dict[str, dict[str, Any]], valid_asset_ids: set[str],
                      repair_task_id: int, base_plan_version_id: int) -> dict[str, Any]:
    patched = deepcopy(base_plan)
    variants = patched.get("variants") or []
    if len(variants) != 1:
        raise ValueError("首版修复仅支持唯一基线成片方案")
    variant = ensure_video_track(variants[0])
    variants[0] = variant
    for action in repair_plan.get("actions") or []:
        action_id = action["action_id"]
        action_type = action["type"]
        if action_type == "FULL_REPLAN":
            raise ValueError("FULL_REPLAN 需要重新调用剪辑规划，不属于局部补丁")
        if action_type == "AUDIO_CAPTION_REWRITE":
            target_start, target_end = float(action["target_range"]["start"]), float(action["target_range"]["end"])
            changed = 0
            for clip in variant.get("narrative_segments") or []:
                clip_start, clip_end = _range(clip, "timeline_range")
                if clip_end <= target_start or clip_start >= target_end:
                    continue
                if clip.get("audio_strategy") != "ai_voiceover":
                    raise ValueError("问题范围包含真人原声，禁止自动改写")
                original = str(clip.get("speech_text") or "")
                rewritten = original
                for phrase in RISKY_COPY:
                    rewritten = rewritten.replace(phrase, "")
                rewritten = rewritten.strip("，。！？；、 ")
                if not rewritten or rewritten == original:
                    raise ValueError("无法在不编造事实的前提下自动生成合规改写")
                clip["speech_text"] = rewritten
                clip["subtitle_words"] = []
                clip["needs_tts_regeneration"] = True
                changed += 1
            if not changed:
                raise ValueError("问题范围未命中可编辑的系统口播")
            continue
        candidate = candidates.get(action_id)
        if not candidate:
            raise ValueError(f"修复动作 {action_id} 未选择候选素材")
        target = _target_seconds(action)
        if action_type == "VISUAL_REPLACE":
            variant["video_track"] = replace_range(variant.get("video_track") or [], target, candidate)
        elif action_type == "AV_SEGMENT_REPLACE":
            variant["video_track"] = replace_range(variant.get("video_track") or [], target, candidate)
            variant["narrative_segments"] = replace_range(
                variant.get("narrative_segments") or [], target, candidate, audio=True)
        elif action_type == "PROCESS_INSERT":
            _assert_insert_tracks_supported(variant)
            at = float(target["start"])
            variant["video_track"] = insert_segment(
                variant.get("video_track") or [], at, candidate, audio=False)
            variant["narrative_segments"] = insert_segment(
                variant.get("narrative_segments") or [], at, candidate, audio=True)
            duration = candidate["source_range"]["end"] - candidate["source_range"]["start"]
            variant["actual_duration_seconds"] = round(
                float(variant["actual_duration_seconds"]) + duration, 3)
            variant["target_duration_seconds"] = round(
                float(variant.get("target_duration_seconds") or variant["actual_duration_seconds"]) + duration, 3)
        else:
            raise ValueError(f"首版补丁引擎暂不支持动作 {action_type}")
    patched["repair_metadata"] = {
        "base_plan_version_id": base_plan_version_id,
        "repair_task_id": repair_task_id,
        "actions": [
            {"action_id": row["action_id"], "type": row["type"], "target_range": row["target_range"],
             "preserve_original_audio": row["type"] == "VISUAL_REPLACE"}
            for row in repair_plan.get("actions") or []
        ],
    }
    errors = validate_repair_variant(variant, valid_asset_ids)
    if errors:
        raise ValueError("修复后时间轴无效: " + "; ".join(errors[:10]))
    return patched
