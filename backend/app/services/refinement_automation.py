"""Prepare real materials and narration before publishing an automatic preview."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from time import monotonic

from app.config import get_settings
from app.models import VideoRefinementVoiceover
from app.schemas.video_refinement import MaterialSegment, RefinementItem
from app.services.llm import LLMClient
from app.services.refinement_plan import compile_plan
from app.services.refinement_timing import candidate_frame_bounds, frame_at_ms, ms_at_frame, required_frames, frames_for_samples


def safe_insertion_anchor(context: dict, requested: int) -> int:
    duration = context["source"]["duration_ms"]
    fps = context["source"].get("frame_rate", context["source"]["fps"])
    def align(point):
        return min(duration, max(0, ms_at_frame(frame_at_ms(point, fps), fps)))
    if not context["source"]["has_audio"]:
        return align(requested)
    if context.get("asr_state") not in {"completed","no_speech"}:
        return duration
    cues, words = context.get("transcript",[]), context.get("words",[])
    candidates = {0,duration,align(requested),*(align(c["end_ms"]+80) for c in cues),
                  *(align(c["start_ms"]-80) for c in cues)}
    safe = [point for point in candidates if 0 <= point <= duration and
            (point in {0,duration} or not any(c["start_ms"] < point < c["end_ms"] for c in cues) and
             not any(w["start_ms"]-60 < point < w["end_ms"]+60 for w in words))]
    return min(safe,key=lambda point:(abs(point-requested),point))


def plan_candidates(context: dict) -> list[dict]:
    records = context.get("material_evaluation",{}).get("records",[])
    return [{"asset_id":row["asset_id"],"segments":[segment for batch in row.get("batches",[]) for segment in batch["segments"]]}
            for row in sorted(records,key=lambda row:row.get("score",0),reverse=True) if row["status"]=="evaluated"]


async def prepare_materials(client, context, items, candidates, stats, *, lengths=None, frame_budgets=None, progress=None, options=None, checkpoint=None):
    from app.services.refinement_evaluation import sampled_frames
    catalog = {asset["asset_id"]:asset for asset in context["material_catalog"]}
    settings = get_settings()
    lengths = lengths or {}
    frame_budgets = frame_budgets or {}
    options = options or {}
    fps = context["source"].get("frame_rate") if options.get("timing_protocol") == "v3" else context["source"]["fps"]
    used = {}
    adjustments = []
    for item in items:
        if not item.enabled:
            continue
        if item.type == "SEGMENT_INSERT":
            anchor = safe_insertion_anchor(context,item.range.start_ms)
            item.range.start_ms = item.range.end_ms = anchor
            if item.narration_strategy == "tts":
                item.subtitle_strategy = "regenerate"
        if item.type not in {"VISUAL_REPLACE","SEGMENT_INSERT"} and item.edit_intent != "compress_expression":
            continue
        needed = lengths.get(item.item_id) or (item.range.end_ms-item.range.start_ms if item.type=="VISUAL_REPLACE" else
                 sum(s.source_range.end_ms-s.source_range.start_ms for s in item.material_segments))
        if needed <= 0:
            raise ValueError("自动方案缺少真实的素材选段, 请重试生成方案")
        proposed = [s.model_dump() for s in item.material_segments]
        original_proposals = deepcopy(proposed)
        proposed.extend({"asset_id":row["asset_id"],"source_range":{"start_ms":s["start_ms"],"end_ms":s["end_ms"]}}
                        for row in candidates for s in row["segments"])
        budget = frame_budgets.get(item.item_id)
        if budget is None:
            budget = (frame_at_ms(item.range.end_ms, fps) - frame_at_ms(item.range.start_ms, fps)
                      if item.type == "VISUAL_REPLACE" else required_frames(needed, fps))
        selected, seen, remaining = [], set(), budget
        if options.get("localization"):
            from app.services.refinement_selection import MAX_SEGMENTS, choose_window, locate_candidates
            localized = await locate_candidates(client, context, item,
                proposed if options.get("content_reselection") or not original_proposals else original_proposals,
                stats, settings.llm_vision_model, progress, checkpoint)
            from app.services.refinement_timing import frame_rate
            def capacity(candidate):
                bounds = candidate["safe_range"]
                count = (bounds["end_ms"]-bounds["start_ms"])*frame_rate(fps)/1000
                return count.numerator//count.denominator
            localized.sort(key=lambda candidate: (capacity(candidate) < budget, candidate["content_kind"] != "action"))
            for candidate in localized:
                if len(selected) >= MAX_SEGMENTS:
                    break
                take = min(remaining, capacity(candidate))
                window = choose_window(candidate, take, fps, used.get(candidate["asset_id"], []))
                if not window:
                    continue
                selected.append(MaterialSegment(asset_id=candidate["asset_id"], source_range=window, candidate_id=candidate["candidate_id"]))
                used.setdefault(candidate["asset_id"], []).append(tuple(window.values()))
                remaining -= take
                if remaining <= 0:
                    break
            if remaining > 0:
                raise ValueError("已核验局部范围不能在保留动作核心的前提下覆盖目标时长, 请补充更合适的无字幕商品素材")
            item.material_segments = selected
            item.material_asset_id = selected[0].asset_id
            item.material_start_ms = selected[0].source_range.start_ms
            item.material_audio_policy = "mute"
            item.audio_strategy = "keep_source"
            continue
        for segment in proposed:
            asset = catalog.get(segment["asset_id"])
            if not asset or asset.get("burned_subtitles"):
                continue
            a,b = segment["source_range"]["start_ms"],segment["source_range"]["end_ms"]
            if not 0 <= a < b <= asset["duration_ms"]:
                continue
            original_range = {"start_ms":a, "end_ms":b}
            start_frame, end_frame = candidate_frame_bounds(a, b, fps)
            take = min(remaining, end_frame - start_frame)
            a = ms_at_frame(start_frame, fps)
            b = ms_at_frame(start_frame + take, fps)
            if b <= a or any(a < right and left < b for left,right in used.get(asset["asset_id"],[])):
                continue
            key = (asset["asset_id"],a,b)
            if key in seen:
                continue
            seen.add(key)
            if progress:
                progress("fitting")
            frames = await asyncio.to_thread(sampled_frames,asset,[a,(a+b)//2,max(a,b-ms_at_frame(1, fps))])
            if progress:
                progress("fitting")
            verified = await client.chat_json([{"role":"user","content":[{"type":"text","text":
                "核对这段真实画面用于当前商品展示或台词是否合适. 必须没有烧录字幕或叠加文案, 包装上的印刷文字不算字幕. "
                "只返回 JSON {usable:布尔,burned_subtitles:布尔,reason:说明}. 输入时间为采样时间, 不表示整段已连续观看. "
                "目标: "+item.operation+" 台词: "+item.script},*frames]}],model=settings.llm_vision_model,temperature=.1,max_tokens=1000)
            stats["visual_verified"] = stats.get("visual_verified",0)+1
            if not isinstance(verified,dict):
                continue
            if verified.get("burned_subtitles") is True:
                asset["burned_subtitles"] = True
                continue
            if verified.get("usable") is not True or verified.get("burned_subtitles") is not False:
                continue
            asset["burned_subtitles"] = False
            selected.append(MaterialSegment.model_validate({"asset_id":asset["asset_id"],"source_range":{"start_ms":a,"end_ms":b}}))
            if 0 < end_frame - (start_frame + take) <= 1:
                adjustments.append({"item_id":item.item_id,"asset_id":asset["asset_id"],
                                    "code":"FRAME_BUDGET_CORRECTED","before_range":original_range,
                                    "after_range":{"start_ms":a,"end_ms":b},"removed_frames":1,
                                    "message":"已自动缩短最后一个商品展示片段一帧, 配音完整保留."})
            used.setdefault(asset["asset_id"],[]).append((a,b))
            remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            raise ValueError("没有匹配到足够长且无字幕的同商品画面, 请补充无字幕商品素材后刷新素材")
        item.material_segments = selected
        item.material_asset_id = selected[0].asset_id
        item.material_start_ms = selected[0].source_range.start_ms
        item.material_audio_policy = "mute"
        item.audio_strategy = "keep_source"
        item.evidence = list(dict.fromkeys([ref for ref in item.evidence if not ref.startswith("material-")]+[f"material-{s.asset_id}" for s in selected]))
    return adjustments


def prepare_narration(db, task, plan, progress, *, checkpoint=None):
    from app.services.refinement_voiceover import ensure_sample_metadata,run_voiceover,voiceover_catalog,voiceover_fingerprint,voiceover_parameters
    if any(block["code"] in {"EDIT_AUTHORIZATION_REQUIRED", "SOURCE_AUDIO_OFFSET", "SOURCE_AUDIO_DURATION"}
           or block["code"] == "AUDIO_METADATA_REQUIRED" and not block.get("item_id") for block in plan["execution_blockers"]):
        return plan
    started = monotonic()
    options = plan.get("automation_options", {})
    precise = options.get("timing_protocol") == "v3"
    metrics = {"tts_reused":0,"tts_generated":0}
    context = deepcopy(plan["analysis"]["context"])
    items = [RefinementItem.model_validate(row) for row in plan["items"]]
    parameters = voiceover_parameters()
    lengths = {}
    frame_budgets = {}
    voices = {}
    for item in items:
        if not item.enabled or item.narration_strategy != "tts":
            continue
        if not item.script.strip():
            raise ValueError("AI 没有生成补充段的完整台词, 请重试生成方案")
        progress("voiceover")
        fingerprint = voiceover_fingerprint(item.script,parameters)
        voice = db.query(VideoRefinementVoiceover).filter_by(tenant_id=task.tenant_id,task_id=task.id,fingerprint=fingerprint).first()
        if not voice:
            voice = VideoRefinementVoiceover(tenant_id=task.tenant_id,task_id=task.id,revision=task.current_revision,
                     item_id=item.item_id,script=item.script.strip(),parameters_json=parameters,fingerprint=fingerprint,status="queued_v3")
            db.add(voice);db.commit()
        elif voice.status in {"failed", "superseded"}:
            voice.status="queued_v3";voice.error_message="";voice.completed_at=None;voice.revision=task.current_revision;db.commit()
        elif voice.status in {"queued", "queued_v3"}:
            db.query(VideoRefinementVoiceover).filter_by(id=voice.id,status=voice.status).update({"revision":task.current_revision,"status":"queued_v3"},synchronize_session=False)
            db.commit();db.refresh(voice)
        if voice.status == "ready":
            metrics["tts_reused"] += 1
        else:
            metrics["tts_generated"] += 1
        voice_id = voice.id
        db.commit()
        run_voiceover(voice_id)
        db.expire_all()
        voice = db.get(VideoRefinementVoiceover,voice_id)
        progress("voiceover")
        if voice.status != "ready":
            raise ValueError(voice.error_message or "局部配音尚未准备完成, 请稍后重试")
        item.voiceover_id = voice.id
        if precise:
            ensure_sample_metadata(db, voice)
            voices[item.item_id] = voice
        if item.type == "SEGMENT_INSERT" or item.edit_intent == "compress_expression":
            fps = context["source"]["frame_rate"] if precise else context["source"]["fps"]
            frames = frames_for_samples(voice.timing_json["audio"]["sample_count"], fps) if precise else required_frames(voice.duration_ms, fps)
            length = ms_at_frame(frames, fps)
            if item.type == "SEGMENT_INSERT":
                item.duration_delta_ms = length
            lengths[item.item_id] = length
            frame_budgets[item.item_id] = frames
    if precise:
        offset = 0
        by_id = {item.item_id:item for item in items}
        fps = context["source"]["frame_rate"]
        for mapping in plan["source_to_output_map"]:
            if mapping["kind"] not in {"inserted", "rewritten"}:
                continue
            item = by_id[mapping["item_ids"][0]]
            voice = voices.get(item.item_id)
            if not voice:
                continue
            bounds = mapping["output_frames"]
            frames = frames_for_samples(voice.timing_json["audio"]["sample_count"], fps, start_frame=bounds["start_frame"]+offset)
            offset += frames-(bounds["end_frame"]-bounds["start_frame"])
            frame_budgets[item.item_id] = frames
            lengths[item.item_id] = ms_at_frame(frames, fps)
            if item.type == "SEGMENT_INSERT":
                item.duration_delta_ms = lengths[item.item_id]
    progress("fitting")
    adjustments = asyncio.run(prepare_materials(LLMClient(),context,items,plan_candidates(context),context["material_evaluation"],
                                              lengths=lengths,frame_budgets=frame_budgets, progress=progress, options=options, checkpoint=checkpoint))
    result = compile_plan(context,items,task.user_brief,task.expected_result,manual=False,
                          requirements=plan.get("requirements_resolution",[]),options=plan.get("automation_options",{}),
                          voiceovers=voiceover_catalog(db,task))
    result["analysis"] = {**plan["analysis"],"context":context}
    result["findings_not_applied"] = plan["findings_not_applied"]
    result["automatic_preview"] = True
    result["automatic_adjustments"] = adjustments
    result["metrics"] = {**plan.get("metrics", {}),**metrics,"preparation_seconds":round(monotonic()-started,3),"automatic_adjustments":len(adjustments),
                         "used_segments":sum(len(item.material_segments) for item in items if item.enabled),
                         "locator_calls":context["material_evaluation"].get("locator_calls",0),
                         "locator_cache_hits":context["material_evaluation"].get("locator_cache_hits",0),
                         "blocker_categories":[b.get("category", "technical") for b in result["execution_blockers"]]}
    result["material_evaluation"]["partial_accepted"] = bool(plan.get("automation_options",{}).get("accept_partial_evaluation"))
    result["execution_blockers"].extend(deepcopy(block) for block in plan["execution_blockers"] if block["code"]=="EVALUATION_INCOMPLETE")
    return result
