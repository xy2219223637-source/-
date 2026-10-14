import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.refinement_authorization import (
    AUTHORIZATION_VERSION,
    active_clauses,
    apply_authorizations,
    constraints,
    item_authorization,
    permissions,
)
from app.services.refinement_plan import compile_plan
from app.services.refinement_requirements import parse_requirements, verify_requirements
from test_refinement_v2 import item, snapshot, voice


def requirements_for(*texts):
    return [
        {
            "requirement_id": f"req-{index}",
            "text": text,
            "request_quote": text,
            "status": "EXECUTABLE",
            "reason": "落实用户目标或约束",
            "question": "",
            "decision": "pending",
            "item_ids": [],
        }
        for index, text in enumerate(texts)
    ]


def recut(requirements, **extra):
    return item(
        "PACE_RECUT",
        range={"start_ms": 0, "end_ms": 30000},
        material_segments=[],
        removed_ranges=[{"start_ms": 0, "end_ms": 10000}],
        edit_intent="remove_content",
        narration_strategy="cut_source",
        requirement_ids=[row["requirement_id"] for row in requirements],
        **extra,
    )


class GoalAutomationAuthorizationTest(unittest.TestCase):
    def test_duration_goal_and_audio_constraints_jointly_authorize_recut(self):
        requirements = requirements_for(
            "将视频缩短到 20 秒",
            "优先删减开头较长的铺垫和重复镜头",
            "沿用原有音频",
            "不生成新的配音",
        )
        brief = "; ".join(row["text"] for row in requirements)
        edit = recut(requirements)
        authorization = item_authorization(edit, requirements, brief)
        self.assertTrue(authorization["scoped"])
        self.assertTrue(authorization["allow_cut"])
        self.assertFalse(authorization["allow_narration"])
        records, blockers = apply_authorizations([edit], requirements, 30000, brief=brief)
        self.assertFalse(blockers)
        self.assertTrue(records[edit.item_id]["allow_cut"])
        context = snapshot()
        context["source"]["duration_ms"] = 30000
        plan = compile_plan(
            context, [edit], brief, "", manual=False, requirements=requirements,
            options={"authorization_version": AUTHORIZATION_VERSION, "target_duration_ms": 20000},
        )
        self.assertFalse(plan["execution_blockers"])
        self.assertEqual(plan["summary"]["expected_duration_ms"], 20000)
        self.assertTrue(all(row["status"] in {"EXECUTABLE", "ALREADY_SATISFIED"}
                            for row in plan["requirements_resolution"]))

    def test_delete_synonym_authorizes_a_cut(self):
        requirements = requirements_for("删减重复镜头")
        record = item_authorization(recut(requirements), requirements, requirements[0]["text"])
        self.assertTrue(record["scoped"])
        self.assertTrue(record["allow_cut"])

    def test_explicit_cut_prohibition_overrides_goal_and_answer(self):
        for denial in ("禁止裁切", "不要删减内容", "不能删除镜头"):
            with self.subTest(denial=denial):
                requirements = requirements_for("将视频缩短到 20 秒", denial)
                requirements[0].update(decision="answered", answer="允许裁切并删减镜头")
                brief = "; ".join(row["text"] for row in requirements)
                edit = recut(requirements)
                record = item_authorization(edit, requirements, brief)
                self.assertFalse(record["allow_cut"])
                _, blockers = apply_authorizations([edit], requirements, 30000, brief=brief)
                self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in blockers])

    def test_explicit_voice_prohibition_overrides_answer(self):
        requirements = requirements_for("修改台词", "不生成新的配音")
        requirements[0].update(decision="answered", answer="允许改写台词并重新配音")
        edit = item("VOICE_REPLACE", narration_strategy="tts", script="修改后的台词",
                    requirement_ids=[requirements[0]["requirement_id"]])
        brief = "; ".join(row["text"] for row in requirements)
        record = item_authorization(edit, requirements, brief)
        self.assertFalse(record["allow_narration"])
        _, blockers = apply_authorizations([edit], requirements, 30000, brief=brief)
        self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in blockers])

    def test_unrelated_voice_requirement_cannot_authorize_visual_item_tts(self):
        requirements = requirements_for("替换第一句口播", "增加商品展示镜头")
        requirements[0].update(decision="answered", answer="允许生成新的配音")
        edit = item("VISUAL_REPLACE", narration_strategy="tts", script="不应生成的台词",
                    requirement_ids=[requirements[1]["requirement_id"]])
        brief = "; ".join(row["text"] for row in requirements)
        record = item_authorization(edit, requirements, brief)
        self.assertFalse(record["allow_narration"])
        _, blockers = apply_authorizations([edit], requirements, 30000, brief=brief)
        self.assertTrue(edit.narration_strategy != "tts" or blockers)

    def test_linking_both_requirements_cannot_union_insert_and_voice_permissions(self):
        requirements = requirements_for("增加商品展示镜头", "替换第一句口播")
        edit = item(
            "SEGMENT_INSERT", range={"start_ms": 2000, "end_ms": 2000},
            narration_strategy="tts", script="擅自增加的口播",
            requirement_ids=[row["requirement_id"] for row in requirements],
        )
        brief = "; ".join(row["text"] for row in requirements)
        record = item_authorization(edit, requirements, brief)
        self.assertFalse(record["allow_insert"] and record["allow_narration"])
        _, blockers = apply_authorizations([edit], requirements, 30000, brief=brief)
        self.assertTrue(edit.type != "SEGMENT_INSERT" or edit.narration_strategy != "tts" or blockers)

    def test_structured_shorten_option_only_authorizes_source_cut(self):
        requirements = requirements_for("节奏更利落")
        options = {"duration_preference": "shorten"}
        cut = recut(requirements)
        record = item_authorization(cut, requirements, "节奏更利落", options=options)
        self.assertTrue(record["allow_cut"])
        self.assertFalse(record["allow_narration"])
        _, blockers = apply_authorizations([cut], requirements, 30000, brief="节奏更利落", options=options)
        self.assertFalse(blockers)
        speech = item("VOICE_REPLACE", narration_strategy="tts", script="擅自改写",
                      requirement_ids=[requirements[0]["requirement_id"]])
        _, blockers = apply_authorizations([speech], requirements, 30000, brief="节奏更利落", options=options)
        self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in blockers])


