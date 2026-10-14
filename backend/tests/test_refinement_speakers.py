from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from pydantic import ValidationError

from app.models import EcommerceVideoRefinementPlan, EcommerceVideoRefinementTask, EcommerceVideoRefinementVoiceover
from app.schemas.video_refinement import RefinementInput, RefinementItem, RequirementsInput
from app.services.refinement_plan import compile_plan, content_hash
from app.services.refinement_speakers import (
    cached_source_selection, chinese_speakers, manual_selection, pitch_observation, select_source_voice,
    selection_key, source_voice_observation, speaker_by_id, valid_selection,
)
from app.services.refinement_voiceover import voiceover_fingerprint, voiceover_parameters
import test_refinement_v2 as timeline_fixture
import test_refinement_v2_api as api_fixture

FEMALE = "zh_female_shuangkuaisisi_uranus_bigtts"
MALE = "zh_male_m191_uranus_bigtts"


def automatic(context, brief, expected="", scripts=()):
    return {**manual_selection(MALE), "mode":"auto", "basis":"source_audio_and_content",
            "reason":"参考原片低声区和商品讲解风格", "key":selection_key(context,brief,expected,scripts)}


class ChineseSpeakerTest(unittest.TestCase):
    def test_catalog_has_exactly_276_real_unique_chinese_ids(self):
        rows = chinese_speakers()
        self.assertEqual(len(rows),276)
        self.assertEqual(len({r["id"] for r in rows}),276)
        self.assertEqual(sum(r["gender"]=="female" for r in rows),110)
        self.assertEqual(sum(r["gender"]=="male" for r in rows),166)
        self.assertTrue(all("zh" in r["languages"] and r["resource_id"]=="seed-tts-2.0" for r in rows))
        self.assertEqual(speaker_by_id(FEMALE)["name"],"爽快思思 2.0")
        import json
        text=json.dumps(sorted([[r["id"],r["name"]] for r in rows]),ensure_ascii=False,separators=(",",":"))
        checksum=2166136261
        for char in text:
            checksum=((checksum ^ ord(char))*16777619) & 0xffffffff
        self.assertEqual(f"{checksum:x}","76b3beae")

    def test_unknown_and_foreign_speaker_ids_are_rejected_by_all_inputs(self):
        for speaker in ("en_male_tim_uranus_bigtts","arbitrary","", "../speaker"):
            with self.subTest(speaker=speaker):
                with self.assertRaises(ValueError):
                    speaker_by_id(speaker)
                with self.assertRaises(ValidationError):
                    RequirementsInput(revision=0,tts_speaker=speaker)
                with self.assertRaises(ValidationError):
                    RefinementInput(source_asset_id=1,user_brief="新增台词",tts_speaker=speaker)
                with self.assertRaises(ValidationError):
                    timeline_fixture.item("VOICE_REPLACE",tts_speaker=speaker)
        self.assertIsNone(RequirementsInput(revision=0,tts_speaker=None).tts_speaker)

    def test_pitch_uses_actual_samples_and_rejects_silence(self):
        seconds = np.arange(16000)/8000
        for pitch, register in ((110,"low"),(190,"mid"),(280,"high")):
            result = pitch_observation((5000*np.sin(2*np.pi*pitch*seconds)).astype(np.int16))
            self.assertEqual(result["state"],"measured")
            self.assertAlmostEqual(result["median_pitch_hz"],pitch,delta=4)
            self.assertEqual(result["register"],register)
        self.assertEqual(pitch_observation(np.zeros(16000,dtype=np.int16))["state"],"unreliable")

    def test_no_speech_does_not_read_or_infer_audio(self):
        context=timeline_fixture.snapshot()
        context["source"]["has_audio"]=False
        with patch("app.services.refinement_speakers.verified_path") as media:
            self.assertEqual(source_voice_observation(context)["state"],"content_only")
        media.assert_not_called()

    def test_audio_is_sampled_only_in_real_transcript_intervals(self):
        context=timeline_fixture.snapshot()
        context["source"]["has_background_music"]=False
        context["asr_state"]="completed"
        context["transcript"]=[{"start_ms":500,"end_ms":2500,"text":"厨房湿巾轻松清洁","is_mock":False}]
        samples=(5000*np.sin(2*np.pi*110*np.arange(16000)/8000)).astype("<i2").tobytes()
        with patch("app.services.refinement_speakers.verified_path",return_value="source.mp4"), patch(
            "app.services.refinement_speakers.subprocess.run",return_value=SimpleNamespace(returncode=0,stdout=samples)) as extract:
            result=source_voice_observation(context)
        self.assertEqual(result["state"],"measured")
        self.assertEqual(result["register"],"low")
        command=extract.call_args.args[0]
        self.assertEqual(command[command.index("-ss")+1],"0.5")
        self.assertEqual(command[command.index("-t")+1],"2.0")

    def test_selected_voice_changes_fingerprint_but_explanation_does_not(self):
        male=voiceover_parameters(MALE,selection=manual_selection(MALE))
        female=voiceover_parameters(FEMALE,selection=manual_selection(FEMALE))
        self.assertNotEqual(voiceover_fingerprint("台词",male),voiceover_fingerprint("台词",female))
        rewritten=deepcopy(male);rewritten["selection"]["reason"]="同一音色的新说明"
        self.assertEqual(voiceover_fingerprint("台词",male),voiceover_fingerprint("台词",rewritten))
        faster={**male,"speech_rate":10}
        self.assertNotEqual(voiceover_fingerprint("台词",male),voiceover_fingerprint("台词",faster))

    def test_changing_manual_speaker_blocks_old_bound_audio(self):
        context=timeline_fixture.snapshot()
        params=voiceover_parameters(FEMALE)
        voice={"id":3,"status":"ready","script":"完整台词","parameters":params,"fingerprint":voiceover_fingerprint("完整台词",params),
               "content_hash":"c"*64,"duration_ms":1000,"timing":{"segments":[]}}
        edit=timeline_fixture.item("VOICE_REPLACE",script="完整台词",tts_speaker=MALE,voiceover_id=3,
                                    material_segments=[],subtitle_strategy="none",range={"start_ms":0,"end_ms":1000})
        plan=compile_plan(context,[edit],"改写台词","",manual=True,voiceovers={3:voice})
        self.assertIn("VOICEOVER_REQUIRED",[r["code"] for r in plan["execution_blockers"]])


class VoiceSelectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_audio_and_video_content_select_a_nondefault_voice(self):
        context=timeline_fixture.snapshot()
        client=MagicMock();client.chat_json=AsyncMock(return_value={"speaker":MALE,"speech_rate":8,"reason":"声区较低, 原片是稳重的商品讲解"})
        settings=SimpleNamespace(llm_mock_mode=False,llm_api_key="test-key",llm_vision_model="vision")
        with patch("app.services.refinement_speakers.get_settings",return_value=settings), patch(
            "app.services.refinement_speakers.source_voice_observation",return_value={"state":"measured","register":"low","mixed_audio":False}), patch(
            "app.services.refinement_evaluation.sampled_frames",return_value=[{"type":"text","text":"真实源片"}]):
            result=await select_source_voice(context,"新增台词","",client=client)
        self.assertEqual(result["speaker"],MALE)
        self.assertEqual(result["speech_rate"],8)
        self.assertEqual(result["basis"],"source_audio_and_content")
        prompt=client.chat_json.call_args.args[0][0]["content"][0]["text"]
        self.assertIn(MALE,prompt)
        self.assertIn("声区",prompt)
        self.assertNotIn("object_url",prompt)

    async def test_without_trustworthy_voice_content_matching_is_explicit(self):
        context=timeline_fixture.snapshot()
        settings=SimpleNamespace(llm_mock_mode=False,llm_api_key="test-key",llm_vision_model="vision")
        for observation in ({"state":"content_only"},{"state":"measured","mixed_audio":True}):
            client=MagicMock();client.chat_json=AsyncMock(return_value={"speaker":MALE,"speech_rate":15,"reason":"按商品展示风格选择自然解说"})
            with patch("app.services.refinement_speakers.get_settings",return_value=settings), patch(
                "app.services.refinement_speakers.source_voice_observation",return_value=observation), patch(
                "app.services.refinement_evaluation.sampled_frames",return_value=[]):
                result=await select_source_voice(context,"新增台词","",client=client)
            self.assertEqual(result["basis"],"content_only")
            self.assertEqual(result["speech_rate"],0)

    async def test_matching_cache_reuses_the_same_source_and_goal_only(self):
        context=timeline_fixture.snapshot();cached=automatic(context,"新增台词")
        with patch("app.services.refinement_speakers.source_voice_observation") as audio, patch(
            "app.services.refinement_speakers.LLMClient") as model:
            self.assertEqual(await select_source_voice(context,"新增台词","",cached=cached),cached)
            audio.assert_not_called();model.assert_not_called()
        changed=deepcopy(context);changed["source"]["content_hash"]="d"*64
        with patch("app.services.refinement_speakers.get_settings",return_value=SimpleNamespace(llm_mock_mode=True)):
            with self.assertRaisesRegex(ValueError,"真实AI"):
                await select_source_voice(changed,"新增台词","",cached=cached)

    async def test_changing_new_scripts_invalidates_source_selection(self):
        context=timeline_fixture.snapshot()
        cached=automatic(context,"新增台词",scripts=["展示商品"])
        self.assertTrue(valid_selection(cached,context,"新增台词","",["展示商品"]))
        self.assertFalse(valid_selection(cached,context,"新增台词","",["促销活动"]))
        with patch("app.services.refinement_speakers.LLMClient") as model:
            self.assertEqual(await select_source_voice(context,"新增台词","",scripts=["展示商品"],cached=cached),cached)
            model.assert_not_called()

    async def test_invalid_ai_selection_never_silently_generates_default_audio(self):
        settings=SimpleNamespace(llm_mock_mode=False,llm_api_key="test-key",llm_vision_model="vision")
        for invalid in ({"speaker":"foreign","speech_rate":0,"reason":"原因"},
                        {"speaker":MALE,"speech_rate":True,"reason":"原因"},
                        {"speaker":MALE,"speech_rate":99,"reason":"原因"},
                        {"speaker":MALE,"speech_rate":0,"reason":""}):
            client=MagicMock();client.chat_json=AsyncMock(return_value=invalid)
            with patch("app.services.refinement_speakers.get_settings",return_value=settings), patch(
                "app.services.refinement_speakers.source_voice_observation",return_value={"state":"content_only"}), patch(
                "app.services.refinement_evaluation.sampled_frames",return_value=[]):
                with self.assertRaises(ValueError):
                    await select_source_voice(timeline_fixture.snapshot(),"新增台词","",client=client)


