from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from app.schemas.video_refinement import RefinementItem


def content_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _compile_v1(snapshot: dict, items: list[RefinementItem], brief: str, expected_result: str, *, manual: bool) -> dict:
    """Validate an editable draft; only derive estimates, never authorize rendering."""
    source = snapshot["source"]
    duration = source["duration_ms"]
    ids: set[str] = set()
    materials = {row["asset_id"]: row for row in snapshot["material_catalog"]}
    ranges: list[tuple[int, int]] = []
    normalized = []
    blockers = []
    for item in items:
        if item.item_id in ids:
            raise ValueError("修改项 ID 不能重复")
        ids.add(item.item_id)
        start, end = item.range.start_ms, item.range.end_ms
        insertion = item.type == "SEGMENT_INSERT"
        if not (0 <= start == end <= duration if insertion else 0 <= start < end <= duration):
            raise ValueError("修改区间必须位于源视频内, 且结束时间大于开始时间")
        if item.type not in {"PACE_RECUT", "SEGMENT_INSERT"} and item.duration_delta_ms:
            raise ValueError("当前仅节奏修改支持时长变化")
        if item.type == "PACE_RECUT" and not -(end - start) <= item.duration_delta_ms <= 0:
            raise ValueError("缩短时长不能超过修改区间时长, 且不能增加时长")
        if insertion and item.duration_delta_ms <= 0:
            raise ValueError("插入片段时长必须大于零")
        if item.type not in {"VISUAL_REPLACE", "SEGMENT_INSERT"} and item.material_asset_id is not None:
            raise ValueError("当前只有画面替换可以选择素材")
        if item.material_asset_id is not None and item.material_asset_id not in materials:
            raise ValueError("候选素材必须来自冻结快照中的同商品素材")
        row = item.model_dump()
        if insertion:
            row["audio_strategy"] = "use_material"
            if "插入片段使用素材原声" not in row["risk"]:
                row["risk"] = (row["risk"] + "; 插入片段使用素材原声, 请核对音画和衔接").strip("; ")[:2000]
        if manual:
            row["status"] = "HUMAN_EDITED"
        if item.material_asset_id is not None:
            length = item.duration_delta_ms if insertion else end - start
            if materials[item.material_asset_id]["duration_ms"] > 0 and item.material_start_ms + length > materials[item.material_asset_id]["duration_ms"]:
                raise ValueError("替换或插入区间超过候选素材的可用时长")
        if item.enabled and item.type in {"PACE_RECUT", "VISUAL_REPLACE"}:
            if any(start < previous_end and end > previous_start for previous_start, previous_end in ranges):
                raise ValueError("启用的画面和节奏修改区间不能重叠")
            ranges.append((start, end))
        if item.type in {"VOICE_REPLACE", "SUBTITLE_EDIT"}:
            limitation = snapshot["capabilities"]["voice" if item.type == "VOICE_REPLACE" else "subtitles"]["reason"]
            row["risk"] = limitation
            if item.enabled:
                blockers.append({"item_id": item.item_id, "code": "HUMAN_REQUIRED", "message": limitation})
        if item.enabled and item.type in {"VISUAL_REPLACE", "SEGMENT_INSERT"} and item.material_asset_id is None:
            blockers.append({"item_id": item.item_id, "code": "MATERIAL_REQUIRED", "message": "请补充并映射同商品素材"})
        normalized.append(row)
    for item in normalized:
        if item["enabled"] and item["type"] == "SEGMENT_INSERT" and any(start < item["range"]["start_ms"] < end for start, end in ranges):
            raise ValueError("插入位置不能位于其他修改区间内部")
    delta = sum(item["duration_delta_ms"] for item in normalized if item["enabled"])
    if duration + delta <= 0:
        raise ValueError("精修后的视频时长必须大于零")
    return {
        "schema_version": "video_refinement_plan.v1", "source": deepcopy(source),
        "summary": {"objective": brief, "expected_result": expected_result,
                    "expected_duration_ms": duration + delta, "duration_delta_ms": delta,
                    "duration_reason": "汇总当前启用的节奏修改项; 实际时长需渲染后测量" if delta else "当前启用项保持源视频时长",
                    "confidence_level": "LOW" if any(row["confidence_level"] == "LOW" and row["enabled"] for row in normalized) else "MEDIUM",
                    "is_mock": snapshot["is_mock"]},
        "items": normalized, "execution_blockers": blockers,
        "findings_not_applied": ["画幅沿用源片, 不建议调整", "未修改区间保留原始音画和字幕"],
    }


