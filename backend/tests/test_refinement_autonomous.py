import json
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.refinement_authorization import AUTONOMOUS_VERSION
from app.services.refinement_evaluation import analyze_v2
from app.services.refinement_plan import compile_plan
from app.services.refinement_policy import generation_policy
from app.services.refinement_requirements import parse_requirements
from test_refinement_goal_automation import requirements_for
from test_refinement_precise import precise_snapshot, sampled_voice
from test_refinement_v2 import item, snapshot, voice
import test_refinement_v2_api as api_tests


GOAL = "增加商品使用细节"
OPTIONS = {"authorization_version": AUTONOMOUS_VERSION}


class AutonomousRefinementTest(unittest.TestCase):
    def compile(self, edit, *, brief=GOAL, options=None, context=None, voices=None):
        requirements = requirements_for(brief)
        edit.requirement_ids = [requirements[0]["requirement_id"]]
        return compile_plan(
            context or snapshot(), [edit], brief, "", manual=False,
            requirements=requirements, options={**OPTIONS, **(options or {})},
            voiceovers=voices or {},
        )

    def test_intent_without_keywords_authorizes_visual_edit(self):
        plan = self.compile(item())
        self.assertFalse(plan["execution_blockers"])
        self.assertTrue(plan["edit_authorizations"]["edit"]["allow_visual"])

    def test_intent_authorizes_local_voice_and_extension_without_extra_permission(self):
        edit = item(
            "SEGMENT_INSERT", range={"start_ms": 2000, "end_ms": 2000},
            narration_strategy="tts", script="新增台词", voiceover_id=3,
            subtitle_strategy="regenerate",
        )
        plan = self.compile(edit, voices={3: voice()})
        self.assertFalse(plan["execution_blockers"])
        self.assertEqual(plan["items"][0]["type"], "SEGMENT_INSERT")
        self.assertEqual(plan["items"][0]["narration_strategy"], "tts")
        self.assertEqual(plan["summary"]["expected_duration_ms"], 5000)
        self.assertTrue(any(row["source_kind"] == "tts" for row in plan["narration_track"]))

    def test_text_preferences_do_not_reintroduce_authorization_blockers(self):
        brief = GOAL + "; 不生成新的配音; 保留原配音; 不延长视频; 保持原时长"
        requirements = requirements_for(*brief.split("; "))
        edit = item(
            "SEGMENT_INSERT", range={"start_ms": 2000, "end_ms": 2000},
            narration_strategy="tts", script="新增台词", voiceover_id=3,
            subtitle_strategy="none", requirement_ids=[row["requirement_id"] for row in requirements],
        )
        for preference in ("preserve", "shorten"):
            with self.subTest(preference=preference):
                plan = compile_plan(
                    snapshot(), [edit], brief, "", manual=False, requirements=requirements,
                    options={**OPTIONS, "duration_preference": preference, "target_duration_ms": 3000},
                    voiceovers={3: voice()},
                )
                self.assertFalse(plan["execution_blockers"])
                self.assertEqual(plan["summary"]["expected_duration_ms"], 5000)

    def test_precise_timing_keeps_autonomous_permissions(self):
        edit = item(
            "SEGMENT_INSERT", range={"start_ms": 2002, "end_ms": 2002},
            narration_strategy="tts", script="新增台词", voiceover_id=3, subtitle_strategy="none",
            material_segments=[{"asset_id": 2, "source_range": {"start_ms": 6000, "end_ms": 7001}}],
        )
        plan = self.compile(
            edit, context=precise_snapshot(), options={"timing_protocol": "v3", "duration_preference": "preserve"},
            voices={3: sampled_voice()},
        )
        self.assertFalse(plan["execution_blockers"])
        self.assertEqual(plan["generation_policy"]["authorization_version"], AUTONOMOUS_VERSION)
        self.assertGreater(plan["summary"]["duration_delta_ms"], 0)

    def test_autonomy_does_not_bypass_material_ownership_or_bounds(self):
        for asset_id, end in ((999, 7000), (2, 19000)):
            with self.subTest(asset_id=asset_id, end=end), self.assertRaises(ValueError):
                self.compile(item(material_segments=[{
                    "asset_id": asset_id, "source_range": {"start_ms": 6000, "end_ms": end},
                }]))

    def test_autonomy_does_not_bypass_material_coverage_or_voice_duration(self):
        plan = self.compile(item(material_segments=[{
            "asset_id": 2, "source_range": {"start_ms": 6000, "end_ms": 6500},
        }]))
        self.assertIn("MATERIAL_TOO_SHORT", [row["code"] for row in plan["execution_blockers"]])
        edit = item("VOICE_REPLACE", material_segments=[], narration_strategy="tts", script="新增台词", voiceover_id=3)
        plan = self.compile(edit, voices={3: voice(duration=1500)})
        self.assertIn("VOICEOVER_DURATION", [row["code"] for row in plan["execution_blockers"]])

    def test_autonomy_does_not_bypass_speech_boundaries(self):
        context = snapshot()
        context["words"] = [{"start_ms": 900, "end_ms": 1100, "text": "商品"}]
        context["transcript"] = [{"cue_id": "speech", "start_ms": 0, "end_ms": 4000, "text": "完整台词"}]
        edit = item(
            "PACE_RECUT", material_segments=[], removed_ranges=[{"start_ms": 1000, "end_ms": 1500}],
            edit_intent="remove_content", narration_strategy="cut_source", boundary_reviewed=False,
        )
        plan = self.compile(edit, context=context)
        codes = {row["code"] for row in plan["execution_blockers"]}
        self.assertTrue(codes & {"SPEECH_BOUNDARY", "SENTENCE_BOUNDARY", "INCOMPLETE_SENTENCE"})
        self.assertNotIn("EDIT_AUTHORIZATION_REQUIRED", codes)

    def test_server_policy_enables_autonomy_without_enabling_timing_rollout(self):
        settings = SimpleNamespace(
            refinement_rollout_tenants="", refinement_precise_timing_enabled=True,
            refinement_localization_enabled=True, refinement_content_reselection_enabled=True,
            refinement_display_tail_enabled=True,
        )
        with patch("app.services.refinement_policy.get_settings", return_value=settings):
            policy = generation_policy("tenant")
        self.assertEqual(policy["authorization_version"], AUTONOMOUS_VERSION)
        self.assertEqual(policy["timing_protocol"], "v2")
        self.assertFalse(policy["localization"])