class SpeakerApiTest(unittest.TestCase):
    asset=api_fixture.RefinementV2ApiTest.asset
    setUp=api_fixture.RefinementV2ApiTest.setUp
    tearDown=api_fixture.RefinementV2ApiTest.tearDown
    create=api_fixture.RefinementV2ApiTest.create
    ready_plan=api_fixture.RefinementV2ApiTest.ready_plan

    def speech_plan(self):
        task=self.ready_plan(self.create(user_brief="替换商品展示画面和口播"))
        record=self.db.query(EcommerceVideoRefinementPlan).one()
        plan=deepcopy(record.plan_json)
        plan["items"][0].update(narration_strategy="tts",script="已授权的完整台词")
        record.plan_json=plan;record.plan_hash=content_hash(plan);self.db.commit()
        return task,record

    def test_authenticated_voice_options_returns_276_and_routes_before_task_id(self):
        response=self.client.get("/api/video-refinements/voice-options")
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["total"],276)
        self.assertEqual(len(response.json()["items"]),276)
        self.assertEqual(response.json()["default_mode"],"auto")

    def test_task_voice_choice_persists_and_can_return_to_automatic(self):
        task=self.create()
        response=self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":0,"tts_speaker":MALE})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["options"]["tts_speaker"],MALE)
        response=self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":1,"tts_speaker":None})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()["options"]["tts_speaker"])
        response=self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":2,"tts_speaker":"foreign"})
        self.assertEqual(response.status_code,422,response.text)

    def test_manual_voice_skips_matching_and_does_not_reuse_another_speaker(self):
        task,record=self.speech_plan()
        with patch("app.services.refinement_speakers.select_source_voice",new_callable=AsyncMock) as matcher:
            for speaker in (MALE,FEMALE,MALE):
                plan=deepcopy(record.plan_json);plan["items"][0]["tts_speaker"]=speaker
                record.plan_json=plan;record.plan_hash=content_hash(plan);self.db.commit()
                response=self.client.post(f"/api/video-refinements/{task['id']}/voiceovers",json={"revision":0,"plan_hash":record.plan_hash,"item_id":"visual"})
                self.assertEqual(response.status_code,202,response.text)
            matcher.assert_not_awaited()
        rows=self.db.query(EcommerceVideoRefinementVoiceover).all()
        self.assertEqual(len(rows),2)
        self.assertEqual({r.parameters_json["speaker"] for r in rows},{MALE,FEMALE})

    def test_auto_voice_matches_and_discards_stale_draft_before_queueing(self):
        task,record=self.speech_plan()
        async def change_goal(context,brief,expected,**kwargs):
            response=self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":0,"user_brief":"新的目标"})
            self.assertEqual(response.status_code,200,response.text)
            return automatic(context,brief,expected)
        with patch("app.services.refinement_speakers.select_source_voice",new=change_goal):
            response=self.client.post(f"/api/video-refinements/{task['id']}/voiceovers",json={"revision":0,"plan_hash":record.plan_hash,"item_id":"visual"})
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(self.db.query(EcommerceVideoRefinementVoiceover).count(),0)

    def test_automatic_requests_reuse_selection_from_existing_audio(self):
        task,record=self.speech_plan()
        calls=[]
        async def match(context,brief,expected,**kwargs):
            if valid_selection(kwargs.get("cached"),context,brief,expected,kwargs["scripts"]):
                return kwargs["cached"]
            calls.append(kwargs["scripts"])
            return automatic(context,brief,expected,kwargs["scripts"])
        with patch("app.services.refinement_speakers.select_source_voice",new=match):
            for _ in range(2):
                response=self.client.post(f"/api/video-refinements/{task['id']}/voiceovers",json={"revision":0,"plan_hash":record.plan_hash,"item_id":"visual"})
                self.assertEqual(response.status_code,202,response.text)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.db.query(EcommerceVideoRefinementVoiceover).count(),1)
        model=self.db.get(EcommerceVideoRefinementTask,task["id"])
        context=record.plan_json.get("analysis",{}).get("context") or task["source_snapshot"]
        self.assertIsNotNone(cached_source_selection(self.db,model,context,model.user_brief,model.expected_result,calls[0]))
        self.assertIsNone(cached_source_selection(self.db,model,context,model.user_brief,model.expected_result,["不同的新台词"]))

    def test_item_speaker_overrides_task_speaker_without_matching(self):
        task,record=self.speech_plan()
        request=self.db.query(api_fixture.EcommerceVideoRefinementRequest).one()
        request.options_json={**request.options_json,"tts_speaker":FEMALE}
        plan=deepcopy(record.plan_json);plan["items"][0]["tts_speaker"]=MALE
        record.plan_json=plan;record.plan_hash=content_hash(plan);self.db.commit()
        with patch("app.services.refinement_speakers.select_source_voice",new_callable=AsyncMock) as matcher:
            response=self.client.post(f"/api/video-refinements/{task['id']}/voiceovers",json={"revision":0,"plan_hash":record.plan_hash,"item_id":"visual"})
        self.assertEqual(response.status_code,202,response.text)
        matcher.assert_not_awaited()
        self.assertEqual(self.db.query(EcommerceVideoRefinementVoiceover).one().parameters_json["speaker"],MALE)

    def test_visual_only_automation_does_not_match_or_generate_audio(self):
        from app.services.refinement_automation import prepare_narration
        task_data=self.ready_plan(self.create());task=self.db.get(EcommerceVideoRefinementTask,task_data["id"])
        plan=deepcopy(task_data["plan"]);context=deepcopy(task_data["source_snapshot"])
        context["material_evaluation"]={"complete":True,"records":[]}
        plan["analysis"]={"context":context};plan["automation_options"]={}
        with patch("app.services.refinement_speakers.select_source_voice",new_callable=AsyncMock) as matcher, patch(
            "app.services.refinement_automation.prepare_materials",new_callable=AsyncMock,return_value=[]):
            prepare_narration(self.db,task,plan,lambda stage:None)
        matcher.assert_not_awaited()
        self.assertEqual(self.db.query(EcommerceVideoRefinementVoiceover).count(),0)

    def test_batch_uses_one_auto_voice_and_respects_task_manual_choice(self):
        from app.services.refinement_automation import prepare_narration
        task_data=self.create(user_brief="替换两句口播");task=self.db.get(EcommerceVideoRefinementTask,task_data["id"])
        context=deepcopy(task_data["source_snapshot"])
        context["material_evaluation"]={"complete":True,"records":[]}
        edits=[timeline_fixture.item("VOICE_REPLACE",item_id=f"voice-{index}",script=text,
            range={"start_ms":index*2000,"end_ms":(index+1)*2000},material_segments=[],subtitle_strategy="none")
            for index,text in enumerate(("介绍商品","说明用法"))]
        plan=compile_plan(context,edits,task.user_brief,"",manual=True)
        plan["analysis"]={"context":context};plan["automation_options"]={}
        def generate(voice_id):
            row=self.db.get(EcommerceVideoRefinementVoiceover,voice_id)
            row.status="ready";row.duration_ms=1000;row.content_hash="c"*64;row.object_url="/voice.wav"
            row.timing_json={"segments":[]};self.db.commit()
        selected=automatic(context,task.user_brief,task.expected_result,["介绍商品","说明用法"])
        with patch("app.services.refinement_speakers.select_source_voice",new_callable=AsyncMock,return_value=selected) as matcher, patch(
            "app.services.refinement_voiceover.run_voiceover",side_effect=generate), patch(
            "app.services.refinement_automation.prepare_materials",new_callable=AsyncMock,return_value=[]):
            result=prepare_narration(self.db,task,plan,lambda stage:None)
            matcher.assert_awaited_once()
            self.assertEqual(matcher.call_args.kwargs["scripts"],["介绍商品","说明用法"])
            self.assertEqual({r.parameters_json["speaker"] for r in self.db.query(EcommerceVideoRefinementVoiceover).all()},{MALE})
            self.assertEqual(result["analysis"]["voice_selection"]["speaker"],MALE)
            matcher.reset_mock()
            manual=deepcopy(plan);manual["automation_options"]["tts_speaker"]=FEMALE
            prepare_narration(self.db,task,manual,lambda stage:None)
            matcher.assert_not_awaited()
        self.assertEqual(self.db.query(EcommerceVideoRefinementVoiceover).count(),4)


if __name__ == "__main__":
    unittest.main()
