"""Compile the existing edit graph in frame coordinates, serialize source time separately."""
from copy import deepcopy
from fractions import Fraction

from app.schemas.video_refinement import RefinementItem
from app.services.refinement_plan import content_hash
from app.services.refinement_requirements import verify_requirements
from app.services.refinement_timeline import compile_timeline
from app.services.refinement_timing import (AUDIO_RATE, PRECISE_TIMING_VERSION, candidate_frame_bounds,
    frame_at_ms, frame_rate, ms_at_frame, precise_tail_allowed, required_frames, sample_at_frame)


def compile_precise(snapshot, items, brief, expected, *, manual, requirements, options, voiceovers):
    source = snapshot["source"]
    if not source.get("frame_rate") or not source.get("video_duration"):
        raise ValueError("缺少可信有理数帧率或视频时间基, 不能生成精确时间方案")
    fps = str(frame_rate(source["frame_rate"]))
    duration_frames = Fraction(source["video_duration"])*frame_rate(fps)
    if duration_frames <= 0:
        raise ValueError("源片精确视频时长无效")
    total_source = (duration_frames.numerator+duration_frames.denominator-1)//duration_frames.denominator
    original_duration = source["duration_ms"]

    def source_frame(point):
        return total_source if point == original_duration else frame_at_ms(point, fps)

    def convert_range(value, converter):
        return {"start_ms":converter(value["start_ms"]), "end_ms":converter(value["end_ms"])}

    context = deepcopy(snapshot)
    context["source"].update(duration_ms=total_source, fps=fps)
    for key in ("words", "transcript", "silence_ranges"):
        for value in context.get(key, []):
            value.update(convert_range(value, source_frame))
    catalog = {a["asset_id"]:a for a in context["material_catalog"]}
    real_catalog = {a["asset_id"]:a for a in snapshot["material_catalog"]}
    for asset in catalog.values():
        asset["duration_ms"] = required_frames(asset["duration_ms"], fps)
        # The semantic validator uses the same internal coordinate unit.
        for highlight in asset.get("analysis", {}).get("data", {}).get("highlights", []):
            highlight["start"] = frame_at_ms(round(highlight.get("start", 0)*1000), fps)/1000
            highlight["end"] = frame_at_ms(round(highlight.get("end", 0)*1000), fps)/1000
    converted, origins = [], {}
    for item in items:
        row = item.model_dump()
        row["range"] = convert_range(row["range"], source_frame)
        row["removed_ranges"] = [convert_range(value, source_frame) for value in row["removed_ranges"]]
        row["duration_delta_ms"] = frame_at_ms(row["duration_delta_ms"], fps)
        segments = row["material_segments"]
        if not segments and row["material_asset_id"]:
            length = item.duration_delta_ms if item.type == "SEGMENT_INSERT" else item.range.end_ms-item.range.start_ms
            segments = [{"asset_id":item.material_asset_id,"source_range":{"start_ms":item.material_start_ms,"end_ms":item.material_start_ms+length}}]
        converted_segments = []
        for segment in segments:
            raw = segment["source_range"]
            asset = real_catalog.get(segment["asset_id"])
            if not asset or not 0 <= raw["start_ms"] < raw["end_ms"] <= asset["duration_ms"]:
                raise ValueError("素材不属于当前快照或超过实测时长")
            # Source timestamps are not output-frame indices. Only length is budgeted.
            length = Fraction(raw["end_ms"]-raw["start_ms"], 1000)*frame_rate(fps)
            count = length.numerator//length.denominator
            restored = candidate_frame_bounds(raw["start_ms"], raw["end_ms"], fps)
            # Permit only the known half-ms UI serialization uncertainty.
            if abs(ms_at_frame(restored[1]-restored[0], fps)-(raw["end_ms"]-raw["start_ms"])) <= 1:
                count = restored[1]-restored[0]
            start = frame_at_ms(raw["start_ms"], fps)
            if count <= 0:
                raise ValueError("素材选段不足一帧")
            converted_segments.append({"asset_id":segment["asset_id"],"source_range":{"start_ms":start,"end_ms":start+count}})
            origins.setdefault(item.item_id, []).append({"asset_id":segment["asset_id"],"frame_start":start,"frame_end":start+count,"range":raw,"candidate_id":segment.get("candidate_id")})
        row["material_segments"] = converted_segments
        row["material_start_ms"] = frame_at_ms(row["material_start_ms"], fps)
        converted.append(RefinementItem.model_validate(row))
    internal_voices = deepcopy(voiceovers)
    for voice in internal_voices.values():
        # This field is used only for graph shape; sample capacity is checked below.
        voice["duration_ms"] = required_frames(voice["duration_ms"], fps)
    internal_options = deepcopy(options)
    if options.get("target_duration_ms"):
        internal_options["target_duration_ms"] = frame_at_ms(options["target_duration_ms"], fps)
    graph = compile_timeline(context, converted, brief, expected, manual=manual, requirements=[],
                             options=internal_options, voiceovers=internal_voices, frame_coordinates=True)
    rows = {row["item_id"]:row for row in graph["items"]}
    blockers = graph["execution_blockers"]
    original_audio_samples = sample_at_frame(total_source, fps)
    if source.get("audio_duration"):
        samples = Fraction(source["audio_duration"])*AUDIO_RATE
        original_audio_samples = (samples.numerator+samples.denominator-1)//samples.denominator
    if source.get("has_audio") and not source.get("audio_duration"):
        blockers.append({"item_id":"","code":"AUDIO_METADATA_REQUIRED","category":"audio","message":"源音轨缺少可信有效时长, 不能推测原台词样本边界"})
    if source.get("has_audio") and source.get("audio_offset") != "0":
        blockers.append({"item_id":"","code":"SOURCE_AUDIO_OFFSET","category":"audio","message":"源音轨偏移尚未验证为零, 不能自动改变原音画同步"})
    if source.get("has_audio") and original_audio_samples > sample_at_frame(total_source, fps):
        blockers.append({"item_id":"","code":"SOURCE_AUDIO_DURATION","category":"audio","message":"源片有效音频超出输出画面帧预算, 禁止截断原台词"})

    def display_range(value):
        return {"start_ms":ms_at_frame(value["start_ms"], fps), "end_ms":ms_at_frame(value["end_ms"], fps)}

    def source_time(point):
        return original_duration if point == total_source else ms_at_frame(point, fps)

    def original_range(clip):
        value = clip["source_range"]
        if clip["source_kind"] == "source":
            return convert_range(value, source_time)
        for item_id in clip["item_ids"]:
            for origin in origins.get(item_id, []):
                if origin["asset_id"] == clip["source_asset_id"] and origin["frame_start"] <= value["start_ms"] < value["end_ms"] <= origin["frame_end"]:
                    left = origin["range"]["start_ms"] + ms_at_frame(value["start_ms"]-origin["frame_start"], fps)
                    right = min(origin["range"]["end_ms"], left+ms_at_frame(value["end_ms"]-value["start_ms"], fps))
                    return {"start_ms":left,"end_ms":right}
        raise ValueError("精确画面轨缺少源时间范围依据")

    for clip in [*graph["video_track"], *graph["narration_track"]]:
        left, right = clip["output_range"].values()
        clip["output_frames"] = {"start_frame":left,"end_frame":right}
        clip["output_range"] = {"start_ms":ms_at_frame(left, fps),"end_ms":ms_at_frame(right, fps)}
        clip["output_samples"] = {"start_sample":sample_at_frame(left, fps),"end_sample":sample_at_frame(right, fps)}
        if clip["source_kind"] != "tts":
            if clip["source_kind"] == "source":
                clip["source_normalized_frames"] = {"start_frame":clip["source_range"]["start_ms"],"end_frame":clip["source_range"]["end_ms"]}
                clip["source_samples"] = {"start_sample":sample_at_frame(clip["source_range"]["start_ms"],fps),
                    "end_sample":original_audio_samples if clip["source_range"]["end_ms"] == total_source else sample_at_frame(clip["source_range"]["end_ms"],fps),"sample_rate":AUDIO_RATE}
            clip["source_range"] = original_range(clip)
            continue
        voice = voiceovers[clip["source_asset_id"]]
        count, rate = voice.get("sample_count"), voice.get("sample_rate")
        clip["source_range"] = {"start_ms":0,"end_ms":voice["duration_ms"]}
        clip["source_samples"] = {"start_sample":0,"end_sample":count,"sample_rate":rate}
        from app.services.refinement_authorization import resolved_authorization
        current = next(item for item in items if item.item_id == clip["item_ids"][0])
        authorization = resolved_authorization(current, requirements, brief, expected, manual=manual, overrides=options.get("manual_permissions") if manual else None, options=options)
        tail = (options.get("display_tail_ms", 0) if rows[current.item_id]["type"] == "SEGMENT_INSERT"
                and authorization["scoped"] and authorization["allow_insert"] and authorization["allow_visual"] else 0)
        clip["padding_policy"] = {"version":"display-tail-1","max_display_ms":tail}
        if type(count) is not int or count <= 0 or rate != AUDIO_RATE:
            blockers.append({"item_id":clip["item_ids"][0],"code":"AUDIO_METADATA_REQUIRED","category":"audio","message":"配音缺少可信 PCM 样本元数据, 请重新生成或校验配音"})
        elif not precise_tail_allowed(sample_at_frame(right, fps)-sample_at_frame(left, fps)-count, fps, display_ms=tail):
            blockers.append({"item_id":clip["item_ids"][0],"code":"VOICEOVER_DURATION","category":"audio","message":"配音样本与授权画面帧预算不匹配, 禁止截断配音或无界留白"})
    for mapping in graph["source_to_output_map"]:
        left, right = mapping["output_range"].values()
        mapping["output_frames"] = {"start_frame":left,"end_frame":right}
        mapping["output_range"] = display_range(mapping["output_range"])
        mapping["source_normalized_frames"] = {"start_frame":mapping["source_range"]["start_ms"],"end_frame":mapping["source_range"]["end_ms"]}
        mapping["source_range"] = convert_range(mapping["source_range"], source_time)
    # Subtitle speech times stay on the sample clock, including original captions.
    graph["subtitle_track"] = []
    for clip in graph["video_track"]:
        if clip["source_kind"] != "material" or not any(rows[key]["type"] == "VISUAL_REPLACE" and rows[key]["subtitle_strategy"] == "regenerate" for key in clip["item_ids"]):
            continue
        left, right = clip["output_frames"].values()
        mapping = next((value for value in graph["source_to_output_map"] if value["kind"] == "retained" and value["output_frames"]["start_frame"] <= left < right <= value["output_frames"]["end_frame"]), None)
        if not mapping:
            continue
        start = mapping["source_normalized_frames"]["start_frame"]+left-mapping["output_frames"]["start_frame"]
        lower, upper = sample_at_frame(start, fps), sample_at_frame(start+right-left, fps)
        offset = sample_at_frame(left, fps)-lower
        for cue in snapshot.get("transcript", []):
            a, b = max(lower, cue["start_ms"]*48), min(upper, cue["end_ms"]*48)
            if a >= b:
                continue
            graph["subtitle_track"].append({"cue_id":f"{cue['cue_id']}-{left}","text":cue["text"],"source_kind":"source",
                "start_sample":a+offset,"end_sample":b+offset,"start_ms":round(Fraction((a+offset)*1000,AUDIO_RATE)),
                "end_ms":round(Fraction((b+offset)*1000,AUDIO_RATE))})
    for clip in graph["narration_track"]:
        if clip["source_kind"] != "tts" or rows[clip["item_ids"][0]]["subtitle_strategy"] != "regenerate":
            continue
        voice = voiceovers[clip["source_asset_id"]]
        count = voice.get("sample_count")
        for index, cue in enumerate(voice.get("timing", {}).get("segments", [])):
            a, b = round(Fraction(str(cue["start"]))*AUDIO_RATE), round(Fraction(str(cue["end"]))*AUDIO_RATE)
            if type(count) is not int or not 0 <= a < b <= count:
                blockers.append({"item_id":clip["item_ids"][0],"code":"SUBTITLE_ALIGNMENT","message":"配音字幕样本时间越界"})
                continue
            start = clip["output_samples"]["start_sample"]
            graph["subtitle_track"].append({"cue_id":f"tts-{voice['id']}-{index}","text":cue["text"],"start_sample":start+a,"end_sample":start+b,
                "start_ms":round(Fraction((start+a)*1000, AUDIO_RATE)),"end_ms":round(Fraction((start+b)*1000, AUDIO_RATE)),"source_kind":"tts","voiceover_id":voice["id"]})
    for row in graph["items"]:
        row["range"] = convert_range(row["range"], source_time)
        row["removed_ranges"] = [convert_range(value, source_time) for value in row["removed_ranges"]]
        row["duration_delta_ms"] = ms_at_frame(row["duration_delta_ms"], fps)
        row["material_segments"] = [{"asset_id":o["asset_id"],"source_range":o["range"], **({"candidate_id":o["candidate_id"]} if o.get("candidate_id") else {})} for o in origins.get(row["item_id"], [])]
        if row["material_segments"]:
            row["material_start_ms"] = row["material_segments"][0]["source_range"]["start_ms"]
        else:
            row["material_start_ms"] = ms_at_frame(row["material_start_ms"], fps)
    total = graph["summary"]["expected_duration_ms"]
    original_items = {item.item_id:item for item in items}
    alignment = [{"item_id":row["item_id"],"requested_range":original_items[row["item_id"]].range.model_dump(),
        "actual_range":row["range"],"error_ms":{"start":row["range"]["start_ms"]-original_items[row["item_id"]].range.start_ms,
        "end":row["range"]["end_ms"]-original_items[row["item_id"]].range.end_ms},
        "material_segments":deepcopy(row["material_segments"])} for row in graph["items"]]
    graph.update(schema_version="video_refinement_plan.v3", timing_version=PRECISE_TIMING_VERSION, source=deepcopy(source),
                 renderer_version="v3-render-1", frame_alignment=alignment, voiceover_assets=[deepcopy(voiceovers[v["id"]]) for v in graph["voiceover_assets"]])
    graph["summary"].update(total_frames=total, sample_count=sample_at_frame(total, fps), sample_rate=AUDIO_RATE, frame_rate=fps,
                            expected_duration_ms=ms_at_frame(total, fps), duration_delta_ms=ms_at_frame(total, fps)-original_duration)
    graph["snapshot_hash"] = content_hash(snapshot)
    graph["requirements_fingerprint"] = content_hash({"brief":brief,"expected":expected,"options":options,"timing":PRECISE_TIMING_VERSION})
    graph["requirements_resolution"] = verify_requirements(requirements, graph["items"], blockers)
    return graph
