from __future__ import annotations

import asyncio
from copy import deepcopy
from fractions import Fraction
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.refinement_automation import prepare_materials
from app.services.refinement_evaluation import analyze_v2, apply_visual_only_defaults
from app.services.refinement_plan import compile_plan, content_hash
from app.services.refinement_timing import candidate_frame_bounds, frame_at_ms, ms_at_frame, required_frames, technical_tail_allowed
from test_refinement_v2 import item, snapshot, voice


class FrameBudgetTest(unittest.TestCase):
    def test_budget_uses_one_ceil_including_fractional_rates(self):
        for fps in (24, 25, 30, 60, "24000/1001", "30000/1001"):
            with self.subTest(fps=fps):
                frames = required_frames(9240, fps)
                rate = Fraction(str(fps))
                self.assertGreaterEqual(Fraction(frames, 1) / rate, Fraction(9240, 1000))
                self.assertLess(Fraction(frames - 1, 1) / rate, Fraction(9240, 1000))
        self.assertEqual(required_frames(9240, 30), 278)
        self.assertEqual(required_frames(1000, 30), 30)

    def test_rounded_frame_endpoints_restore_the_same_budget(self):
        for fps in (24, 25, 30, 60, "30000/1001"):
            for start, end in ((0, 76), (77, 159), (101, 379)):
                with self.subTest(fps=fps, start=start):
                    self.assertEqual(candidate_frame_bounds(ms_at_frame(start, fps), ms_at_frame(end, fps), fps), (start, end))

    def test_tail_is_bounded_and_never_accepts_truncation(self):
        self.assertTrue(technical_tail_allowed(34, 1000, 30))
        self.assertFalse(technical_tail_allowed(35, 1000, 30))
        self.assertTrue(technical_tail_allowed(200, 1000, 5))
        self.assertFalse(technical_tail_allowed(-1, 48000, 30))
        self.assertFalse(technical_tail_allowed(4800, 48000, 30))
        for ms in range(0, 40):
            self.assertEqual(technical_tail_allowed(ms, 1000, 30), technical_tail_allowed(ms * 48, 48000, 30))

    def prepare(self, context, edit, **kwargs):
        client = MagicMock(chat_json=AsyncMock(return_value={"usable":True,"burned_subtitles":False}))
        with patch("app.services.refinement_evaluation.sampled_frames",return_value=[]), patch("app.services.refinement_automation.get_settings",return_value=SimpleNamespace(llm_vision_model="vision")):
            return asyncio.run(prepare_materials(client,context,[edit],[],{},**kwargs))

    def test_task_21_spends_278_frames_and_trims_only_last_segment(self):
        context = snapshot()
        context["source"]["duration_ms"] = 22780
        context["material_catalog"][0]["asset_id"] = 254
        context["material_catalog"].append({**context["material_catalog"][0],"asset_id":205})
        segments = [{"asset_id":254,"source_range":{"start_ms":0,"end_ms":2533}},
                    {"asset_id":254,"source_range":{"start_ms":2567,"end_ms":5300}},
                    {"asset_id":205,"source_range":{"start_ms":0,"end_ms":4033}}]
        original = item("SEGMENT_INSERT",range={"start_ms":22780,"end_ms":22780},script="新增台词",narration_strategy="tts",voiceover_id=3,
                        material_segments=segments,requirement_ids=["req"])
        plans = []
        for _ in range(2):
            edit = original.model_copy(deep=True)
            adjustments = self.prepare(deepcopy(context),edit,lengths={"edit":9267},frame_budgets={"edit":278})
            self.assertEqual(sum(frame_at_ms(s.source_range.end_ms,30)-frame_at_ms(s.source_range.start_ms,30) for s in edit.material_segments),278)
            self.assertEqual(edit.material_segments[-1].source_range.end_ms,4000)
            self.assertEqual([s.model_dump(exclude_none=True) for s in edit.material_segments[:2]],segments[:2])
            self.assertEqual(len(adjustments),1)
            self.assertEqual(adjustments[0]["before_range"]["end_ms"],4033)
            plan = compile_plan(context,[edit],"明确新增讲解","",manual=False,voiceovers={3:voice(duration=9240)},
                                requirements=[{"requirement_id":"req","text":"新增讲解","status":"EXECUTABLE","reason":"已授权","item_ids":[]}])
            self.assertFalse(plan["execution_blockers"])
            self.assertEqual(plan["requirements_resolution"][0]["status"],"EXECUTABLE")
            plan["automatic_adjustments"] = adjustments
            plans.append(plan)
        self.assertEqual(content_hash(plans[0]),content_hash(plans[1]))

    def test_multiple_materials_never_spend_an_extra_frame(self):
        for fps in (24, 25, 30, 60):
            with self.subTest(fps=fps):
                context = snapshot();context["source"]["fps"] = fps
                context["material_catalog"].append({**context["material_catalog"][0],"asset_id":3})
                edit = item("SEGMENT_INSERT",range={"start_ms":0,"end_ms":0},narration_strategy="none",narration_padding_reason="展示",
                            material_segments=[{"asset_id":2,"source_range":{"start_ms":0,"end_ms":1000}},
                                               {"asset_id":3,"source_range":{"start_ms":0,"end_ms":2000}}])
                self.prepare(context,edit,lengths={"edit":1234})
                self.assertEqual(sum(frame_at_ms(s.source_range.end_ms,fps)-frame_at_ms(s.source_range.start_ms,fps) for s in edit.material_segments),required_frames(1234,fps))
                self.assertEqual(edit.narration_strategy,"none")


