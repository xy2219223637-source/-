"""Derive per-edit permissions from bounded quotes, never from model flags."""
from __future__ import annotations

import re


AUTHORIZATION_VERSION = "refinement-authorization-v2-goal-constraints"
_SEPARATOR = r"[,，;；。.!！？?\n]|并且|同时|然后|并(?=新增|增加|补充|修改|替换)"
_VOICE = r"(?:台词|配音|口播|讲解)"
_ACTION = r"(?:新增|增加|补充|改写|修改|改动|改变|更改|变更|调整|替换|精简|缩短|改短|重写|重新生成|生成|重配|重新配|重录|改)"
_NEGATIVE = r"(?:不要|不需要|无需|禁止|不允许|不得|不能|不再|不)"
_CUT = r"(?:删除|删减|裁切|缩短|精简|压缩|剪掉|剪短|删掉|去掉)"
_CUT_GOAL = r"(?:优化|改善|提升|加强|加快).{0,6}(?:节奏|开头)|(?:节奏|开头).{0,6}(?:紧凑|更快|拖沓)|(?:去除|移除|减少).{0,8}(?:冗余|重复|铺垫|停顿)"
_VISUAL_GOAL = r"(?:突出|强化|加强).{0,6}(?:产品|商品|卖点)|(?:优化|改善|提升).{0,6}(?:观感|开头)"


def request_clauses(text: str) -> list[str]:
    return [part.strip() for part in re.split(_SEPARATOR, text) if part.strip()]


def permissions(text: str) -> dict:
    denied_voice = bool(re.search(_NEGATIVE + r".{0,4}" + _ACTION + r".{0,8}" + _VOICE, text) or
                        re.search(_NEGATIVE + r"\s*(?:新的?|任何)?" + _VOICE, text) or
                        re.search(_VOICE + r".{0,6}" + _NEGATIVE + r".{0,6}" + _ACTION, text) or
                        re.search(_NEGATIVE + r".{0,4}(?:重新配音|重配音|重录口播)", text))
    voice = not denied_voice and bool(re.search(_ACTION + r".{0,8}" + _VOICE, text) or
                                     re.search(_VOICE + r".{0,8}" + _ACTION, text) or
                                     re.search(r"重新配音|重配音|重录口播", text))
    insert = bool(re.search(r"插入|延长|增加时长|新增内容|新段落", text)) and not bool(
        re.search(_NEGATIVE + r".{0,6}(?:插入|延长|增加时长|新增内容|新段落)", text))
    new_voice = voice and bool(re.search(r"(?:新增|增加|补充).{0,8}" + _VOICE, text))
    denied_cut = bool(re.search(_NEGATIVE + r".{0,6}" + _CUT, text))
    denied_visual = bool(re.search(_NEGATIVE + r".{0,4}(?:替换|修改|改动|更改|改变|调整).{0,6}(?:画面|镜头)", text) or
                         re.search(r"(?:画面|镜头).{0,6}" + _NEGATIVE + r".{0,4}(?:替换|修改|改动|更改|改变|调整)", text))
    return {"allow_visual":not denied_visual and bool(re.search(r"画面|镜头|展示|演示|特写|" + _VISUAL_GOAL, text)),
            "allow_narration":voice, "allow_insert":insert or new_voice,
            "allow_rewrite":voice and not new_voice,
            "allow_cut":not denied_cut and bool(re.search(_CUT + r"|" + _CUT_GOAL, text)),
            "deny_narration":denied_voice, "deny_cut":denied_cut, "deny_visual":denied_visual}


def preserves_all_narration(text: str) -> bool:
    if re.search(_NEGATIVE + r".{0,6}保留", text):
        return False
    return bool(re.search(r"(?:保留|保持).{0,6}(?:完整|全部|所有).{0,6}" + _VOICE +
                          r"|(?:完整|全部|所有).{0,6}(?:保留|保持).{0,6}" + _VOICE +
                          r"|" + _VOICE + r".{0,8}(?:全部保留|完整保留|不删减|不删除|不变|一字不删)", text))