class AutonomousPlanningTest(unittest.IsolatedAsyncioTestCase):
    async def test_requirement_parser_does_not_turn_preferences_into_conflicts(self):
        brief = "缩短到 2 秒; 完整保留全部台词; 保持原时长"
        context = snapshot()
        context["transcript"] = [{"start_ms": 0, "end_ms": 4000, "text": "完整口播"}]
        client = MagicMock(chat_json=AsyncMock(return_value={"requirements": requirements_for(*brief.split("; "))}))
        result = await parse_requirements(client, context, brief, "", {**OPTIONS, "target_duration_ms": 2000})
        self.assertNotIn("CONFLICT", [row["status"] for row in result])
        prompt = client.chat_json.call_args.args[0][0]["content"]
        self.assertIn("由 AI 自主完成", prompt)
        self.assertIn("不生成独立限制项或授权冲突", prompt)

    async def test_usage_detail_plan_is_accepted_without_authorization_replanning(self):
        context = snapshot()
        context["asr_state"] = "no_speech"
        context["material_catalog"][0]["analysis"] = {
            "data": {"summary": "使用展示", "highlights": [{"start": 6, "end": 7}]},
        }
        requirements = requirements_for(GOAL)
        edit = item(
            "SEGMENT_INSERT", range={"start_ms": 2000, "end_ms": 2000},
            narration_strategy="tts", script="新增台词", subtitle_strategy="regenerate",
            requirement_ids=[requirements[0]["requirement_id"]],
        )
        draft = {"timeline": [], "source_traits": {}, "items": [edit.model_dump()], "findings_not_applied": []}
        prompts = []

        async def chat(messages, **kwargs):
            content = messages[0]["content"]
            self.assertNotEqual(messages[0]["role"], "system")
            if isinstance(content, list):
                prompts.append(content[0]["text"])
                return deepcopy(draft)
            materials = json.loads(content[content.index('{"requirements"'):])["materials"]
            return {"evaluations": [{"key": row["key"], "score": 90, "reason": "使用展示",
                                     "segments": [{"highlight_index": 0}]} for row in materials]}

        client = MagicMock(chat_json=AsyncMock(side_effect=chat))
        settings = SimpleNamespace(llm_mock_mode=False, llm_api_key="test", llm_vision_model="vision")
        with patch("app.services.refinement_evaluation.source_context", return_value=context), \
             patch("app.services.refinement_evaluation.get_settings", return_value=settings), \
             patch("app.services.refinement_evaluation.LLMClient", return_value=client), \
             patch("app.services.refinement_evaluation.parse_requirements", new=AsyncMock(return_value=requirements)), \
             patch("app.services.storage.get_storage_by_name", return_value=SimpleNamespace(signed_url=lambda url: "https://media.example/source")):
            plan = await analyze_v2(context, [], GOAL, "", options={**OPTIONS, "auto_preview": True}, progress=None, batches={})
        codes = {row["code"] for row in plan["execution_blockers"]}
        self.assertNotIn("EDIT_AUTHORIZATION_REQUIRED", codes)
        self.assertIn("VOICEOVER_REQUIRED", codes)
        self.assertEqual(plan["metrics"]["planning_retries"], 0)
        self.assertEqual(plan["items"][0]["type"], "SEGMENT_INSERT")
        self.assertEqual(plan["automation_options"]["authorization_version"], AUTONOMOUS_VERSION)
        self.assertIn("AI 可根据目标自主插入展示或讲解", prompts[0])
        self.assertIn("ASR 缺失时放在完整源片末尾", prompts[0])
        self.assertIn("仅选择没有烧录字幕的同商品素材", prompts[0])
        self.assertNotIn("不代表用户授权新增配音或延长视频", prompts[0])
        self.assertNotIn("未明确要求新台词或延长视频时", prompts[0])


