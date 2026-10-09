from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.refinement_automation import prepare_materials, safe_insertion_anchor
from app.services.refinement_plan import compile_plan
from app.services.transcription import transcribe_configured_media
from app.services.tts import synthesize_speech
from test_refinement_v2 import item, snapshot


class AutomaticMaterialsTest(unittest.TestCase):
    def test_anchor_uses_a_real_sentence_gap_after_frame_alignment(self):
        context = snapshot()
        context["transcript"] = [{"start_ms":0,"end_ms":2100},{"start_ms":2500,"end_ms":3700}]
        context["words"] = [{"start_ms":1900,"end_ms":2100},{"start_ms":2500,"end_ms":2700}]
        anchor = safe_insertion_anchor(context,2000)
        self.assertEqual(anchor,2167)
        self.assertFalse(any(w["start_ms"]-60 < anchor < w["end_ms"]+60 for w in context["words"]))

    def test_failed_asr_appends_without_claiming_a_manual_boundary_review(self):
        context = snapshot()
        # Real source duration is commonly between frame boundaries.
        context["source"]["duration_ms"] = 4013
        context["asr_state"] = "failed"
        anchor = safe_insertion_anchor(context,1000)
        edit = item("SEGMENT_INSERT",range={"start_ms":anchor,"end_ms":anchor},boundary_reviewed=False)
        plan = compile_plan(context,[edit],"新增画面","",manual=False)
        self.assertEqual(anchor,context["source"]["duration_ms"])
        self.assertFalse(plan["items"][0]["boundary_reviewed"])
        self.assertEqual(plan["items"][0]["range"]["start_ms"],4013)
        self.assertNotIn("BOUNDARY_REVIEW_REQUIRED",[b["code"] for b in plan["execution_blockers"]])

    def test_burned_and_unknown_subtitle_results_cannot_be_selected(self):
        context = snapshot()
        for asset_id in (3,4,5):
            asset = deepcopy(context["material_catalog"][0]);asset["asset_id"] = asset_id
            context["material_catalog"].append(asset)
        context["material_catalog"][0]["burned_subtitles"] = True
        edit = item()
        candidates = [{"asset_id":i,"segments":[{"start_ms":0,"end_ms":5000}]} for i in (2,3,4,5)]
        client = MagicMock();client.chat_json = AsyncMock(side_effect=[
            {"usable":True,"burned_subtitles":True}, {"usable":True}, {"usable":True,"burned_subtitles":False}])
        with patch("app.services.refinement_evaluation.sampled_frames",return_value=[]),patch("app.services.refinement_automation.get_settings",return_value=SimpleNamespace(llm_vision_model="vision")):
            asyncio.run(prepare_materials(client,context,[edit],candidates,{}))
        self.assertEqual([s.asset_id for s in edit.material_segments],[5])
        self.assertEqual(edit.material_audio_policy,"mute")
        self.assertEqual(client.chat_json.await_count,3)

    def test_real_segments_cover_narration_without_repeating_overlapping_footage(self):
        context = snapshot();context["asr_state"] = "failed"
        second = deepcopy(context["material_catalog"][0]);second["asset_id"] = 3
        context["material_catalog"].append(second)
        edit = item("SEGMENT_INSERT",range={"start_ms":1000,"end_ms":1000},boundary_reviewed=False,script="补充台词",
                    material_segments=[{"asset_id":2,"source_range":{"start_ms":0,"end_ms":1000}}])
        candidates = [{"asset_id":2,"segments":[{"start_ms":0,"end_ms":5000}]},
                      {"asset_id":3,"segments":[{"start_ms":0,"end_ms":5000}]}]
        client = MagicMock();client.chat_json = AsyncMock(return_value={"usable":True,"burned_subtitles":False})
        with patch("app.services.refinement_evaluation.sampled_frames",return_value=[]),patch("app.services.refinement_automation.get_settings",return_value=SimpleNamespace(llm_vision_model="vision")):
            asyncio.run(prepare_materials(client,context,[edit],candidates,{},lengths={"edit":1234}))
        total = sum(s.source_range.end_ms-s.source_range.start_ms for s in edit.material_segments)
        self.assertGreaterEqual(total,1234)
        self.assertLessEqual(total-1234,34)
        self.assertEqual([s.asset_id for s in edit.material_segments],[2,3])
        self.assertEqual(edit.range.start_ms,4000)
        self.assertEqual(edit.narration_strategy,"tts")

    def test_insufficient_clean_footage_requires_new_material_instead_of_looping(self):
        context = snapshot();edit = item("SEGMENT_INSERT",range={"start_ms":0,"end_ms":0})
        client = MagicMock();client.chat_json = AsyncMock(return_value={"usable":True,"burned_subtitles":False})
        with patch("app.services.refinement_evaluation.sampled_frames",return_value=[]),patch("app.services.refinement_automation.get_settings",return_value=SimpleNamespace(llm_vision_model="vision")):
            with self.assertRaisesRegex(ValueError,"补充无字幕商品素材"):
                asyncio.run(prepare_materials(client,context,[edit],[{"asset_id":2,"segments":[{"start_ms":6000,"end_ms":8000}]}],{},lengths={"edit":4000}))