def constraints(text: str) -> dict:
    value = permissions(text)
    return {"deny_narration":value["deny_narration"], "deny_cut":value["deny_cut"], "deny_visual":value["deny_visual"],
            "keep_source_audio":bool(re.search(r"(?:沿用|保留|保持|使用).{0,8}(?:原有|原始|原).{0,6}(?:音频|原声|配音|声音|口播)", text)) and not bool(re.search(_NEGATIVE + r"\s*(?:再|继续)?(?:保留|保持|沿用|使用)", text)),
            "keep_all_narration":preserves_all_narration(text),
            "keep_duration":bool(re.search(r"(?:保持|保留|沿用).{0,6}(?:原|当前|总).{0,3}时长|保持(?:视频|成片)?时长|不改变.{0,4}(?:总)?时长|时长.{0,4}不变", text)),
            "no_extension":bool(re.search(_NEGATIVE + r".{0,4}(?:增加|延长).{0,4}(?:时长|视频|成片)|(?:时长|视频|成片).{0,4}" + _NEGATIVE + r".{0,4}(?:增加|延长)", text)),
            "keep_frame":bool(re.search(r"(?:保持|保留|沿用).{0,8}(?:画幅|画面比例|分辨率)", text))}


def is_constraint(text: str) -> bool:
    value = constraints(text)
    if not any(value.values()):
        return False
    action = permissions(text)
    return not any(action[key] for key in ("allow_narration", "allow_insert", "allow_rewrite", "allow_cut"))


def constraint_satisfied(text: str, items: list[dict]) -> bool:
    value = constraints(text)
    enabled = [item for item in items if item["enabled"]]
    voice = any(item["narration_strategy"] == "tts" or item["type"] == "VOICE_REPLACE"
                or item.get("edit_intent") == "compress_expression" for item in enabled)
    cuts = [item for item in enabled if item["type"] == "PACE_RECUT" and
            (item.get("removed_ranges") or item.get("duration_delta_ms", 0) < 0 or item.get("edit_intent") == "compress_expression")]
    return not ((value["deny_narration"] or value["keep_source_audio"]) and voice
                or value["deny_cut"] and cuts
                or value["deny_visual"] and any(item["type"] in {"VISUAL_REPLACE", "SEGMENT_INSERT"} or item.get("material_segments") for item in enabled)
                or value["keep_all_narration"] and (voice or any(item.get("edit_intent") != "remove_pause" for item in cuts))
                or value["keep_duration"] and sum(item.get("duration_delta_ms", 0) for item in enabled) != 0
                or value["no_extension"] and sum(item.get("duration_delta_ms", 0) for item in enabled) > 0)


def active_clauses(brief: str | None, expected: str, requirements: list[dict]) -> list[str]:
    clauses = request_clauses((brief or "") + ";" + expected) if brief is not None else [row.get("request_quote") or row["text"] for row in requirements]
    result = []
    answered = set()
    for part in clauses:
        row = next((row for row in requirements if (row.get("request_quote") or row["text"]) == part), None)
        if row and row.get("decision") == "waived":
            continue
        if row and row.get("decision") == "answered" and row.get("answer", "").strip():
            if not is_constraint(part):
                result.append(part)
            result.extend(request_clauses(row["answer"]))
            answered.add(row["requirement_id"])
        else:
            result.append(part)
    # Older plans may contain normalized quotes. Their user answers still constrain edits.
    for row in requirements:
        if row.get("decision") == "answered" and row["requirement_id"] not in answered:
            result.extend(part for part in request_clauses(row.get("answer", "")) if any(constraints(part).values()))
    return result


def _restrict(authorization: dict, item, clauses: list[str]) -> dict:
    if any(permissions(part)["deny_narration"] for part in clauses):
        authorization["allow_narration"] = authorization["allow_rewrite"] = False
    if any(permissions(part)["deny_cut"] for part in clauses):
        authorization["allow_cut"] = False
    if any(permissions(part)["deny_visual"] for part in clauses):
        authorization["allow_visual"] = False
    manual = any(value["source"] in {"manual_draft", "manual_fields"} for value in authorization["evidence"])
    if not manual and any(constraints(part)["keep_source_audio"] for part in clauses):
        authorization["allow_narration"] = authorization["allow_rewrite"] = False
    if item.type == "PACE_RECUT" and item.edit_intent != "remove_pause" and any(preserves_all_narration(part) for part in clauses):
        authorization["allow_cut"] = False
    return authorization