class GoalAutomationRequirementsTest(unittest.TestCase):
    def parse(self, *texts):
        requirements = requirements_for(*texts)
        context = snapshot()
        context["source"]["duration_ms"] = 30000
        context["transcript"] = [
            {"cue_id": "speech", "start_ms": 0, "end_ms": 25000, "text": "完整原口播"}
        ]
        client = MagicMock(chat_json=AsyncMock(return_value={"requirements": requirements}))
        return asyncio.run(parse_requirements(
            client, context, "; ".join(texts), "", {"target_duration_ms": 20000},
        ))

    def test_source_audio_preservation_is_not_full_transcript_preservation(self):
        for preservation in ("沿用原有音频", "保留原配音", "不生成新的配音"):
            with self.subTest(preservation=preservation):
                resolved = self.parse("将视频缩短到 20 秒", preservation)
                self.assertNotIn("CONFLICT", [row["status"] for row in resolved])

    def test_preserving_all_spoken_content_still_conflicts_with_short_target(self):
        for preservation in ("完整保留全部台词", "保留完整口播"):
            with self.subTest(preservation=preservation):
                resolved = self.parse("将视频缩短到 20 秒", preservation)
                self.assertIn("CONFLICT", [row["status"] for row in resolved])

    def test_duration_conflict_only_links_involved_goal_and_preservation(self):
        resolved = self.parse(
            "将视频缩短到 20 秒", "完整保留全部台词", "不生成新的配音", "保持原有画幅",
        )
        involved = {row["requirement_id"] for row in resolved[:2]}
        for row in resolved[:2]:
            self.assertEqual(row["status"], "CONFLICT")
            self.assertEqual(set(row["conflict_ids"]), involved - {row["requirement_id"]})
        for row in resolved[2:]:
            self.assertNotEqual(row["status"], "CONFLICT")
            self.assertFalse(row.get("conflict_ids"))

    def test_internal_authorization_failure_does_not_become_four_user_questions(self):
        requirements = requirements_for(
            "将视频缩短到 20 秒", "优先删减开头较长的铺垫和重复镜头",
            "沿用原有音频", "不生成新的配音",
        )
        edit = recut(requirements)
        blockers = [{"item_id": edit.item_id, "code": "EDIT_AUTHORIZATION_REQUIRED",
                     "message": "规划产生了未经授权的操作"}]
        resolved = verify_requirements(requirements, [edit.model_dump()], blockers)
        self.assertNotIn("NEEDS_CLARIFICATION", [row["status"] for row in resolved])
        self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in blockers])

    def test_missing_goal_edit_is_internal_planning_failure_not_user_question(self):
        requirements = requirements_for("将视频缩短到 20 秒")
        blockers = []
        resolved = verify_requirements(requirements, [], blockers)
        self.assertNotEqual(resolved[0]["status"], "NEEDS_CLARIFICATION")
        self.assertTrue(blockers)

    def test_explicit_constraints_without_edits_do_not_need_how_to_execute_answer(self):
        requirements = requirements_for("沿用原有音频", "不生成新的配音", "保持原有画幅")
        blockers = []
        resolved = verify_requirements(requirements, [], blockers)
        self.assertTrue(all(row["status"] == "ALREADY_SATISFIED" for row in resolved))
        self.assertFalse(blockers)
        repeated = verify_requirements(deepcopy(resolved), [], blockers)
        self.assertEqual(resolved, repeated)
        self.assertFalse(blockers)

    def test_unrequested_internal_edit_keeps_render_blocker_without_user_question(self):
        requirements = requirements_for("沿用原有音频", "不生成新的配音")
        edit = item("PACE_RECUT", material_segments=[], requirement_ids=[],
                    removed_ranges=[{"start_ms": 1000, "end_ms": 1500}])
        plan = compile_plan(
            snapshot(), [edit], "沿用原有音频; 不生成新的配音", "", manual=False,
            requirements=requirements, options={"authorization_version": AUTHORIZATION_VERSION},
        )
        self.assertTrue(plan["execution_blockers"])
        self.assertIn("UNREQUESTED_EDIT", [row["code"] for row in plan["execution_blockers"]])
        self.assertNotIn("NEEDS_CLARIFICATION", [row["status"] for row in plan["requirements_resolution"]])