class RequirementBlockingTest(unittest.TestCase):
    def compile(self, edit, **kw):
        return compile_plan(snapshot(),[edit],"增加展示镜头","",manual=False,
                            requirements=[{"requirement_id":"req","text":"展示镜头","status":"EXECUTABLE","reason":"素材适用","item_ids":[],"decision":"pending"}],**kw)

    def test_audio_failure_does_not_claim_missing_material(self):
        edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},narration_strategy="tts",script="新增台词",voiceover_id=3,requirement_ids=["req"])
        plan = self.compile(edit,voiceovers={3:voice(duration=1200)})
        requirement = plan["requirements_resolution"][0]
        self.assertEqual(requirement["status"],"EXECUTABLE")
        self.assertEqual(requirement["execution_blockers"][0]["category"],"audio")
        self.assertNotIn("REQUIREMENT_UNRESOLVED",[b["code"] for b in plan["execution_blockers"]])

    def test_actual_material_shortage_is_reported_once_and_recovers(self):
        edit = item(material_segments=[],requirement_ids=["req"])
        first = self.compile(edit)
        self.assertEqual(first["requirements_resolution"][0]["status"],"AWAITING_MATERIAL")
        self.assertNotIn("REQUIREMENT_UNRESOLVED",[b["code"] for b in first["execution_blockers"]])
        second = compile_plan(snapshot(),[item(requirement_ids=["req"])],"镜头","",manual=False,requirements=first["requirements_resolution"])
        self.assertEqual(second["requirements_resolution"][0]["status"],"EXECUTABLE")
        self.assertFalse(second["requirements_resolution"][0]["execution_blockers"])

    def test_compiler_only_accepts_technical_tail_not_a_global_100_ms_gap(self):
        edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},narration_strategy="tts",script="新增台词",voiceover_id=3,requirement_ids=["req"],
                    material_segments=[{"asset_id":2,"source_range":{"start_ms":0,"end_ms":1033}}])
        self.assertFalse(self.compile(edit,voiceovers={3:voice()})["execution_blockers"])
        edit.material_segments[0].source_range.end_ms = 1100
        self.assertIn("VOICEOVER_DURATION",[b["code"] for b in self.compile(edit,voiceovers={3:voice()})["execution_blockers"]])