def item_authorization(item, requirements, brief: str | None, expected: str = "", *, manual=False, options=None) -> dict:
    keys = ("allow_visual", "allow_narration", "allow_insert", "allow_rewrite", "allow_cut")
    result = {"version":AUTHORIZATION_VERSION, **dict.fromkeys(keys, False), "evidence":[], "scoped":False}
    clauses = active_clauses(brief, expected, requirements)
    if manual:
        return _restrict({**result, **dict.fromkeys(keys, True), "scoped":True,
                          "evidence":[{"source":"manual_draft","quote":"用户显式保存的编辑项"}]}, item, clauses)
    linked = [row for row in requirements if row["requirement_id"] in item.requirement_ids and row.get("decision") != "waived"]
    for row in linked:
        quote = row.get("request_quote") or row["text"]
        matches = [part for part in clauses if quote == part or (quote in part and len(quote) >= 4)]
        if brief is None:
            matches = [quote]
        # A whole multi-clause brief is not an atomic authorization span.
        if len(matches) == 1 and len(request_clauses(quote)) == 1:
            result["evidence"].append({"source":"request","quote":matches[0],"requirement_id":row["requirement_id"]})
        if row.get("decision") == "answered" and row.get("answer", "").strip():
            result["evidence"].append({"source":"answer","quote":row["answer"],"requirement_id":row["requirement_id"]})
    actionable = [part for part in clauses if any(permissions(part)[key] for key in keys)]
    if not result["evidence"] and len(actionable) == 1:
        result["evidence"].append({"source":"request","quote":actionable[0]})
    if (options or {}).get("duration_preference") == "shorten":
        result["evidence"].append({"source":"duration_option","quote":"缩短成片"})
    sources = []
    for evidence in result["evidence"]:
        for part in request_clauses(evidence["quote"]):
            sources.append(permissions(part))
    result["scoped"] = bool(result["evidence"])
    primary = {"VISUAL_REPLACE":"allow_visual", "SEGMENT_INSERT":"allow_insert",
               "VOICE_REPLACE":"allow_rewrite", "PACE_RECUT":"allow_cut"}.get(item.type)
    needed = [primary] if primary else []
    if item.type == "VOICE_REPLACE" or item.edit_intent == "compress_expression" or item.narration_strategy == "tts" and item.type != "VISUAL_REPLACE":
        needed.append("allow_narration")
    # One atomic action must authorize the edit. Additional goals and constraints
    # may support it, but cannot combine unrelated clauses into a new TTS scope.
    selected = next((value for value in sources if needed and all(value[key] for key in needed)), None)
    if selected is None:
        selected = next((value for value in sources if primary and value[primary]), None)
    if selected is None and item.type in {"SEGMENT_INSERT", "VISUAL_REPLACE"}:
        selected = next((value for value in sources if value["allow_visual"]), None)
    if selected:
        result.update({key:selected[key] for key in keys})
    return _restrict(result, item, clauses)


def manual_permissions(items, previous):
    from app.schemas.video_refinement import RefinementItem
    old = {row["item_id"]:RefinementItem.model_validate(row).model_dump() for row in (previous or {}).get("items", [])}
    result = {}
    for item in items:
        value = item.model_dump()
        prior = old.get(item.item_id)
        prior_authorization = (previous or {}).get("edit_authorizations", {}).get(item.item_id, {})
        record = {key:bool(prior_authorization.get(key)) for key in ("allow_visual", "allow_narration", "allow_insert", "allow_rewrite", "allow_cut")}
        record["evidence"] = [dict(evidence) for evidence in prior_authorization.get("evidence", []) if evidence.get("source") in {"manual_draft", "manual_fields"}]
        if not record["evidence"]:
            record.update(dict.fromkeys(("allow_visual", "allow_narration", "allow_insert", "allow_rewrite", "allow_cut"), False))
        changed = set(value) if prior is None else {key for key in value if value[key] != prior[key]}
        visual = bool(changed & {"type", "range", "material_segments", "material_asset_id", "material_start_ms"})
        voice = bool(changed & {"type", "script", "narration_strategy", "voiceover_id"})
        timing = bool(changed & {"type", "range", "duration_delta_ms", "removed_ranges", "edit_intent"})
        record["allow_visual"] |= visual
        record["allow_narration"] |= voice
        record["allow_rewrite"] |= voice
        record["allow_insert"] |= timing and item.type == "SEGMENT_INSERT"
        record["allow_cut"] |= timing and item.type == "PACE_RECUT"
        explicit = changed & {"type", "range", "material_segments", "material_asset_id", "material_start_ms", "script", "narration_strategy", "voiceover_id", "duration_delta_ms", "removed_ranges", "edit_intent"}
        if explicit:
            record["evidence"].append({"source":"manual_fields","quote":",".join(sorted(explicit))})
        result[item.item_id] = record
    return result


