from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import wave
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.director_workflow import _ffmpeg_executable
from app.services.refinement_authorization import AUTHORIZATION_VERSION, apply_authorizations, item_authorization, manual_permissions
from app.services.refinement_media import file_hash, precise_probe, render_preview, render_recipe
from app.services.refinement_plan import compile_plan, content_hash
from app.services.refinement_selection import choose_window, normalize_candidate, locate_candidates
from app.services.refinement_timing import (frame_rate, frames_for_samples, ms_at_frame, precise_tail_allowed, sample_at_frame)
from test_refinement_v2 import item, snapshot, voice


def precise_snapshot(fps="30000/1001"):
    context = snapshot()
    context["source"].update(frame_rate=fps, video_duration="1001/250", video_duration_ms=4004, duration_ms=4004, audio_duration="1001/250", audio_offset="0")
    return context


def sampled_voice(samples=48000):
    result = voice(duration=round(samples/48))
    result.update(sample_count=samples, sample_rate=48000)
    result["timing"]["audio"] = {"sample_count":samples,"sample_rate":48000,"channels":2,"format":"pcm_s16le"}
    result["timing"]["segments"][0]["end"] = samples/48000
    return result


class PreciseTimingTest(unittest.TestCase):
    def test_minimum_global_budget_and_no_negative_tail(self):
        for fps in (24,25,30,60,"24000/1001","30000/1001"):
            for start in (0,1,29,73,121):
                for samples in (1,1600,48000,443520):
                    frames = frames_for_samples(samples, fps, start_frame=start)
                    capacity = sample_at_frame(start+frames,fps)-sample_at_frame(start,fps)
                    self.assertGreaterEqual(capacity,samples)
                    self.assertLess(sample_at_frame(start+frames-1,fps)-sample_at_frame(start,fps),samples)
                    self.assertTrue(precise_tail_allowed(capacity-samples,fps))
        self.assertFalse(precise_tail_allowed(-1,30))
        self.assertFalse(precise_tail_allowed(4800,30))
        self.assertTrue(precise_tail_allowed(4800,30,display_ms=100))
        self.assertFalse(precise_tail_allowed(4801,30,display_ms=100))

    def test_frame_graph_preserves_fractional_rate_and_real_source_origin(self):
        context = precise_snapshot()
        edits = [item(range={"start_ms":1001,"end_ms":2002},material_segments=[{"asset_id":2,"source_range":{"start_ms":6107,"end_ms":7150}}])]
        first = compile_plan(context,edits,"修改画面","",manual=False,options={"timing_protocol":"v3"})
        self.assertFalse(first["execution_blockers"])
        self.assertEqual(first["summary"]["total_frames"],120)
        self.assertEqual(first["summary"]["sample_count"],192192)
        self.assertEqual(first["video_track"][1]["source_range"]["start_ms"],6107)
        self.assertEqual(sum(c["output_frames"]["end_frame"]-c["output_frames"]["start_frame"] for c in first["video_track"]),120)
        second = compile_plan(context,edits,"修改画面","",manual=False,options={"timing_protocol":"v3"})
        self.assertEqual(content_hash(first),content_hash(second))
        self.assertEqual(context,precise_snapshot())

    def test_one_extra_sample_blocks_without_truncation_or_padding_reason_bypass(self):
        context = precise_snapshot("30")
        context["source"].update(video_duration="4",video_duration_ms=4000,duration_ms=4000,audio_duration="4")
        edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},narration_strategy="tts",script="新增台词",voiceover_id=3,
                    narration_padding_reason="不能用文字绕过样本校验",subtitle_strategy="none")
        plan = compile_plan(context,[edit],"新增讲解","",manual=False,options={"timing_protocol":"v3"},voiceovers={3:sampled_voice(48001)})
        self.assertIn("VOICEOVER_DURATION",[b["code"] for b in plan["execution_blockers"]])
        plan = compile_plan(context,[edit],"新增讲解","",manual=False,options={"timing_protocol":"v3"},voiceovers={3:voice()})
        self.assertIn("AUDIO_METADATA_REQUIRED",[b["code"] for b in plan["execution_blockers"]])

    def test_source_metadata_and_unknown_versions_are_not_guessed(self):
        with self.assertRaisesRegex(ValueError,"时间基"):
            compile_plan(snapshot(),[],"目标","",manual=False,options={"timing_protocol":"v3"})
        with self.assertRaisesRegex(ValueError,"协议"):
            compile_plan(snapshot(),[],"目标","",manual=False,options={"timing_protocol":"v4"})
        with self.assertRaisesRegex(ValueError,"版本"):
            render_recipe(snapshot(),{"schema_version":"video_refinement_plan.v4"})

    def test_original_captions_keep_trusted_sample_times_not_frame_rounded_cues(self):
        context=precise_snapshot()
        context["source"]["burned_subtitles"]=True
        context["transcript"]=[{"cue_id":"source-cue","start_ms":1010,"end_ms":1990,"text":"可信原台词"}]
        edit=item(range={"start_ms":1001,"end_ms":2002},material_segments=[{"asset_id":2,"source_range":{"start_ms":6000,"end_ms":7100}}])
        plan=compile_plan(context,[edit],"修改画面","",manual=False,options={"timing_protocol":"v3"})
        self.assertEqual(plan["subtitle_track"][0]["start_ms"],1010)
        self.assertEqual(plan["subtitle_track"][0]["end_ms"],1990)
        self.assertEqual(plan["subtitle_track"][0]["start_sample"],48480)

    def test_precise_probe_uses_pts_and_rational_not_display_float(self):
        raw = {"streams":[{"codec_type":"video","avg_frame_rate":"30000/1001","r_frame_rate":"30/1",
                            "time_base":"1/30000","duration_ts":120120,"nb_frames":"120"}]}
        with patch("app.config.get_settings",return_value=SimpleNamespace(director_ffprobe_path="ffprobe")), \
             patch("app.services.refinement_media.subprocess.run",return_value=SimpleNamespace(returncode=0,stdout=json.dumps(raw))):
            metadata = precise_probe(Path("source"))
        self.assertEqual(metadata["frame_rate"],"30000/1001")
        self.assertEqual(metadata["video_duration"],"1001/250")

    def test_display_tail_is_scene_bounded_and_not_a_voice_replacement_tolerance(self):
        context = precise_snapshot("30")
        context["source"].update(video_duration="4",video_duration_ms=4000,duration_ms=4000,audio_duration="4")
        edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},script="新增台词",narration_strategy="tts",voiceover_id=3,subtitle_strategy="none",
                    material_segments=[{"asset_id":2,"source_range":{"start_ms":6000,"end_ms":7100}}])
        options={"timing_protocol":"v3","authorization_version":AUTHORIZATION_VERSION,"display_tail_ms":100}
        plan = compile_plan(context,[edit],"新增商品展示口播","",manual=False,options=options,voiceovers={3:sampled_voice()})
        self.assertFalse(plan["execution_blockers"])
        self.assertEqual(next(c for c in plan["narration_track"] if c["source_kind"] == "tts")["padding_policy"]["max_display_ms"],100)
        options["display_tail_ms"]=0
        blocked=compile_plan(context,[edit],"新增商品展示口播","",manual=False,options=options,voiceovers={3:sampled_voice()})
        self.assertIn("VOICEOVER_DURATION",[b["code"] for b in blocked["execution_blockers"]])
        options["display_tail_ms"]=100
        edit=item("VOICE_REPLACE",range={"start_ms":1000,"end_ms":2100},material_segments=[],script="新增台词",voiceover_id=3,subtitle_strategy="none")
        blocked=compile_plan(context,[edit],"替换第一句口播","",manual=False,options=options,voiceovers={3:sampled_voice()})
        self.assertIn("VOICEOVER_DURATION",[b["code"] for b in blocked["execution_blockers"]])

    def test_requirement_reverification_is_idempotent(self):
        requirements=[{"requirement_id":"req","text":"新增台词","status":"CONFLICT","reason":"保持时长存在冲突","item_ids":[]}]
        plan=compile_plan(snapshot(),[],"新增台词","",manual=False,requirements=requirements,options={"authorization_version":AUTHORIZATION_VERSION,"localization":True})
        self.assertEqual([b["code"] for b in plan["execution_blockers"]],["REQUIREMENT_UNRESOLVED"])