class ConfiguredSpeechTest(unittest.TestCase):
    def test_configured_asr_extracts_audio_and_preserves_real_timestamps(self):
        client = MagicMock();client.transcribe = AsyncMock(return_value=[{"start_ms":100,"end_ms":1500,"text":"商品展示",
            "words":[{"start_ms":200,"end_ms":400,"text":"商品"}]}])
        def extract(command, **kwargs):
            Path(command[-1]).write_bytes(b"audio")
            return SimpleNamespace(returncode=0)
        with patch("app.services.llm.LLMClient",return_value=client),patch("app.services.llm.resolve_director_asr_provider",return_value="volcengine_asr"),patch("app.services.refinement_media.probe",return_value={"duration_seconds":2}),patch("app.services.transcription.subprocess.run",side_effect=extract):
            result = transcribe_configured_media(Path("source.mp4"))
        self.assertEqual(result["provider"],"volcengine_asr")
        self.assertEqual(result["segments"],[{"start":.1,"end":1.5,"text":"商品展示"}])
        self.assertEqual(result["words"],[{"start":.2,"end":.4,"text":"商品"}])
        self.assertEqual(client.transcribe.call_args.args[0].suffix,".mp3")

    def test_asr_text_without_timestamps_is_rejected(self):
        client = MagicMock();client.transcribe = AsyncMock(return_value=[{"text":"没有时间"}])
        def extract(command, **kwargs):
            Path(command[-1]).write_bytes(b"audio")
            return SimpleNamespace(returncode=0)
        with patch("app.services.llm.LLMClient",return_value=client),patch("app.services.llm.resolve_director_asr_provider",return_value="volcengine_asr"),patch("app.services.refinement_media.probe",return_value={"duration_seconds":2}),patch("app.services.transcription.subprocess.run",side_effect=extract):
            with self.assertRaisesRegex(ValueError,"可信的分句时间"):
                transcribe_configured_media(Path("source.mp4"))

    def test_tts_accepts_api_key_and_legacy_credentials_without_mixing_headers(self):
        for key, app_id, token in (("test-key","",""),("","test-app","test-token")):
            settings = SimpleNamespace(doubao_tts_api_key=key,doubao_tts_app_id=app_id,doubao_tts_access_token=token,
                                       doubao_tts_resource_id="seed-tts-2.0",doubao_tts_speaker="speaker")
            http = MagicMock();response = http.__enter__.return_value.stream.return_value.__enter__.return_value
            response.iter_lines.return_value = ['{"code":0,"data":"'+base64.b64encode(b"audio").decode()+'"}','{"code":20000000}']
            with tempfile.TemporaryDirectory() as directory,patch("app.services.tts.get_settings",return_value=settings),patch("app.services.tts.httpx.Client",return_value=http):
                output = Path(directory)/"voice.mp3"
                synthesize_speech("真实台词",output)
                self.assertEqual(output.read_bytes(),b"audio")
            headers = http.__enter__.return_value.stream.call_args.kwargs["headers"]
            self.assertEqual("X-Api-Key" in headers,bool(key))
            self.assertEqual("X-Api-App-Id" in headers,bool(app_id))
            self.assertEqual(headers["X-Api-Resource-Id"],"seed-tts-2.0")
            audio_params = http.__enter__.return_value.stream.call_args.kwargs["json"]["req_params"]["audio_params"]
            self.assertTrue(audio_params["enable_subtitle"])
            self.assertNotIn("enable_timestamp",audio_params)

    def test_missing_tts_configuration_is_actionable_and_sends_no_request(self):
        settings = SimpleNamespace(doubao_tts_api_key="",doubao_tts_app_id="",doubao_tts_access_token="")
        with patch("app.services.tts.get_settings",return_value=settings),patch("app.services.tts.httpx.Client") as http:
            with self.assertRaisesRegex(ValueError,"backend/.env"):
                synthesize_speech("真实台词",Path("unused.mp3"))
            http.assert_not_called()