def resolved_authorization(item, requirements, brief, expected="", *, manual=False, overrides=None, options=None):
    authorization = item_authorization(item, requirements, brief, expected, manual=manual and overrides is None, options=options)
    override = (overrides or {}).get(item.item_id, {})
    if override.get("evidence"):
        for key in ("allow_visual", "allow_narration", "allow_insert", "allow_rewrite", "allow_cut"):
            authorization[key] |= bool(override.get(key))
        authorization["evidence"].extend(override["evidence"])
        authorization["scoped"] = True
    return _restrict(authorization, item, active_clauses(brief, expected, requirements))


def apply_authorizations(items, requirements, duration_ms, *, brief=None, expected="", manual=False, overrides=None, options=None) -> tuple[dict, list[dict]]:
    records, blockers = {}, []
    for item in items:
        authorization = resolved_authorization(item, requirements, brief, expected, manual=manual, overrides=overrides, options=options)
        records[item.item_id] = authorization
        if not item.enabled:
            continue
        visual_only = authorization["allow_visual"] and not authorization["allow_narration"] and not authorization["allow_insert"]
        if visual_only and item.type in {"SEGMENT_INSERT", "VISUAL_REPLACE"}:
            if item.type == "SEGMENT_INSERT":
                length = sum(s.source_range.end_ms - s.source_range.start_ms for s in item.material_segments) or item.duration_delta_ms
                if length <= 0:
                    blockers.append({"item_id":item.item_id,"code":"MATERIAL_REQUIRED","message":"纯画面修改缺少可用选段范围"})
                    continue
                length = min(length, duration_ms)
                item.range.start_ms = min(item.range.start_ms, duration_ms - length)
                item.range.end_ms = item.range.start_ms + length
            item.type = "VISUAL_REPLACE"
            item.edit_intent = "replace_visual"
            item.narration_strategy = "keep_source"
            item.duration_delta_ms = 0
            item.script = ""
            item.voiceover_id = None
            item.subtitle_strategy = "keep_visible"
            item.background_strategy = "keep_mixed"
        elif item.type == "SEGMENT_INSERT" and authorization["allow_insert"] and not authorization["allow_narration"]:
            item.narration_strategy = "none"
            item.script = ""
            item.voiceover_id = None
            item.subtitle_strategy = "none"
            item.narration_padding_reason = "用户明确插入展示画面, 未要求新增口播"
        needs_voice = item.narration_strategy == "tts" or item.type == "VOICE_REPLACE" or item.edit_intent == "compress_expression"
        unauthorized = (not authorization["scoped"] or
                        item.type == "VISUAL_REPLACE" and not authorization["allow_visual"] or
                        needs_voice and not authorization["allow_narration"] or
                        item.type == "SEGMENT_INSERT" and not authorization["allow_insert"] or
                        item.type == "VOICE_REPLACE" and not authorization["allow_rewrite"] or
                        item.type == "PACE_RECUT" and not authorization["allow_cut"])
        if unauthorized:
            blockers.append({"item_id":item.item_id,"code":"EDIT_AUTHORIZATION_REQUIRED","category":"technical",
                             "message":"AI 方案包含超出目标或违反明确限制的操作, 需要重新规划"})
    return records, blockers