class ScopedAuthorizationTest(unittest.TestCase):
    def test_saving_visual_range_does_not_authorize_unchanged_unrelated_tts(self):
        visual=item(requirement_ids=["visual"])
        speech=item("VOICE_REPLACE",item_id="speech",script="未经授权的台词",narration_strategy="tts",requirement_ids=["voice"])
        previous={"items":[visual.model_dump(),speech.model_dump()]}
        visual.range.start_ms=900
        requirements=[{"requirement_id":"visual","text":"增加商品镜头","request_quote":"增加商品镜头"},
                      {"requirement_id":"voice","text":"保持原配音","request_quote":"保持原配音"}]
        records, blockers=apply_authorizations([visual,speech],requirements,4000,brief="增加商品镜头, 保持原配音",manual=True,
                                             overrides=manual_permissions([visual,speech],previous))
        self.assertFalse(records["speech"]["allow_narration"])
        self.assertIn("EDIT_AUTHORIZATION_REQUIRED",[b["code"] for b in blockers])
        speech.script="用户显式改写后的完整台词"
        records, blockers=apply_authorizations([visual,speech],requirements,4000,brief="增加商品镜头, 保持原配音",manual=True,
                                             overrides=manual_permissions([visual,speech],previous))
        self.assertTrue(records["speech"]["allow_narration"])
    def test_a_voice_change_does_not_authorize_unrelated_shot(self):
        brief = "替换第一句口播, 增加商品展示镜头"
        requirements = [{"requirement_id":"voice","text":"替换第一句口播","request_quote":"替换第一句口播"},
                        {"requirement_id":"visual","text":"增加商品展示镜头","request_quote":"增加商品展示镜头"}]
        visual = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},narration_strategy="tts",script="不应生成",requirement_ids=["visual"])
        speech = item("VOICE_REPLACE",item_id="speech",narration_strategy="tts",script="已授权",requirement_ids=["voice"])
        records, blockers = apply_authorizations([visual,speech],requirements,4000,brief=brief)
        self.assertFalse(blockers)
        self.assertEqual(visual.type,"VISUAL_REPLACE")
        self.assertFalse(visual.script)
        self.assertFalse(records["edit"]["allow_narration"])
        self.assertTrue(records["speech"]["allow_narration"])

    def test_forged_quote_and_whole_request_cannot_expand_scope(self):
        brief = "增加第一段展示镜头, 替换第二句口播"
        for quote in (brief,"增加第一段展示镜头和新增口播"):
            requirements = [{"requirement_id":"req","text":quote,"request_quote":quote}]
            record = item_authorization(item(requirement_ids=["req"]), requirements, brief)
            self.assertFalse(record["scoped"])
            self.assertFalse(record["allow_narration"])

    def test_answers_are_scoped_and_manual_draft_is_explicit(self):
        requirements = [{"requirement_id":"first","text":"第一个镜头","decision":"answered","answer":"新增讲解配音"},
                        {"requirement_id":"second","text":"第二个镜头"}]
        record = item_authorization(item(requirement_ids=["second"]),requirements,"增加第一个镜头, 增加第二个镜头")
        self.assertFalse(record["allow_narration"])
        self.assertTrue(item_authorization(item(),[],"展示",manual=True)["allow_narration"])
        for brief in ("增加镜头, 不要重新配音", "增加镜头, 不增加口播"):
            edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},script="擅自撰写",narration_strategy="tts")
            apply_authorizations([edit],[],4000,brief=brief)
            self.assertEqual(edit.type,"VISUAL_REPLACE")
            self.assertFalse(edit.script)


