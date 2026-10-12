"""Prepare real materials and narration before publishing an automatic preview."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import math

from app.config import get_settings
from app.models import VideoRefinementVoiceover
from app.schemas.video_refinement import MaterialSegment, RefinementItem
from app.services.llm import LLMClient
from app.services.refinement_plan import compile_plan


def safe_insertion_anchor(context: dict, requested: int) -> int:
    duration = context["source"]["duration_ms"]
    fps = context["source"]["fps"]
    def align(point):
        return min(duration, max(0, round(round(point * fps / 1000) * 1000 / fps)))
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


async def prepare_materials(client, context, items, candidates, stats, *, lengths=None):
    from app.services.refinement_evaluation import sampled_frames
    catalog = {asset["asset_id"]:asset for asset in context["material_catalog"]}
    settings = get_settings()
    lengths = lengths or {}
    fps = context["source"]["fps"]
    used = {}
    for item in items:
        if not item.enabled:
            continue
        if item.type == "SEGMENT_INSERT":
            anchor = safe_insertion_anchor(context,item.range.start_ms)
            item.range.start_ms = item.range.end_ms = anchor
            item.narration_strategy = "tts"
            item.subtitle_strategy = "regenerate"
        if item.type not in {"VISUAL_REPLACE","SEGMENT_INSERT"} and item.edit_intent != "compress_expression":
            continue
        needed = lengths.get(item.item_id) or (item.range.end_ms-item.range.start_ms if item.type=="VISUAL_REPLACE" else
                 sum(s.source_range.end_ms-s.source_range.start_ms for s in item.material_segments))
        if needed <= 0:
            raise ValueError("自动方案缺少真实的素材选段, 请重试生成方案")
        proposed = [s.model_dump() for s in item.material_segments]
        proposed.extend({"asset_id":row["asset_id"],"source_range":{"start_ms":s["start_ms"],"end_ms":s["end_ms"]}}
                        for row in candidates for s in row["segments"])
        selected, seen, remaining = [], set(), needed
        for segment in proposed:
            asset = catalog.get(segment["asset_id"])
            if not asset or asset.get("burned_subtitles"):
                continue
            a,b = segment["source_range"]["start_ms"],segment["source_range"]["end_ms"]
            if not 0 <= a < b <= asset["duration_ms"]:
                continue
            # Use whole frames and enough real footage to play narration in full.
            rounding = fps / 2000
            a = round(math.ceil(a * fps / 1000 - rounding) * 1000 / fps)
            b = min(round(math.floor(b * fps / 1000 + rounding) * 1000 / fps),
                    round(math.ceil((a+remaining) * fps / 1000 - rounding) * 1000 / fps))
            if b <= a or any(a < right and left < b for left,right in used.get(asset["asset_id"],[])):
                continue
            key = (asset["asset_id"],a,b)
            if key in seen:
                continue
            seen.add(key)
            frames = await asyncio.to_thread(sampled_frames,asset,[a,(a+b)//2,max(a,b-round(1000/fps))])
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
            used.setdefault(asset["asset_id"],[]).append((a,b))
            remaining -= b-a
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


def prepare_narration(db, task, plan, progress):
    from app.services.refinement_voiceover import run_voiceover,voiceover_catalog,voiceover_fingerprint,voiceover_parameters
    context = deepcopy(plan["analysis"]["context"])
    items = [RefinementItem.model_validate(row) for row in plan["items"]]
    parameters = voiceover_parameters()
    lengths = {}
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
                     item_id=item.item_id,script=item.script.strip(),parameters_json=parameters,fingerprint=fingerprint)
            db.add(voice);db.commit()
        elif voice.status == "failed":
            voice.status="queued";voice.error_message="";voice.completed_at=None;db.commit()
        voice_id = voice.id
        db.commit()
        run_voiceover(voice_id)
        db.expire_all()
        voice = db.get(VideoRefinementVoiceover,voice_id)
        progress("voiceover")
        if voice.status != "ready":
            raise ValueError(voice.error_message or "局部配音尚未准备完成, 请稍后重试")
        item.voiceover_id = voice.id
        if item.type == "SEGMENT_INSERT" or item.edit_intent == "compress_expression":
            fps = context["source"]["fps"]
            length = round(math.ceil(voice.duration_ms*fps/1000)*1000/fps)
            if item.type == "SEGMENT_INSERT":
                item.duration_delta_ms = length
            lengths[item.item_id] = length
    progress("fitting")
    asyncio.run(prepare_materials(LLMClient(),context,items,plan_candidates(context),context["material_evaluation"],lengths=lengths))
    result = compile_plan(context,items,task.user_brief,task.expected_result,manual=False,
                          requirements=plan.get("requirements_resolution",[]),options=plan.get("automation_options",{}),
                          voiceovers=voiceover_catalog(db,task))
    result["analysis"] = {**plan["analysis"],"context":context}
    result["findings_not_applied"] = plan["findings_not_applied"]
    result["automatic_preview"] = True
    result["material_evaluation"]["partial_accepted"] = bool(plan.get("automation_options",{}).get("accept_partial_evaluation"))
    result["execution_blockers"].extend(deepcopy(block) for block in plan["execution_blockers"] if block["code"]=="EVALUATION_INCOMPLETE")
    return result
