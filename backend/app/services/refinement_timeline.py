"""Compile original coordinates into separate, immutable output tracks."""
from __future__ import annotations

from copy import deepcopy

from app.services.refinement_plan import content_hash
from app.services.refinement_requirements import verify_requirements


def compile_timeline(snapshot, items, brief, expected, *, manual, requirements, options, voiceovers):
    source = snapshot["source"]
    duration, fps = source["duration_ms"], source["fps"]
    catalog = {r["asset_id"]: r for r in snapshot["material_catalog"]}
    tolerance = max(1, round(1000 / fps))
    blockers, normalized = [], []
    removed, visuals, inserts, voices, compressions = [], [], {}, [], []
    ids, groups = set(), {}
    words = snapshot.get("words", [])
    cues = snapshot.get("transcript", [])

    def block(row, code, message):
        blockers.append({"item_id": row["item_id"], "code": code, "message": message})

    def align(value):
        if value in {0,duration}:
            return value
        return min(duration, round(round(value * fps / 1000) * 1000 / fps))

    def overlaps(a, b):
        return a[0] < b[1] and b[0] < a[1]

    def check_boundary(row, start, end=None):
        boundaries = [start] if end is None else [start, end]
        # Appending after the complete source never interrupts its speech.
        if end is None and start in {0, duration}:
            return
        if row["boundary_reviewed"]:
            return
        if source["has_audio"] and snapshot.get("asr_state") not in {"completed", "no_speech"}:
            block(row, "BOUNDARY_REVIEW_REQUIRED", "缺少可信语音时间, 请播放并人工确认插入或删除边界")
            return
        for point in boundaries:
            if any(w["start_ms"] - 60 < point < w["end_ms"] + 60 for w in words if 0 < point < duration):
                block(row, "SPEECH_BOUNDARY", "边界过于接近有效发音, 请移动边界或人工核对")
                return
        if end is None:
            if any(c["start_ms"] + tolerance < start < c["end_ms"] - tolerance for c in cues):
                block(row, "SENTENCE_BOUNDARY", "新增内容应位于完整句子边界, 请调整插入位置")
        elif row["edit_intent"] == "remove_pause":
            if not any(s["start_ms"] <= start and end <= s["end_ms"] for s in snapshot.get("silence_ranges", [])):
                block(row, "PAUSE_NOT_VERIFIED", "该区间未通过真实音频静音检测, 请核对后确认删除边界")
        else:
            affected = [c for c in cues if overlaps((start, end), (c["start_ms"], c["end_ms"]))]
            if row["edit_intent"] != "compress_expression" and any(start > c["start_ms"] + tolerance or end < c["end_ms"] - tolerance for c in affected):
                block(row, "INCOMPLETE_SENTENCE", "删除区间只覆盖了部分台词, 请选择完整内容或改短台词")
            row["affected_cue_ids"] = [c["cue_id"] for c in affected]

    def tts(row, length):
        from app.services.refinement_voiceover import voiceover_fingerprint
        voice = voiceovers.get(row["voiceover_id"])
        if not row["script"].strip():
            block(row, "SCRIPT_REQUIRED", "请填写新增或改写的完整台词")
            return None
        if not voice or voice["status"] != "ready" or voice["fingerprint"] != voiceover_fingerprint(row["script"], voice["parameters"]):
            block(row, "VOICEOVER_REQUIRED", "请先生成并绑定与当前台词一致的局部配音")
            return None
        if voice["duration_ms"] > length or (length - voice["duration_ms"] > tolerance and not row["narration_padding_reason"]):
            block(row, "VOICEOVER_DURATION", "配音实测时长与画面不匹配, 请调整台词或素材选段; 句间留白需明确说明")
        if row["subtitle_strategy"] == "regenerate" and not voice.get("timing", {}).get("segments"):
            block(row, "SUBTITLE_ALIGNMENT", "配音缺少可信字幕时间, 请重新对齐或明确选择不生成字幕")
        row["risk"] = (row["risk"] + "; 局部配音使用默认女声, 请核对与原声及音乐的衔接").strip("; ")[:2000]
        return voice

    for item in items:
        row = item.model_dump()
        if row["item_id"] in ids:
            raise ValueError("修改项 ID 不能重复")
        ids.add(row["item_id"])
        if manual:
            row["status"] = "HUMAN_EDITED"
        start, end = row["range"]["start_ms"], row["range"]["end_ms"]
        insertion = row["type"] == "SEGMENT_INSERT"
        if not (0 <= start == end <= duration if insertion else 0 <= start < end <= duration):
            raise ValueError("修改区间必须位于源成片内, 插入项使用相同起止位置")
        row["requested_range"] = deepcopy(row["range"])
        start, end = align(start), align(end)
        row["range"] = {"start_ms": start, "end_ms": end}
        row["frame_alignment_error_ms"] = {"start": start - item.range.start_ms, "end": end - item.range.end_ms}
        if not insertion and start >= end:
            raise ValueError("帧对齐后的修改区间为空")
        row["edit_intent"] = row["edit_intent"] or {"VISUAL_REPLACE":"replace_visual", "SEGMENT_INSERT":"insert_content", "PACE_RECUT":"remove_content"}.get(row["type"])
        if row["edit_intent"] == "reduce_shot_share" and row["type"] != "VISUAL_REPLACE":
            raise ValueError("减少镜头占比必须使用画面覆盖并保留完整原配音")
        if row["edit_intent"] == "insert_content" and not insertion:
            raise ValueError("新增内容必须使用插入项")
        if row["edit_intent"] in {"remove_pause", "remove_content", "compress_expression"} and row["type"] != "PACE_RECUT" and not row["edit_group_id"]:
            raise ValueError("裁切或压缩表达必须使用节奏项或关联编辑单元")
        segments = row["material_segments"]
        if not segments and row["material_asset_id"]:
            length = row["duration_delta_ms"] if insertion else end - start
            segments = [{"asset_id": row["material_asset_id"], "source_range": {"start_ms": row["material_start_ms"], "end_ms": row["material_start_ms"] + length}}]
        aligned = []
        for segment in segments:
            asset = catalog.get(segment["asset_id"])
            if not asset:
                raise ValueError("素材必须来自当前冻结快照中的同商品素材")
            left, right = segment["source_range"]["start_ms"], segment["source_range"]["end_ms"]
            if not 0 <= left < right <= asset["duration_ms"]:
                raise ValueError("素材截取范围为空或超过实测素材时长")
            a = round(round(left * fps / 1000) * 1000 / fps)
            b = min(asset["duration_ms"], round(round(right * fps / 1000) * 1000 / fps))
            if a >= b:
                raise ValueError("素材截取范围不足一帧")
            aligned.append({"asset_id": segment["asset_id"], "source_range":{"start_ms":a, "end_ms":b}, "requested_range": segment["source_range"], "frame_alignment_error_ms":{"start":a-left,"end":b-right}})
        row["material_segments"] = aligned
        if aligned:
            row["material_asset_id"] = aligned[0]["asset_id"]
            row["material_start_ms"] = aligned[0]["source_range"]["start_ms"]
        row["audio_strategy"] = "keep_source"
        row["material_audio_policy"] = "mute"
        if not row["enabled"]:
            normalized.append(row)
            continue
        if row["edit_group_id"]:
            groups.setdefault(row["edit_group_id"], []).append(row)
        if row["background_strategy"] in {"keep_independent","music_only"}:
            block(row,"MUSIC_SOURCE_REQUIRED","缺少已验证的独立音乐来源, 不能从混合音轨保留纯音乐")
        length = sum(s["source_range"]["end_ms"]-s["source_range"]["start_ms"] for s in aligned)
        if row["type"] in {"VISUAL_REPLACE", "SEGMENT_INSERT"} or row["edit_intent"] == "compress_expression":
            if not aligned:
                block(row, "MATERIAL_REQUIRED", "请选择足够长的同商品素材局部片段")
            elif row["type"] == "VISUAL_REPLACE" and length < end-start:
                block(row, "MATERIAL_TOO_SHORT", "选中素材长度不足, 请扩大区间或添加更多相关片段")
            if any(catalog[s["asset_id"]].get("burned_subtitles") for s in aligned):
                block(row, "BURNED_SUBTITLE_CONFLICT", "选中素材带烧录字幕, 请换用干净素材或人工处理")
            if any(h.get("lip_visible") for s in aligned for h in catalog[s["asset_id"]].get("analysis",{}).get("data",{}).get("highlights",[]) if h.get("start",0)*1000 < s["source_range"]["end_ms"] and h.get("end",0)*1000 > s["source_range"]["start_ms"]):
                row["confidence_level"] = "LOW"
                row["risk"] += "; 选段含讲话口型, 请核对与台词是否一致"
        if row["type"] == "VISUAL_REPLACE":
            if row["narration_strategy"] != "keep_source":
                raise ValueError("辅助画面或镜头占比调整必须保留原配音; 改写台词请使用关联配音项")
            if any(overlaps((start,end), (r["range"]["start_ms"],r["range"]["end_ms"])) for r in visuals):
                raise ValueError("同一画面轨上的替换区间不能重叠")
            row["duration_delta_ms"] = 0
            visuals.append(row)
            if source.get("burned_subtitles"):
                if not cues:
                    block(row, "SUBTITLE_SOURCE_REQUIRED", "替换画面会移除原烧录字幕, 缺少可信台词时间, 请补充转写")
                else:
                    row["subtitle_strategy"] = "regenerate"
        elif insertion:
            row["narration_strategy"] = "tts" if row["narration_strategy"] != "none" else "none"
            if length <= 0:
                length = max(row["duration_delta_ms"], tolerance)
            row["duration_delta_ms"] = length
            row["background_strategy"] = "none"
            if row["narration_strategy"] == "none" and not row["narration_padding_reason"]:
                block(row, "NARRATION_INTENT_REQUIRED", "无讲话的展示段需明确说明, 新增内容默认使用局部配音")
            if row["narration_strategy"] == "tts" and row["subtitle_strategy"] == "keep_visible":
                row["subtitle_strategy"] = "regenerate"
            check_boundary(row, start)
            inserts.setdefault(start, []).append(row)
        elif row["type"] == "PACE_RECUT":
            if row["edit_intent"] == "compress_expression":
                row["narration_strategy"] = "tts"
                row["background_strategy"] = "drop_replaced_interval"
                if source.get("has_background_music"):
                    row["risk"] += "; 精简台词会移除原区间的混合音乐和环境声"
                if row["subtitle_strategy"] == "keep_visible":
                    row["subtitle_strategy"] = "regenerate"
                row["duration_delta_ms"] = length - (end-start)
                if length >= end-start:
                    block(row, "COMPRESSION_DURATION", "精简表达后的片段必须短于原内容区间")
                check_boundary(row, start, end)
                compressions.append(row)
            else:
                cuts = row["removed_ranges"] or ([{"start_ms":start, "end_ms":start-row["duration_delta_ms"]} if row["cut_from"] == "start" else {"start_ms":end+row["duration_delta_ms"], "end_ms":end}] if row["duration_delta_ms"] < 0 else [])
                row["narration_strategy"] = "cut_source"
                row["removed_ranges"] = []
                for cut in cuts:
                    a,b = align(cut["start_ms"]),align(cut["end_ms"])
                    if not start <= a < b <= end:
                        raise ValueError("待删除区间必须位于修改范围内")
                    row["removed_ranges"].append({"start_ms":a,"end_ms":b})
                    check_boundary(row,a,b)
                    removed.append((a,b,row["item_id"]))
                row["duration_delta_ms"] = -sum(c["end_ms"]-c["start_ms"] for c in row["removed_ranges"])
        elif row["type"] == "VOICE_REPLACE":
            row["narration_strategy"] = "tts"
            if row["subtitle_strategy"] == "keep_visible":
                row["subtitle_strategy"] = "regenerate"
            row["duration_delta_ms"] = 0
            if any(overlaps((start,end),(r["range"]["start_ms"],r["range"]["end_ms"])) for r in voices):
                raise ValueError("同一配音轨的台词替换不能重叠")
            check_boundary(row,start,end)
            voices.append(row)
            if source.get("burned_subtitles") and not any(v["range"]["start_ms"] <= start and end <= v["range"]["end_ms"] for v in visuals):
                # Rechecked after all rows, so ordering of cross-track edits has no effect.
                pass
            if source.get("has_background_music"):
                row["background_strategy"] = "drop_replaced_interval"
                row["risk"] += "; 替换混合音轨会同时移除该区间的音乐和环境声"
        else:
            block(row,"HUMAN_REQUIRED","独立修改原烧录字幕需要干净画面, 请使用关联画面和可信字幕时间")
        normalized.append(row)

    for row in normalized:
        if row["enabled"] and any(dep not in ids for dep in row["dependencies"] if dep.startswith("item-")):
            raise ValueError("修改项依赖不存在")
    for row in voices:
        start,end = row["range"].values()
        if source.get("burned_subtitles") and not any(v["range"]["start_ms"] <= start and end <= v["range"]["end_ms"] for v in visuals):
            block(row,"BURNED_SUBTITLE_CONFLICT","改写台词会与原烧录字幕冲突, 请同时替换为干净画面")
    temporal = [(a,b,i) for a,b,i in removed] + [(r["range"]["start_ms"],r["range"]["end_ms"],r["item_id"]) for r in compressions]
    # Union deletion ranges so multiple edits never count the same milliseconds twice.
    merged = []
    for a,b,i in sorted(removed):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b,merged[-1][1]), list(dict.fromkeys([*merged[-1][2],i])))
        else:
            merged.append((a,b,[i]))
    for row in compressions:
        pair = tuple(row["range"].values())
        if any(overlaps(pair,(a,b)) for a,b,_ in merged) or any(other is not row and overlaps(pair,tuple(other["range"].values())) for other in compressions):
            raise ValueError("压缩表达不能与其他时长修改重叠")
    for anchor in inserts:
        if any(a < anchor < b for a,b,_ in temporal):
            raise ValueError("不能在被删除或压缩区间内部插入, 请调整锚点")
    for row in voices:
        if any(overlaps(tuple(row["range"].values()),(a,b)) for a,b,_ in temporal):
            related = next((c for c in compressions if c["edit_group_id"] and c["edit_group_id"] == row["edit_group_id"] and c["range"] == row["range"]),None)
            if not related:
                raise ValueError("配音替换不能跨越已删除区间")
            if row["script"] != related["script"] or row["voiceover_id"] != related["voiceover_id"]:
                raise ValueError("关联压缩单元的台词和配音绑定必须一致")
    points = sorted({0,duration,*inserts,*(p for a,b,_ in merged for p in (a,b)),*(p for r in compressions for p in r["range"].values())})
    mapping, video, narration, subtitles = [], [], [], []
    cursor = 0

    def clip(track, kind, asset_id, source_range, output_range, item_ids, digest, **extra):
        value = {"clip_id":f"{track}-{len(video) if track=='video' else len(narration)}-{output_range['start_ms']}","item_ids":item_ids,"source_kind":kind,"source_asset_id":asset_id,"source_range":source_range,"output_range":output_range,"content_hash":digest,**extra}
        (video if track=="video" else narration).append(value)
        return value

    def materials(row, out, length, offset=0):
        remaining = length
        for segment in row["material_segments"]:
            a,b = segment["source_range"].values()
            available = b-a
            if offset >= available:
                offset -= available
                continue
            take = min(remaining, available-offset)
            if take > 0:
                clip("video","material",segment["asset_id"],{"start_ms":a+offset,"end_ms":a+offset+take},{"start_ms":out,"end_ms":out+take},[row["item_id"]],catalog[segment["asset_id"]]["content_hash"])
                out += take; remaining -= take; offset = 0
            if remaining <= 0:
                break
        if remaining > 0:
            block(row,"VIDEO_COVERAGE","素材不足以覆盖完整画面, 请补充实际选段")

    def generated(row,out,length):
        voice = tts(row,length)
        if voice:
            clip("narration","tts",voice["id"],{"start_ms":0,"end_ms":voice["duration_ms"]},{"start_ms":out,"end_ms":out+length},[row["item_id"]],voice["content_hash"],audio_source_kind="independent_narration", padding_reason=row["narration_padding_reason"])
            if row["subtitle_strategy"] == "regenerate":
                for index,cue in enumerate(voice.get("timing",{}).get("segments",[])):
                    a,b = round(cue["start"]*1000),round(cue["end"]*1000)
                    if not 0 <= a < b <= voice["duration_ms"]+tolerance:
                        block(row,"SUBTITLE_ALIGNMENT","配音字幕时间越界")
                        continue
                    subtitles.append({"cue_id":f"tts-{voice['id']}-{index}","text":cue["text"],"start_ms":out+a,"end_ms":min(out+b,out+length),"source_kind":"tts","voiceover_id":voice["id"]})

    for index,start in enumerate(points):
        for row in inserts.get(start,[]):
            length = row["duration_delta_ms"]
            materials(row,cursor,length)
            if row["narration_strategy"] == "tts":
                generated(row,cursor,length)
            mapping.append({"kind":"inserted","source_range":{"start_ms":start,"end_ms":start},"output_range":{"start_ms":cursor,"end_ms":cursor+length},"item_ids":[row["item_id"]]})
            cursor += length
        if index == len(points)-1:
            break
        end = points[index+1]
        cut = next((entry for entry in merged if entry[0] <= start and end <= entry[1]),None)
        if cut:
            mapping.append({"kind":"removed","source_range":{"start_ms":start,"end_ms":end},"output_range":{"start_ms":cursor,"end_ms":cursor},"item_ids":cut[2]})
            continue
        compression = next((r for r in compressions if r["range"]["start_ms"] <= start < r["range"]["end_ms"]),None)
        if compression:
            if start == compression["range"]["start_ms"]:
                length = sum(s["source_range"]["end_ms"]-s["source_range"]["start_ms"] for s in compression["material_segments"])
                materials(compression,cursor,length)
                generated(compression,cursor,length)
                mapping.append({"kind":"rewritten","source_range":compression["range"],"output_range":{"start_ms":cursor,"end_ms":cursor+length},"item_ids":[compression["item_id"]]})
                cursor += length
            continue
        length = end-start
        mapping.append({"kind":"retained","source_range":{"start_ms":start,"end_ms":end},"output_range":{"start_ms":cursor,"end_ms":cursor+length},"item_ids":[]})
        cuts = sorted({start,end,*(p for r in visuals for p in r["range"].values() if start < p < end)})
        for a,b in zip(cuts,cuts[1:]):
            row = next((r for r in visuals if r["range"]["start_ms"] <= a and b <= r["range"]["end_ms"]),None)
            out = cursor + a-start
            if row:
                materials(row,out,b-a,a-row["range"]["start_ms"])
                if row["subtitle_strategy"] == "regenerate":
                    for cue in cues:
                        left,right = max(a,cue["start_ms"]),min(b,cue["end_ms"])
                        if left < right:
                            subtitles.append({"cue_id":cue["cue_id"]+f"-{out}","text":cue["text"],"start_ms":out+left-a,"end_ms":out+right-a,"source_kind":"source"})
            else:
                clip("video","source",source["asset_id"],{"start_ms":a,"end_ms":b},{"start_ms":out,"end_ms":out+b-a},[],source["content_hash"])
        audio_points = sorted({start,end,*(p for r in voices for p in r["range"].values() if start < p < end)})
        for a,b in zip(audio_points,audio_points[1:]):
            row = next((r for r in voices if r["range"]["start_ms"] <= a and b <= r["range"]["end_ms"]),None)
            out = cursor + a-start
            if row:
                if a != row["range"]["start_ms"] or b != row["range"]["end_ms"]:
                    block(row,"VOICE_RANGE_SPLIT","台词替换不能被插入锚点分割, 请调整插入位置")
                generated(row,out,b-a)
            elif source["has_audio"]:
                # Coalesce adjacent original narration across visual edits.
                if narration and narration[-1]["source_kind"] == "source" and narration[-1]["source_range"]["end_ms"] == a and narration[-1]["output_range"]["end_ms"] == out:
                    narration[-1]["source_range"]["end_ms"] = b
                    narration[-1]["output_range"]["end_ms"] = out+b-a
                else:
                    clip("narration","source",source["asset_id"],{"start_ms":a,"end_ms":b},{"start_ms":out,"end_ms":out+b-a},[],source["content_hash"],audio_source_kind="mixed")
        cursor += length
    if cursor <= 0:
        raise ValueError("精修后视频时长必须大于零")
    if options.get("duration_preference") == "preserve" and cursor != duration:
        blockers.append({"item_id":"","code":"DURATION_CONFLICT","message":"当前方案改变了时长, 与保持原时长的明确要求冲突"})
    target = options.get("target_duration_ms")
    if options.get("duration_preference") == "shorten" and (cursor >= duration or target and cursor > target+tolerance):
        blockers.append({"item_id":"","code":"DURATION_CONFLICT","message":"方案尚未达到缩短目标, 请调整完整台词或裁切区间"})
    resolution = verify_requirements(requirements, normalized, blockers)
    alignment = [{"item_id":row["item_id"],"requested_range":row.get("requested_range"),"actual_range":row["range"],"error_ms":row.get("frame_alignment_error_ms"),"material_segments":deepcopy(row["material_segments"])} for row in normalized]
    for row in normalized:
        row.pop("requested_range", None)  # retained separately in alignment record
        row.pop("frame_alignment_error_ms", None)
        for segment in row["material_segments"]:
            segment.pop("requested_range",None); segment.pop("frame_alignment_error_ms",None)
    return {"schema_version":"video_refinement_plan.v2","source":deepcopy(source),
            "summary":{"objective":brief,"expected_result":expected,"expected_duration_ms":cursor,"duration_delta_ms":cursor-duration,"duration_reason":"由原片坐标和独立音画轨推导; 预览后核对实测时长","confidence_level":"LOW" if any(r["enabled"] and r["confidence_level"]=="LOW" for r in normalized) else "MEDIUM","is_mock":False},
            "items":normalized,"execution_blockers":blockers,"requirements_resolution":resolution,
            "requirements_fingerprint":content_hash({"brief":brief,"expected":expected,"options":options}),
            "material_evaluation":deepcopy(snapshot.get("material_evaluation",{})),"snapshot_hash":content_hash(snapshot),
            "frame_alignment":alignment,"video_track":video,"narration_track":narration,"background_track":[],"subtitle_track":subtitles,"source_to_output_map":mapping,
            "voiceover_assets":list({r["voiceover_id"]:voiceovers[r["voiceover_id"]] for r in normalized if r["enabled"] and r["voiceover_id"] in voiceovers}.values()),
            "findings_not_applied":["沿用原成片画幅", "素材拍摄原声关闭, 画面与叙事音频独立编排"]}