class LocalSelectionTest(unittest.TestCase):
    def candidate(self, kind="stable"):
        return {"content_kind":kind,"safe_range":{"start_ms":3000,"end_ms":9000},"preferred_range":{"start_ms":5000,"end_ms":7000}}

    def test_window_selects_middle_and_never_cuts_action_core(self):
        self.assertEqual(choose_window(self.candidate(),30,30),{"start_ms":5500,"end_ms":6500})
        self.assertIsNone(choose_window(self.candidate("action"),30,30))
        self.assertEqual(choose_window(self.candidate("action"),90,30),{"start_ms":4500,"end_ms":7500})
        self.assertIsNone(choose_window(self.candidate(),300,30))
        self.assertIsNone(choose_window(self.candidate(),30,30,[(5000,7000)]))

    def test_candidate_rejects_unverified_product_and_out_of_bounds_ranges(self):
        value = {**self.candidate(),"usable":True,"burned_subtitles":False,"action_complete":True,"product_match":{"matched":True,"evidence":"当前包装"},"reason":"中部清晰"}
        asset = {"asset_id":2,"content_hash":"a"}
        self.assertEqual(normalize_candidate(value,asset,{"start_ms":0,"end_ms":10000},[0,5000,9999],"goal")["verification"]["confidence"],"sampled_only")
        for changes in ({"burned_subtitles":True},{"product_match":{"matched":True}}, {"safe_range":{"start_ms":0,"end_ms":11000}}, {"content_kind":"action","action_complete":False}):
            with self.assertRaises(ValueError):
                normalize_candidate({**value,**changes},asset,{"start_ms":0,"end_ms":10000},[0,5000,9999],"goal")

    def test_locator_reuses_evidence_and_obeys_call_budget(self):
        context = snapshot()
        value = {**self.candidate(),"usable":True,"burned_subtitles":False,"action_complete":True,"product_match":{"matched":True,"evidence":"包装画面"},"reason":"清晰稳定"}
        client = MagicMock(chat_json=AsyncMock(side_effect=[value,{"usable":True,"burned_subtitles":False,"product_match":True,"core_complete":True}]))
        proposal = [{"asset_id":2,"source_range":{"start_ms":0,"end_ms":10000}}]
        stats = {}
        with patch("app.services.refinement_evaluation.sampled_frames",return_value=[]):
            first = __import__("asyncio").run(locate_candidates(client,context,item(),proposal,stats,"vision"))
            second = __import__("asyncio").run(locate_candidates(client,context,item(),proposal,stats,"vision"))
            self.assertEqual(first,second)
            self.assertEqual(client.chat_json.await_count,2)
            self.assertEqual(stats["locator_cache_hits"],1)
            context["localization_cache"] = {}
            self.assertFalse(__import__("asyncio").run(locate_candidates(client,context,item(),proposal,{"locator_calls":12},"vision")))
            self.assertEqual(client.chat_json.await_count,2)

    def test_prepare_and_compile_bind_a_verified_middle_window(self):
        from app.services.refinement_automation import prepare_materials
        context = precise_snapshot()
        value = {**self.candidate(),"usable":True,"burned_subtitles":False,"action_complete":True,
                 "product_match":{"matched":True,"evidence":"包装画面"},"reason":"中部清晰稳定"}
        client = MagicMock(chat_json=AsyncMock(side_effect=[value,{"usable":True,"burned_subtitles":False,"product_match":True,"core_complete":True}]))
        edit = item(material_segments=[{"asset_id":2,"source_range":{"start_ms":0,"end_ms":10000}}])
        options = {"timing_protocol":"v3","localization":True}
        with patch("app.services.refinement_evaluation.sampled_frames",return_value=[]), \
             patch("app.services.refinement_automation.get_settings",return_value=SimpleNamespace(llm_vision_model="vision")):
            __import__("asyncio").run(prepare_materials(client,context,[edit],[],{},options=options))
        self.assertGreater(edit.material_segments[0].source_range.start_ms,5000)
        plan = compile_plan(context,[edit],"修改画面","",manual=False,options=options)
        self.assertFalse(plan["execution_blockers"])
        self.assertEqual(len(plan["local_selection"]),1)
        edit.material_segments[0].source_range.start_ms = 0
        invalid = compile_plan(context,[edit],"修改画面","",manual=False,options=options)
        self.assertIn("CONTENT_REVIEW_REQUIRED",[b["code"] for b in invalid["execution_blockers"]])

    def test_cancel_check_prevents_the_next_locator_call(self):
        from app.services.refinement_queue import AnalysisSuperseded
        client = MagicMock(chat_json=AsyncMock())
        def cancelled(stage):
            raise AnalysisSuperseded()
        with self.assertRaises(AnalysisSuperseded), patch("app.services.refinement_evaluation.sampled_frames",return_value=[]):
            __import__("asyncio").run(locate_candidates(client,snapshot(),item(),[{"asset_id":2,"source_range":{"start_ms":0,"end_ms":10000}}],{},"vision",cancelled))
        client.chat_json.assert_not_called()