class AutonomousApiTest(unittest.TestCase):
    setUp = api_tests.RefinementV2ApiTest.setUp
    tearDown = api_tests.RefinementV2ApiTest.tearDown
    asset = api_tests.RefinementV2ApiTest.asset
    create = api_tests.RefinementV2ApiTest.create

    def test_failed_legacy_job_retries_with_autonomy_and_keeps_media_checkpoints(self):
        from app.models import EcommerceVideoRefinementAnalysisJob
        from app.services.refinement_authorization import AUTHORIZATION_VERSION

        task = self.create(user_brief=GOAL)
        path = f"/api/video-refinements/{task['id']}/auto-preview"
        self.assertEqual(self.client.post(path, json={"revision": 0}).status_code, 202)
        job = self.db.query(EcommerceVideoRefinementAnalysisJob).one()
        job.status = "failed"
        job.error_message = "旧规则授权拦截"
        job.options_json = {**job.options_json, "authorization_version": AUTHORIZATION_VERSION, "timing_protocol": "v3"}
        job.batches_json = {"media-checkpoint": {"data": {"summary": "已分析素材"}}}
        self.db.commit()
        response = self.client.post(path, json={"revision": 0})
        self.assertEqual(response.status_code, 202, response.text)
        self.db.refresh(job)
        self.assertEqual(job.options_json["authorization_version"], AUTONOMOUS_VERSION)
        self.assertEqual(job.options_json["timing_protocol"], "v3")
        self.assertEqual(job.batches_json["media-checkpoint"]["data"]["summary"], "已分析素材")
        self.assertEqual(job.error_message, "")

    def test_saving_autonomous_draft_does_not_restore_keyword_authorization(self):
        from app.models import EcommerceVideoRefinementPlan, EcommerceVideoRefinementSnapshot
        from app.services.refinement_plan import content_hash

        task = self.create(user_brief=GOAL)
        requirements = requirements_for(GOAL)
        edit = item(
            material_segments=[{"asset_id": self.material.id, "source_range": {"start_ms": 6000, "end_ms": 7000}}],
            requirement_ids=[requirements[0]["requirement_id"]],
        )
        plan = compile_plan(task["source_snapshot"], [edit], GOAL, "", manual=False, requirements=requirements, options=OPTIONS)
        frozen = self.db.query(EcommerceVideoRefinementSnapshot).filter_by(task_id=task["id"]).one()
        self.db.add(EcommerceVideoRefinementPlan(
            task_id=task["id"], revision=0, source_snapshot_id=frozen.id,
            plan_json=plan, plan_hash=content_hash(plan), origin="ai",
        ))
        self.db.commit()
        response = self.client.patch(f"/api/video-refinements/{task['id']}/draft", json={"revision": 0, "items": plan["items"]})
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()["plan"]
        self.assertEqual(updated["generation_policy"]["authorization_version"], AUTONOMOUS_VERSION)
        self.assertNotIn("EDIT_AUTHORIZATION_REQUIRED", [row["code"] for row in updated["execution_blockers"]])


if __name__ == "__main__":
    unittest.main()