class GoalAutomationBoundaryTest(unittest.TestCase):
    def test_answered_visual_requirement_adds_global_voice_prohibition(self):
        for text, quote in (("增加镜头", "增加镜头"), ("补充商品展示镜头", "增加镜头")):
            with self.subTest(text=text, quote=quote):
                requirements = requirements_for("替换第一句口播", text)
                requirements[1].update(request_quote=quote, decision="answered", answer="不要生成配音")
                brief = "替换第一句口播; 增加镜头"
                clauses = active_clauses(brief, "", requirements)
                self.assertIn("不要生成配音", clauses)
                edit = item("VOICE_REPLACE", narration_strategy="tts", script="禁止生成的配音",
                            requirement_ids=[requirements[0]["requirement_id"]])
                record = item_authorization(edit, requirements, brief)
                self.assertFalse(record["allow_narration"])
                plan = compile_plan(
                    snapshot(), [edit], brief, "", manual=False, requirements=requirements,
                    options={"authorization_version": AUTHORIZATION_VERSION},
                )
                self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in plan["execution_blockers"]])

    def test_compound_voice_prohibitions_cannot_become_positive_authorizations(self):
        for denial in ("不要重新配音", "禁止重配音", "不要重录口播"):
            with self.subTest(denial=denial):
                value = permissions(denial)
                self.assertTrue(value["deny_narration"])
                self.assertFalse(value["allow_narration"])
                requirements = requirements_for("修改第一句口播", denial)
                edit = item("VOICE_REPLACE", narration_strategy="tts", script="不得生成的配音",
                            requirement_ids=[requirements[0]["requirement_id"]])
                brief = "; ".join(row["text"] for row in requirements)
                _, blockers = apply_authorizations([edit], requirements, 4000, brief=brief)
                self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in blockers])
                plan = compile_plan(
                    snapshot(), [edit], brief, "", manual=False, requirements=requirements,
                    options={"authorization_version": AUTHORIZATION_VERSION},
                )
                self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in plan["execution_blockers"]])
                self.assertFalse(any(clip["source_kind"] == "tts" for clip in plan["narration_track"]))

    def test_no_extension_constraints_check_net_duration_delta(self):
        for denial in ("不延长视频", "视频时长不能延长"):
            with self.subTest(denial=denial):
                self.assertTrue(constraints(denial)["no_extension"])
                requirements = requirements_for("删除开头一秒", "插入一秒商品展示", denial)
                brief = "; ".join(row["text"] for row in requirements)
                for cut_first in (False, True):
                    with self.subTest(cut_first=cut_first):
                        insert = item(
                            "SEGMENT_INSERT", item_id="insert", range={"start_ms": 2000, "end_ms": 2000},
                            narration_strategy="none", narration_padding_reason="无讲话商品展示",
                            requirement_ids=[requirements[1]["requirement_id"]],
                        )
                        edits = [insert]
                        if cut_first:
                            edits.insert(0, item(
                                "PACE_RECUT", item_id="cut", range={"start_ms": 0, "end_ms": 1000},
                                material_segments=[], removed_ranges=[{"start_ms": 0, "end_ms": 1000}],
                                edit_intent="remove_content", narration_strategy="cut_source",
                                requirement_ids=[requirements[0]["requirement_id"]],
                            ))
                        plan = compile_plan(
                            snapshot(), edits, brief, "", manual=False, requirements=requirements,
                            options={"authorization_version": AUTHORIZATION_VERSION},
                        )
                        codes = [row["code"] for row in plan["execution_blockers"]]
                        if cut_first:
                            self.assertEqual(plan["summary"]["duration_delta_ms"], 0)
                            self.assertFalse(plan["execution_blockers"])
                        else:
                            self.assertEqual(plan["summary"]["duration_delta_ms"], 1000)
                            self.assertIn("GOAL_CONSTRAINT_VIOLATION", codes)

    def test_action_with_duration_constraint_cannot_extend_video(self):
        for requirement_text in ("新增口播但保持原时长", "新增口播"):
            with self.subTest(requirement_text=requirement_text):
                requirements = requirements_for(requirement_text)
                insert = item(
                    "SEGMENT_INSERT", range={"start_ms": 2000, "end_ms": 2000},
                    narration_strategy="tts", script="新增台词", voiceover_id=3,
                    subtitle_strategy="none", requirement_ids=[requirements[0]["requirement_id"]],
                )
                plan = compile_plan(
                    snapshot(), [insert], "新增口播但保持原时长", "", manual=False,
                    requirements=requirements, voiceovers={3: voice()},
                    options={"authorization_version": AUTHORIZATION_VERSION},
                )
                self.assertIn("GOAL_CONSTRAINT_VIOLATION", [row["code"] for row in plan["execution_blockers"]])

    def test_voice_prohibition_and_negated_audio_preservation_are_distinct(self):
        for text in ("台词不要改动", "配音不能更改"):
            with self.subTest(text=text):
                requirements = requirements_for("替换第一句口播", text)
                speech = item("VOICE_REPLACE", narration_strategy="tts", script="修改台词",
                              requirement_ids=[requirements[0]["requirement_id"]])
                record = item_authorization(speech, requirements, "; ".join(row["text"] for row in requirements))
                self.assertFalse(record["allow_narration"])
        value = constraints("不需要保留原配音")
        self.assertFalse(value["deny_narration"])
        self.assertFalse(value["keep_source_audio"])
        requirements = requirements_for("替换第一句口播", "不需要保留原配音")
        speech = item("VOICE_REPLACE", narration_strategy="tts", script="修改台词",
                      requirement_ids=[requirements[0]["requirement_id"]])
        record = item_authorization(speech, requirements, "; ".join(row["text"] for row in requirements))
        self.assertTrue(record["allow_narration"])

    def test_preserve_duration_accepts_equal_cut_and_insert(self):
        requirements = requirements_for("删除开头一秒", "新增口播", "保持原时长")
        cut = item(
            "PACE_RECUT", item_id="cut", range={"start_ms": 0, "end_ms": 1000},
            material_segments=[], removed_ranges=[{"start_ms": 0, "end_ms": 1000}],
            edit_intent="remove_content", narration_strategy="cut_source",
            requirement_ids=[requirements[0]["requirement_id"]],
        )
        insert = item(
            "SEGMENT_INSERT", item_id="insert", range={"start_ms": 2000, "end_ms": 2000},
            narration_strategy="tts", script="新增台词", voiceover_id=3, subtitle_strategy="none",
            requirement_ids=[requirements[1]["requirement_id"]],
        )
        plan = compile_plan(
            snapshot(), [cut, insert], "; ".join(row["text"] for row in requirements), "",
            manual=False, requirements=requirements, voiceovers={3: voice()},
            options={"authorization_version": AUTHORIZATION_VERSION},
        )
        self.assertEqual(plan["summary"]["duration_delta_ms"], 0)
        self.assertFalse(plan["execution_blockers"])

    def test_high_level_goals_authorize_cut_or_visual_but_not_tts(self):
        for text, edit in (("优化开头节奏", recut([])), ("突出商品卖点", item())):
            with self.subTest(text=text):
                requirements = requirements_for(text)
                edit.requirement_ids = [requirements[0]["requirement_id"]]
                record = item_authorization(edit, requirements, text)
                self.assertTrue(record["allow_cut"] if edit.type == "PACE_RECUT" else record["allow_visual"])
                self.assertFalse(record["allow_narration"])
                speech = item("VOICE_REPLACE", narration_strategy="tts", script="擅自改写",
                              requirement_ids=[requirements[0]["requirement_id"]])
                _, blockers = apply_authorizations([speech], requirements, 30000, brief=text)
                self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in blockers])

    def test_visual_prohibition_overrides_other_shot_permission(self):
        requirements = requirements_for("替换商品展示镜头", "不要修改画面")
        edit = item(requirement_ids=[requirements[0]["requirement_id"]])
        brief = "; ".join(row["text"] for row in requirements)
        record = item_authorization(edit, requirements, brief)
        self.assertFalse(record["allow_visual"])
        _, blockers = apply_authorizations([edit], requirements, 4000, brief=brief)
        self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in blockers])

    def test_waived_constraint_does_not_block_other_authorized_edit(self):
        requirements = requirements_for("替换商品展示镜头", "不要修改画面")
        requirements[1]["decision"] = "waived"
        edit = item(requirement_ids=[requirements[0]["requirement_id"]])
        plan = compile_plan(
            snapshot(), [edit], "; ".join(row["text"] for row in requirements), "", manual=False,
            requirements=requirements, options={"authorization_version": AUTHORIZATION_VERSION},
        )
        self.assertFalse(plan["execution_blockers"])

    def test_only_answer_to_preservation_constraint_can_relax_full_narration_conflict(self):
        for answered_index in (0, 1):
            with self.subTest(answered_index=answered_index):
                requirements = requirements_for("将视频缩短到 20 秒", "完整保留全部台词")
                context = snapshot()
                context["source"]["duration_ms"] = 30000
                context["transcript"] = [{"cue_id": "speech", "start_ms": 0, "end_ms": 25000, "text": "完整口播"}]
                row = requirements[answered_index]
                decisions = {row["requirement_id"]: {"decision": "answered", "answer": "允许删减台词, 沿用保留片段的原音频"}}
                client = MagicMock(chat_json=AsyncMock(return_value={"requirements": deepcopy(requirements)}))
                resolved = asyncio.run(parse_requirements(
                    client, context, "; ".join(value["text"] for value in requirements), "",
                    {"target_duration_ms": 20000, "existing_requirements": requirements, "decisions": decisions},
                ))
                if answered_index == 1:
                    self.assertNotIn("CONFLICT", [value["status"] for value in resolved])
                else:
                    self.assertIn("CONFLICT", [value["status"] for value in resolved])


class GoalAutomationReplanningTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = snapshot()
        self.context["source"]["duration_ms"] = 30000
        self.context["asr_state"] = "no_speech"
        self.context["material_catalog"][0]["analysis"] = {
            "data": {"summary": "展示", "highlights": [{"start": 6, "end": 7}]}
        }
        self.requirements = requirements_for("将视频缩短到 20 秒", "不生成新的配音")
        self.brief = "; ".join(row["text"] for row in self.requirements)
        self.options = {"auto_preview": True, "target_duration_ms": 20000}
        self.calls = []
        self.saved = {}
        self.replan_messages = []

    def draft(self, edit):
        return {"timeline": [], "source_traits": {}, "items": [edit] if edit else [],
                "findings_not_applied": []}

    def unauthorized_voice(self):
        return item(
            "VOICE_REPLACE", narration_strategy="tts", script="未获授权的新台词",
            material_segments=[], requirement_ids=[self.requirements[0]["requirement_id"]],
            evidence=["source-video"],
        ).model_dump()

    def compliant_cut(self):
        return recut(self.requirements, evidence=["source-video"]).model_dump()

    async def analyze(self, drafts, *, real_parse=False, cancel=False):
        from app.services.refinement_evaluation import analyze_v2

        async def chat(messages, **kwargs):
            first = messages[0]
            content = first["content"]
            if first["role"] == "system":
                self.assertTrue(content.startswith("重新规划未通过校验"))
                self.calls.append("replanning")
                self.replan_messages.append(deepcopy(messages))
                return deepcopy(drafts[1])
            if isinstance(content, list):
                self.calls.append("planning")
                return deepcopy(drafts[0])
            if content.startswith("将用户精修意见"):
                self.calls.append("requirements")
                return {"requirements": deepcopy(self.requirements)}
            self.calls.append("matching")
            materials = json.loads(content[content.index('{"requirements"'):])["materials"]
            return {"evaluations": [{"key": row["key"], "score": 90, "reason": "展示",
                                     "segments": [{"highlight_index": 0}]} for row in materials]}

        def progress(batches, stats):
            self.saved.update(deepcopy(batches))
            if cancel and any(row.get("error_code") == "planning_constraints_failed"
                              for key, row in batches.items() if key.startswith("draft-diagnostic-")):
                raise RuntimeError("cancelled before replanning")

        client = MagicMock(chat_json=AsyncMock(side_effect=chat))
        settings = SimpleNamespace(llm_mock_mode=False, llm_api_key="test", llm_vision_model="vision")
        parser = parse_requirements if real_parse else AsyncMock(return_value=deepcopy(self.requirements))
        with patch("app.services.refinement_evaluation.source_context", return_value=self.context), \
             patch("app.services.refinement_evaluation.get_settings", return_value=settings), \
             patch("app.services.refinement_evaluation.LLMClient", return_value=client), \
             patch("app.services.refinement_evaluation.parse_requirements", new=parser), \
             patch("app.services.storage.get_storage_by_name", return_value=SimpleNamespace(
                 signed_url=lambda url: "https://media.example/source")):
            return await analyze_v2(self.context, [], self.brief, "", options=self.options,
                                    progress=progress, batches={})

    async def test_unauthorized_tts_is_replanned_into_compliant_source_cut(self):
        result = await self.analyze([self.draft(self.unauthorized_voice()), self.draft(self.compliant_cut())])
        self.assertEqual(self.calls, ["matching", "planning", "replanning"])
        self.assertEqual(result["metrics"]["planning_retries"], 1)
        self.assertFalse(result["execution_blockers"])
        self.assertEqual(result["summary"]["expected_duration_ms"], 20000)
        self.assertEqual(result["items"][0]["narration_strategy"], "cut_source")
        self.assertTrue(any(key.startswith("planning-") for key in self.saved))
        feedback = json.loads(self.replan_messages[0][1]["content"])
        self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in feedback["validation_errors"]])

    async def check_sentence_cut_replanning(self, *, forged_review):
        self.context["asr_state"] = "completed"
        self.context["transcript"] = [
            {"cue_id": "first", "start_ms": 0, "end_ms": 12000, "text": "开头完整介绍商品。"},
            {"cue_id": "second", "start_ms": 12000, "end_ms": 22000, "text": "重复的商品介绍。"},
        ]
        invalid = self.compliant_cut()
        invalid["boundary_reviewed"] = forged_review
        valid = self.compliant_cut()
        valid["removed_ranges"] = [{"start_ms": 12000, "end_ms": 22000}]
        result = await self.analyze([self.draft(invalid), self.draft(valid)])
        self.assertEqual(self.calls, ["matching", "planning", "replanning"])
        self.assertEqual(result["metrics"]["planning_retries"], 1)
        self.assertEqual(result["summary"]["expected_duration_ms"], 20000)
        self.assertFalse(result["execution_blockers"])
        self.assertNotIn("NEEDS_CLARIFICATION", [row["status"] for row in result["requirements_resolution"]])
        self.assertEqual(result["items"][0]["removed_ranges"], valid["removed_ranges"])
        feedback = json.loads(self.replan_messages[0][1]["content"])
        boundary_codes = {"SPEECH_BOUNDARY", "SENTENCE_BOUNDARY", "INCOMPLETE_SENTENCE", "VOICE_RANGE_SPLIT"}
        self.assertTrue(boundary_codes.intersection(row["code"] for row in feedback["validation_errors"]))

    async def test_shortening_cut_that_splits_sentence_is_automatically_replanned(self):
        await self.check_sentence_cut_replanning(forged_review=False)

    async def test_forged_ai_boundary_review_cannot_bypass_sentence_replanning(self):
        await self.check_sentence_cut_replanning(forged_review=True)

    async def test_repeated_violation_fails_without_caching_invalid_plan(self):
        draft = self.draft(self.unauthorized_voice())
        with self.assertRaisesRegex(ValueError, "重新规划后仍未满足"):
            await self.analyze([draft, draft])
        self.assertEqual(self.calls, ["matching", "planning", "replanning"])
        self.assertFalse(any(key.startswith("planning-") for key in self.saved))
        self.assertTrue(any(key.startswith("source-context-") for key in self.saved))
        self.assertTrue(any(key.startswith("relevance-") for key in self.saved))
        diagnostic = next(row for key, row in self.saved.items() if key.startswith("draft-diagnostic-"))
        self.assertEqual(diagnostic["error_code"], "planning_constraints_failed")
        self.assertEqual(diagnostic["replan_calls"], 1)

    async def test_real_full_narration_conflict_does_not_trigger_semantic_replanning(self):
        self.requirements = requirements_for("将视频缩短到 20 秒", "完整保留全部台词")
        self.brief = "; ".join(row["text"] for row in self.requirements)
        self.context["asr_state"] = "completed"
        self.context["transcript"] = [
            {"cue_id": "speech", "start_ms": 0, "end_ms": 25000, "text": "完整口播"}
        ]
        result = await self.analyze([self.draft(None)], real_parse=True)
        self.assertEqual(self.calls, ["requirements", "matching", "planning"])
        self.assertEqual(result["metrics"]["planning_retries"], 0)
        self.assertIn("CONFLICT", [row["status"] for row in result["requirements_resolution"]])
        self.assertTrue(result["execution_blockers"])

    async def test_real_conflict_with_unauthorized_tts_is_not_replanned_or_cached(self):
        self.requirements = requirements_for("将视频缩短到 20 秒", "完整保留全部台词")
        self.options["existing_requirements"] = deepcopy(self.requirements)
        self.brief = "; ".join(row["text"] for row in self.requirements)
        self.context["asr_state"] = "completed"
        self.context["transcript"] = [
            {"cue_id": "speech", "start_ms": 0, "end_ms": 25000, "text": "完整口播"}
        ]
        draft = self.draft(self.unauthorized_voice())
        self.assertEqual(draft["items"][0]["requirement_ids"], [self.requirements[0]["requirement_id"]])
        result = await self.analyze([draft], real_parse=True)
        self.assertEqual(self.calls, ["requirements", "matching", "planning"])
        self.assertEqual(result["metrics"]["planning_retries"], 0)
        self.assertIn("CONFLICT", [row["status"] for row in result["requirements_resolution"]])
        self.assertIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in result["execution_blockers"]])
        self.assertFalse(any(key.startswith("planning-") for key in self.saved))

    async def test_progress_cancellation_prevents_semantic_replanning_call(self):
        with self.assertRaisesRegex(RuntimeError, "cancelled before replanning"):
            await self.analyze([self.draft(self.unauthorized_voice())], cancel=True)
        self.assertEqual(self.calls, ["matching", "planning"])
        self.assertFalse(any(key.startswith("planning-") for key in self.saved))

    async def test_semantic_replanning_still_rejects_forged_evidence(self):
        invalid = self.compliant_cut()
        invalid["evidence"] = [self.requirements[0]["requirement_id"]]
        with self.assertRaisesRegex(ValueError, "不存在的镜头或素材依据"):
            await self.analyze([self.draft(self.unauthorized_voice()), self.draft(invalid)])
        self.assertEqual(self.calls, ["matching", "planning", "replanning"])
        self.assertFalse(any(key.startswith("planning-") for key in self.saved))

    async def test_semantic_replanning_still_rejects_invalid_coordinates(self):
        invalid = self.compliant_cut()
        invalid["range"]["end_ms"] = 30000.5
        with self.assertRaisesRegex(ValueError, "方案格式无效"):
            await self.analyze([self.draft(self.unauthorized_voice()), self.draft(invalid)])
        self.assertFalse(any(key.startswith("planning-") for key in self.saved))


if __name__ == "__main__":
    unittest.main()