class PreciseRealMediaTest(unittest.TestCase):
    def test_late_first_material_frame_outside_the_range_is_not_used(self):
        with tempfile.TemporaryDirectory(prefix="refinement-v3-late-frame-") as directory:
            root=Path(directory)
            paths={"source":root/"source.mp4","material":root/"material.mp4"}
            for key,fps in (("source",30),("material",5)):
                subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i",f"color=green:s=240x320:r={fps}:d=2",
                    "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(paths[key])],capture_output=True,check=True,timeout=60)
            context=precise_snapshot("30")
            context["source"].update(duration_ms=2000,video_duration="2",video_duration_ms=2000,has_audio=False,content_hash=file_hash(paths["source"]))
            context["material_catalog"][0].update(duration_ms=2000,content_hash=file_hash(paths["material"]))
            edit=item("SEGMENT_INSERT",range={"start_ms":0,"end_ms":0},narration_strategy="none",narration_padding_reason="无讲话展示",
                      material_segments=[{"asset_id":2,"source_range":{"start_ms":150,"end_ms":184}}])
            plan=compile_plan(context,[edit],"插入展示","",manual=False,options={"timing_protocol":"v3"})
            self.assertFalse(plan["execution_blockers"])
            with patch("app.services.refinement_media.get_storage_by_name",return_value=SimpleNamespace(local_path=lambda url:paths[url])), \
                 self.assertRaisesRegex(ValueError,"所需画面帧"):
                render_preview(context,plan,root/"preview.mp4")

    def test_fractional_rates_nonzero_tts_and_mixed_input_fps(self):
        with tempfile.TemporaryDirectory(prefix="refinement-v3-real-") as directory:
            root = Path(directory)
            paths = {"source":root/"source.mp4","material":root/"material.mp4","voice":root/"voice.wav"}
            subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","color=green:s=240x320:r=25:d=6",
                "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(paths["material"])],capture_output=True,check=True,timeout=60)
            subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","sine=frequency=660:duration=0.9999791667",
                "-ar","48000","-ac","2",str(paths["voice"])],capture_output=True,check=True,timeout=60)
            with wave.open(str(paths["voice"]),"rb") as stream:
                samples = stream.getnframes()
            generated = sampled_voice(samples);generated["content_hash"] = file_hash(paths["voice"])
            storage = SimpleNamespace(local_path=lambda url:paths[url])
            for fps in ("24","25","60","24000/1001","30000/1001"):
                with self.subTest(fps=fps), patch("app.services.refinement_media.get_storage_by_name",return_value=storage):
                    rate = frame_rate(fps)
                    count = 120
                    seconds = Fraction(count,1)/rate
                    subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i",f"color=red:s=240x320:r={fps}",
                        "-frames:v",str(count),"-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(paths["source"])],capture_output=True,check=True,timeout=60)
                    context = snapshot()
                    context["source"].update(frame_rate=fps,fps=float(rate),video_duration=str(seconds),video_duration_ms=round(seconds*1000),
                        duration_ms=round(seconds*1000),has_audio=False,content_hash=file_hash(paths["source"]))
                    context["material_catalog"][0].update(duration_ms=6000,content_hash=file_hash(paths["material"]))
                    start = 1
                    frames = frames_for_samples(samples,fps,start_frame=start)
                    edit = item("SEGMENT_INSERT",range={"start_ms":ms_at_frame(start,fps),"end_ms":ms_at_frame(start,fps)},
                        script="新增台词",narration_strategy="tts",voiceover_id=3,subtitle_strategy="none",
                        material_segments=[{"asset_id":2,"source_range":{"start_ms":2000,"end_ms":2000+ms_at_frame(frames,fps)}}])
                    plan = compile_plan(context,[edit],"新增讲解","",manual=False,options={"timing_protocol":"v3"},voiceovers={3:generated})
                    self.assertFalse(plan["execution_blockers"])
                    target = root/fps.replace("/","-");target.mkdir()
                    measured,recipe = render_preview(context,plan,target/"preview.mp4")
                    self.assertEqual(measured["decoded_frames"],count+frames)
                    self.assertEqual(measured["pcm_sample_count"],sample_at_frame(count+frames,fps))
                    self.assertEqual((target/"audio-0.pcm").stat().st_size,samples*4)
                    self.assertEqual(recipe["schema_version"],"video_refinement_plan.v3")
                    with wave.open(str(target/"narration.wav"),"rb") as stream:
                        stream.setpos(sample_at_frame(start,fps)+samples-48)
                        self.assertTrue(any(stream.readframes(48)))

    def test_vfr_material_keeps_original_narration_and_complete_tts(self):
        with tempfile.TemporaryDirectory(prefix="refinement-v3-vfr-") as directory:
            root = Path(directory)
            paths = {"source":root/"source.mp4","material":root/"material.mp4","voice":root/"voice.wav"}
            subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","color=red:s=240x320:r=30:d=4",
                "-f","lavfi","-i","sine=frequency=440:duration=4","-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",
                "-c:a","aac","-shortest",str(paths["source"])],capture_output=True,check=True,timeout=60)
            subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","color=green:s=240x320:r=25:d=6",
                "-vf","select=not(eq(mod(n\\,10)\\,0))","-fps_mode","vfr","-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",
                str(paths["material"])],capture_output=True,check=True,timeout=60)
            subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","sine=frequency=660:duration=1",
                "-ar","48000","-ac","2",str(paths["voice"])],capture_output=True,check=True,timeout=60)
            context = snapshot();context["source"].update(frame_rate="30",video_duration="4",video_duration_ms=4000,audio_duration="4",audio_offset="0")
            context["material_catalog"][0]["duration_ms"]=6000
            for asset in [context["source"],*context["material_catalog"]]:
                asset["content_hash"] = file_hash(paths[asset["object_url"]])
            generated=sampled_voice();generated["content_hash"]=file_hash(paths["voice"])
            edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},script="新增台词",voiceover_id=3,
                narration_strategy="tts",subtitle_strategy="none",material_segments=[{"asset_id":2,"source_range":{"start_ms":3000,"end_ms":4000}}])
            plan = compile_plan(context,[edit],"新增讲解","",manual=False,options={"timing_protocol":"v3"},voiceovers={3:generated})
            self.assertFalse(plan["execution_blockers"])
            with patch("app.services.refinement_media.get_storage_by_name",return_value=SimpleNamespace(local_path=lambda url:paths[url])):
                measured,_=render_preview(context,plan,root/"preview.mp4")
            self.assertEqual(measured["decoded_frames"],150)
            self.assertEqual(measured["pcm_sample_count"],240000)
            self.assertEqual((root/"audio-1.pcm").stat().st_size,48000*4)
            import numpy as np
            for start, frequency in ((1.2,440),(2.2,660),(3.3,440)):
                pcm = subprocess.run([_ffmpeg_executable(),"-v","error","-ss",str(start),"-i",str(root/"preview.mp4"),
                    "-t","0.3","-vn","-ar","48000","-ac","1","-f","s16le","-"],capture_output=True,check=True,timeout=60).stdout
                values=np.frombuffer(pcm,dtype=np.int16).astype(float)
                measured_frequency=np.fft.rfftfreq(len(values),1/48000)[np.argmax(abs(np.fft.rfft(values)))]
                self.assertAlmostEqual(measured_frequency,frequency,delta=5)