def compile_plan(snapshot: dict, items: list[RefinementItem], brief: str, expected_result: str, *, manual: bool,
                 requirements: list[dict] | None = None, options: dict | None = None, voiceovers: dict | None = None) -> dict:
    options = options or {}
    protocol = options.get("timing_protocol", "v2")
    if protocol not in {"v2", "v3"}:
        raise ValueError("不支持的精修时间协议, 禁止降级处理")
    if snapshot.get("schema_version") not in {None, "video_refinement_snapshot.v1", "video_refinement_snapshot.v2"}:
        raise ValueError("不支持的精修快照版本")
    if snapshot.get("schema_version") != "video_refinement_snapshot.v2":
        if protocol == "v3":
            raise ValueError("精确时间协议需要真实媒体快照")
        return _compile_v1(snapshot, items, brief, expected_result, manual=manual)
    from app.services.refinement_requirements import duration_options
    options = duration_options(brief, expected_result, options, snapshot["source"]["duration_ms"], requirements=requirements)
    records, blockers = {}, []
    if options.get("authorization_version"):
        from app.services.refinement_authorization import apply_authorizations
        items = [item.model_copy(deep=True) for item in items]
        records, blockers = apply_authorizations(items, requirements or [], snapshot["source"]["duration_ms"],
                                                brief=brief, expected=expected_result, manual=manual,
                                                overrides=options.get("manual_permissions") if manual else None, options=options)
    if protocol == "v3":
        from app.services.refinement_precise import compile_precise
        result = compile_precise(snapshot, items, brief, expected_result, manual=manual,
                                 requirements=requirements or [], options=options, voiceovers=voiceovers or {})
    else:
        from app.services.refinement_timeline import compile_timeline
        result = compile_timeline(snapshot, items, brief, expected_result, manual=manual,
                                  requirements=requirements or [], options=options, voiceovers=voiceovers or {})
    if records:
        from app.services.refinement_requirements import verify_requirements
        result["edit_authorizations"] = records
        result["execution_blockers"].extend(blockers)
        result["requirements_resolution"] = verify_requirements(requirements or [], result["items"], result["execution_blockers"], options=options)
    if options.get("localization"):
        from app.services.refinement_selection import checked_range
        from app.services.refinement_requirements import verify_requirements
        catalog = {asset["asset_id"]:asset for asset in snapshot["material_catalog"]}
        evidence = []
        for item in [RefinementItem.model_validate(row) for row in result["items"]]:
            if not item.enabled:
                continue
            for segment in item.material_segments:
                candidate = snapshot.get("local_candidates", {}).get(segment.candidate_id)
                try:
                    if not candidate or candidate["asset_id"] != segment.asset_id or candidate["content_hash"] != catalog[segment.asset_id]["content_hash"]:
                        raise ValueError("局部选段缺少当前素材的核验依据")
                    window = checked_range(segment.source_range.model_dump(), candidate["safe_range"])
                    if candidate["content_kind"] == "action":
                        checked_range(candidate["preferred_range"], window)
                    evidence.append({"item_id":item.item_id, **deepcopy(candidate), "source_range":window})
                    for clip in result["video_track"]:
                        if item.item_id in clip["item_ids"] and clip["source_asset_id"] == segment.asset_id:
                            actual = clip["source_range"]
                            if actual["start_ms"] < window["end_ms"] and window["start_ms"] < actual["end_ms"]:
                                checked_range(actual, candidate["safe_range"])
                                if candidate["content_kind"] == "action":
                                    checked_range(candidate["preferred_range"], actual)
                except (ValueError, KeyError):
                    result["execution_blockers"].append({"item_id":item.item_id,"code":"CONTENT_REVIEW_REQUIRED","category":"content", "message":"选段缺少可信局部核验或超出动作安全边界, 请重新定位素材"})
        result["local_selection"] = evidence
        result["requirements_resolution"] = verify_requirements(requirements or [], result["items"], result["execution_blockers"], options=options)
    from app.services.refinement_requirements import verify_goal_constraints
    from app.services.refinement_authorization import autonomous_editing
    if not autonomous_editing(options):
        verify_goal_constraints(brief, expected_result, requirements or [], result["items"], result["execution_blockers"])
    result["generation_policy"] = {key:options[key] for key in ("authorization_version", "timing_protocol", "localization", "content_reselection", "display_tail_ms", "policy_version", "tts_speaker") if key in options}
    return result