class AudioAuthorizationTest(unittest.TestCase):
    def test_automatic_analysis_cannot_override_visual_intent_with_invented_tts(self):
        context = snapshot()
        context["material_catalog"][0]["analysis"] = {"data":{"summary":"商品展示","highlights":[{"start":6,"end":7,"description":"包装展示"}]}}
        proposed = item("SEGMENT_INSERT",range={"start_ms":4000,"end_ms":4000},narration_strategy="tts",script="模型擅自新增台词",requirement_ids=["req"]).model_dump()
        def response(messages, **kwargs):
            content = messages[0]["content"]
            text = content if isinstance(content,str) else content[0]["text"]
            if text.startswith("评估本批"):
                data = json.loads(text[text.index('{"requirements":'):])
                return {"evaluations":[{"key":unit["key"],"score":100,"reason":"商品清晰","segments":[{"highlight_index":0}]} for unit in data["materials"]]}
            return {"items":[proposed],"timeline":[],"findings_not_applied":[]}
        client = MagicMock(chat_json=AsyncMock(side_effect=response))
        requirements = [{"requirement_id":"req","text":"增加展示和新台词","status":"EXECUTABLE","reason":"规划适用","item_ids":[]}]
        with patch("app.services.refinement_evaluation.get_settings",return_value=SimpleNamespace(llm_mock_mode=False,llm_api_key="test",llm_vision_model="test")), \
             patch("app.services.refinement_evaluation.LLMClient",return_value=client), \
             patch("app.services.refinement_evaluation.source_context",return_value=context), \
             patch("app.services.refinement_evaluation.parse_requirements",new=AsyncMock(return_value=requirements)), \
             patch("app.services.storage.get_storage_by_name",return_value=SimpleNamespace(signed_url=lambda url:"https://example.invalid/source.mp4")):
            plan = asyncio.run(analyze_v2(context,[],"增加一个商品展示的镜头","",options={"auto_preview":True,"supplement_narration":"tts"},progress=None,batches={}))
        self.assertFalse(plan["execution_blockers"])
        self.assertEqual(plan["items"][0]["type"],"VISUAL_REPLACE")
        self.assertEqual(plan["items"][0]["script"],"")
        self.assertEqual(plan["summary"]["duration_delta_ms"],0)
        self.assertFalse(plan["voiceover_assets"])

    def test_visual_default_does_not_trust_invented_requirements_or_end_anchor(self):
        for brief in ("增加一个商品展示的镜头", "补充展示画面, 保留原配音和时长", "增加镜头, 不要重新配音, 不延长视频"):
            with self.subTest(brief=brief):
                edit = item("SEGMENT_INSERT",range={"start_ms":4000,"end_ms":4000},script="未授权新台词",narration_strategy="tts",voiceover_id=3,requirement_ids=["req"])
                requirements = [{"requirement_id":"req","text":"增加镜头和新配音","status":"EXECUTABLE","reason":"素材适用","item_ids":[]}]
                apply_visual_only_defaults([edit],requirements,4000,brief=brief)
                self.assertEqual(edit.type,"VISUAL_REPLACE")
                self.assertEqual(edit.range.model_dump(),{"start_ms":3000,"end_ms":4000})
                self.assertEqual(edit.script,"")
                self.assertIsNone(edit.voiceover_id)
                plan = compile_plan(snapshot(),[edit],brief,"",manual=False,requirements=requirements)
                self.assertFalse(plan["execution_blockers"])
                self.assertEqual(plan["summary"]["duration_delta_ms"],0)
                self.assertTrue(all(c["source_kind"] == "source" for c in plan["narration_track"]))

    def test_explicit_insertion_without_speech_does_not_force_tts(self):
        edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},narration_strategy="tts",script="未授权")
        apply_visual_only_defaults([edit],[],4000,brief="插入一段商品展示, 保留原内容, 不增加口播")
        self.assertEqual(edit.narration_strategy,"none")
        self.assertFalse(edit.script)
        self.assertFalse(compile_plan(snapshot(),[edit],"插入展示","",manual=False)["execution_blockers"])

    def test_explicit_narration_and_answer_authorize_tts(self):
        for brief, requirements in (("新增口播和商品展示", []), ("补充展示段重新配音", []), ("增加一个镜头", [{"requirement_id":"req","text":"展示","decision":"answered","answer":"新增讲解配音"}])):
            edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},narration_strategy="tts",script="授权台词",requirement_ids=["req"])
            apply_visual_only_defaults([edit],requirements,4000,brief=brief)
            self.assertEqual(edit.type,"SEGMENT_INSERT")
            self.assertEqual(edit.narration_strategy,"tts")
            self.assertEqual(edit.script,"授权台词")
