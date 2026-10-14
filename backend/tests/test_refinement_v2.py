from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import numpy as np

from app.schemas.video_refinement import RefinementItem
from app.services.refinement_plan import compile_plan
from app.services.refinement_media import render_preview, file_hash
from app.services.refinement_voiceover import voiceover_fingerprint
from app.services.director_workflow import _ffmpeg_executable


def snapshot():
    return {"schema_version":"video_refinement_snapshot.v2","is_mock":False,"tenant_id":"t1",
            "source":{"asset_id":1,"duration_ms":4000,"fps":30,"width":240,"height":320,"has_audio":True,"burned_subtitles":False,"has_background_music":False,"content_hash":"a"*64,"object_url":"source","storage_provider":"aliyun_oss"},
            "material_catalog":[{"asset_id":2,"duration_ms":18000,"content_hash":"b"*64,"object_url":"material","storage_provider":"aliyun_oss","burned_subtitles":False}],
            "asr_state":"completed","words":[],"transcript":[],"silence_ranges":[],"capabilities":{}}


def item(kind="VISUAL_REPLACE", **extra):
    value = {"item_id":"edit","type":kind,"range":{"start_ms":1000,"end_ms":2000},"operation":"修改画面","reason":"落实目标","material_segments":[{"asset_id":2,"source_range":{"start_ms":6000,"end_ms":7000}}],"boundary_reviewed":True,**extra}
    return RefinementItem.model_validate(value)


def voice(script="新增台词", duration=1000):
    parameters={"speaker":"default","speech_rate":0,"implementation":"v2"}
    return {"id":3,"status":"ready","script":script,"parameters":parameters,"fingerprint":voiceover_fingerprint(script,parameters),"content_hash":"c"*64,"duration_ms":duration,"object_url":"voice","storage_provider":"aliyun_oss","timing":{"segments":[{"start":0,"end":duration/1000,"text":script}]}}


class RefinementTimelineV2Test(unittest.TestCase):
    def compile(self, edits, **kw):
        return compile_plan(snapshot(),edits,"精修","",manual=False,**kw)

    def test_visual_replacement_keeps_one_continuous_original_narration(self):
        plan=self.compile([item(edit_intent="reduce_shot_share")])
        self.assertEqual(plan["summary"]["expected_duration_ms"],4000)
        self.assertEqual(len(plan["narration_track"]),1)
        self.assertEqual(plan["narration_track"][0]["source_range"],{"start_ms":0,"end_ms":4000})
        self.assertEqual(plan["video_track"][1]["source_range"],{"start_ms":6000,"end_ms":7000})
        self.assertEqual(plan["items"][0]["material_audio_policy"],"mute")
        self.assertFalse(plan["execution_blockers"])

    def test_insert_and_delete_use_original_coordinates_and_shift_once(self):
        cut=item("PACE_RECUT",item_id="cut",range={"start_ms":0,"end_ms":1000},material_segments=[],removed_ranges=[{"start_ms":0,"end_ms":500}],edit_intent="remove_content")
        inserted=item("SEGMENT_INSERT",item_id="insert",range={"start_ms":2000,"end_ms":2000},script="新增台词",narration_strategy="tts",voiceover_id=3,subtitle_strategy="regenerate")
        plan=self.compile([inserted,cut],voiceovers={3:voice()})
        self.assertEqual(plan["summary"]["expected_duration_ms"],4500)
        tts=next(r for r in plan["narration_track"] if r["source_kind"]=="tts")
        self.assertEqual(tts["output_range"],{"start_ms":1500,"end_ms":2500})
        last=plan["narration_track"][-1]
        self.assertEqual(last["source_range"],{"start_ms":2000,"end_ms":4000})
        self.assertEqual(last["output_range"],{"start_ms":2500,"end_ms":4500})
        self.assertFalse(plan["execution_blockers"])

    def test_internal_deletions_are_unioned_and_same_anchor_order_is_stable(self):
        cut=item("PACE_RECUT",range={"start_ms":0,"end_ms":4000},material_segments=[],removed_ranges=[{"start_ms":500,"end_ms":1500},{"start_ms":1000,"end_ms":2000}])
        plan=self.compile([cut])
        self.assertEqual(plan["summary"]["expected_duration_ms"],2500)
        first=item("SEGMENT_INSERT",item_id="first",range={"start_ms":0,"end_ms":0},narration_strategy="none",narration_padding_reason="无讲话商品展示")
        second=item("SEGMENT_INSERT",item_id="second",range={"start_ms":0,"end_ms":0},narration_strategy="none",narration_padding_reason="无讲话细节展示")
        plan=self.compile([first,second])
        self.assertEqual([r["item_ids"] for r in plan["video_track"][:2]],[["first"],["second"]])

    def test_cross_track_overlap_is_valid_but_voice_requires_current_full_asset(self):
        visual=item()
        speech=item("VOICE_REPLACE",item_id="speech",material_segments=[],narration_strategy="tts",script="新增台词",voiceover_id=3)
        plan=self.compile([speech,visual],voiceovers={3:voice()})
        self.assertFalse(plan["execution_blockers"])
        bad=voice(duration=1500)
        plan=self.compile([visual,speech],voiceovers={3:bad})
        self.assertIn("VOICEOVER_DURATION",[b["code"] for b in plan["execution_blockers"]])
        speech.script="修改后的文本"
        plan=self.compile([visual,speech],voiceovers={3:voice()})
        self.assertIn("VOICEOVER_REQUIRED",[b["code"] for b in plan["execution_blockers"]])

    def test_compress_expression_changes_duration_only_once(self):
        compressed=item("PACE_RECUT",range={"start_ms":1000,"end_ms":3000},edit_intent="compress_expression",script="新增台词",voiceover_id=3)
        plan=self.compile([compressed],voiceovers={3:voice()})
        self.assertEqual(plan["summary"]["duration_delta_ms"],-1000)
        self.assertEqual(plan["summary"]["expected_duration_ms"],3000)
        self.assertFalse(plan["execution_blockers"])

    def test_unresolved_requirement_and_noop_results_are_preserved(self):
        req={"requirement_id":"req1","text":"保留原配音","status":"CONFLICT","reason":"时长冲突","item_ids":[],"decision":"pending"}
        plan=self.compile([],requirements=[req])
        self.assertEqual(plan["items"],[])
        self.assertEqual(plan["requirements_resolution"][0]["status"],"CONFLICT")
        self.assertIn("REQUIREMENT_UNRESOLVED",[b["code"] for b in plan["execution_blockers"]])
        req.update(status="ALREADY_SATISFIED",reason="当前画幅一致")
        self.assertFalse(self.compile([],requirements=[req])["execution_blockers"])

    def test_bad_material_ranges_and_same_track_conflicts_fail(self):
        with self.assertRaisesRegex(ValueError,"超过"):
            self.compile([item(material_segments=[{"asset_id":2,"source_range":{"start_ms":17000,"end_ms":19000}}])])
        with self.assertRaisesRegex(ValueError,"不能重叠"):
            self.compile([item(),item(item_id="other")])
        plan=self.compile([item(material_segments=[{"asset_id":2,"source_range":{"start_ms":6000,"end_ms":6500}}])])
        self.assertIn("MATERIAL_TOO_SHORT",[b["code"] for b in plan["execution_blockers"]])

    def test_speech_cuts_require_asr_or_explicit_boundary_review(self):
        snap=snapshot();snap["asr_state"]="unconfigured"
        cut=item("PACE_RECUT",material_segments=[],removed_ranges=[{"start_ms":1000,"end_ms":1500}],boundary_reviewed=False)
        plan=compile_plan(snap,[cut],"删除停顿","",manual=False)
        self.assertIn("BOUNDARY_REVIEW_REQUIRED",[b["code"] for b in plan["execution_blockers"]])


class RefinementIndependentMediaTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="refinement-v2-media-")
        self.root=Path(self.temp.name)
        self.paths={"source":self.root/"source.mp4","material":self.root/"material.mp4","voice":self.root/"voice.wav"}
        for key,color,freq in [("source","red",440),("material","green",880)]:
            subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i",f"color={color}:s=240x320:r=30:d=4","-f","lavfi","-i",f"sine=frequency={freq}:duration=4","-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(self.paths[key])],capture_output=True,check=True,timeout=60)
        subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","sine=frequency=660:duration=1","-ar","48000","-ac","2",str(self.paths["voice"])],capture_output=True,check=True,timeout=60)
        self.snap=snapshot();self.snap["material_catalog"][0]["duration_ms"]=4000
        for descriptor in [self.snap["source"],*self.snap["material_catalog"]]:
            descriptor["content_hash"]=file_hash(self.paths[descriptor["object_url"]])
        self.storage=SimpleNamespace(local_path=lambda url:self.paths[url])
        self.patch=patch("app.services.refinement_media.get_storage_by_name",return_value=self.storage);self.patch.start();self.addCleanup(self.patch.stop)
        self.addCleanup(self.temp.cleanup)

    def frequency(self,output,start):
        pcm=subprocess.run([_ffmpeg_executable(),"-v","error","-ss",str(start),"-i",str(output),"-t","0.3","-vn","-ar","48000","-ac","1","-f","s16le","-"],capture_output=True,check=True,timeout=60).stdout
        values=np.frombuffer(pcm,dtype=np.int16).astype(float)
        return np.fft.rfftfreq(len(values),1/48000)[np.argmax(abs(np.fft.rfft(values)))]

    def test_replacement_changes_pixels_and_keeps_original_frequency(self):
        visual=item(material_segments=[{"asset_id":2,"source_range":{"start_ms":2000,"end_ms":3000}}])
        plan=compile_plan(self.snap,[visual],"替换画面","",manual=False)
        output=self.root/"replacement.mp4"
        measured,recipe=render_preview(self.snap,plan,output)
        self.assertTrue(measured["passed"])
        self.assertEqual(recipe["schema_version"],"video_refinement_plan.v2")
        self.assertAlmostEqual(self.frequency(output,1.2),440,delta=5)
        frame=subprocess.run([_ffmpeg_executable(),"-v","error","-ss","1.2","-i",str(output),"-frames:v","1","-vf","scale=1:1","-pix_fmt","rgb24","-f","rawvideo","-"],capture_output=True,check=True,timeout=60).stdout
        self.assertGreater(frame[1],frame[0]+50)
        self.assertEqual(file_hash(self.paths["source"]),self.snap["source"]["content_hash"])

    def test_insert_preserves_whole_generated_audio_and_shifts_source(self):
        generated=voice();generated["content_hash"]=file_hash(self.paths["voice"])
        insert=item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},script="新增台词",voiceover_id=3,material_segments=[{"asset_id":2,"source_range":{"start_ms":1000,"end_ms":2000}}],subtitle_strategy="none")
        plan=compile_plan(self.snap,[insert],"新增内容","",manual=False,voiceovers={3:generated})
        output=self.root/"insert.mp4"
        measured,_=render_preview(self.snap,plan,output)
        self.assertAlmostEqual(measured["duration_seconds"],5,delta=.06)
        self.assertAlmostEqual(self.frequency(output,2.2),660,delta=5)
        self.assertAlmostEqual(self.frequency(output,3.3),440,delta=5)

    def test_regenerated_subtitles_are_rendered_with_real_timing(self):
        self.snap["source"]["burned_subtitles"]=True
        self.snap["transcript"]=[{"cue_id":"cue","start_ms":1000,"end_ms":2000,"text":"可信台词"}]
        visual=item(material_segments=[{"asset_id":2,"source_range":{"start_ms":2000,"end_ms":3000}}])
        plan=compile_plan(self.snap,[visual],"保留字幕","",manual=False)
        measured,_=render_preview(self.snap,plan,self.root/"subtitle.mp4")
        self.assertTrue(measured["burned_subtitles"])
        self.assertIn("可信台词",(self.root/"refinement.ass").read_text(encoding="utf8"))

    def test_loudness_processing_preserves_the_tail_of_non_frame_aligned_narration(self):
        subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","color=green:s=240x320:r=30:d=16",
            "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(self.paths["material"])],capture_output=True,check=True,timeout=60)
        subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","sine=frequency=660:duration=15.264",
            "-ar","48000","-ac","2",str(self.paths["voice"])],capture_output=True,check=True,timeout=60)
        self.snap["material_catalog"][0].update(duration_ms=16000,content_hash=file_hash(self.paths["material"]))
        generated=voice(duration=15264);generated["content_hash"]=file_hash(self.paths["voice"])
        insert=item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},script="新增台词",voiceover_id=3,
            material_segments=[{"asset_id":2,"source_range":{"start_ms":0,"end_ms":15267}}],subtitle_strategy="none")
        plan=compile_plan(self.snap,[insert],"补充展示并生成配音","",manual=False,voiceovers={3:generated})
        output=self.root/"long-narration.mp4"
        measured,_=render_preview(self.snap,plan,output)
        self.assertTrue(measured["passed"])
        self.assertAlmostEqual(measured["duration_seconds"],19.267,delta=.06)
        self.assertEqual((self.root/"audio-1.pcm").stat().st_size,15264*48*4)
        self.assertAlmostEqual(self.frequency(output,16.8),660,delta=5)
        self.assertAlmostEqual(self.frequency(output,17.4),440,delta=5)

    def test_task_21_equivalent_generates_278_inserted_frames_and_full_audio(self):
        from app.services.refinement_automation import prepare_materials
        for key, spec in (("source", "color=red:s=240x320:r=30:d=22.78"), ("material", "color=green:s=240x320:r=30:d=6")):
            duration = "22.78" if key == "source" else "6"
            subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i",spec,"-f","lavfi","-i",f"sine=frequency=440:duration={duration}",
                "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(self.paths[key])],capture_output=True,check=True,timeout=60)
        subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","sine=frequency=660:duration=9.24",
            "-ar","48000","-ac","2",str(self.paths["voice"])],capture_output=True,check=True,timeout=60)
        self.snap["source"].update(duration_ms=22780,content_hash=file_hash(self.paths["source"]))
        material = self.snap["material_catalog"][0]
        material.update(asset_id=254,duration_ms=6000,content_hash=file_hash(self.paths["material"]))
        self.snap["material_catalog"].append({**material,"asset_id":205})
        edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},narration_strategy="tts",script="新增台词",voiceover_id=3,subtitle_strategy="none",
            material_segments=[{"asset_id":254,"source_range":{"start_ms":0,"end_ms":2533}},
                               {"asset_id":254,"source_range":{"start_ms":2567,"end_ms":5300}},
                               {"asset_id":205,"source_range":{"start_ms":0,"end_ms":4033}}])
        client = MagicMock(chat_json=AsyncMock(return_value={"usable":True,"burned_subtitles":False}))
        with patch("app.services.refinement_evaluation.sampled_frames",return_value=[]):
            adjustments = asyncio.run(prepare_materials(client,self.snap,[edit],[],{},lengths={"edit":9267},frame_budgets={"edit":278}))
        generated = voice(duration=9240);generated["content_hash"] = file_hash(self.paths["voice"])
        plan = compile_plan(self.snap,[edit],"新增讲解","",manual=False,voiceovers={3:generated})
        self.assertFalse(plan["execution_blockers"])
        self.assertEqual(adjustments[0]["after_range"]["end_ms"],4000)
        measured,_ = render_preview(self.snap,plan,self.root/"task-21-equivalent.mp4")
        self.assertTrue(measured["decode_checked"])
        self.assertEqual(measured["decoded_frames"],961)
        self.assertEqual((self.root/"audio-1.pcm").stat().st_size,9240*48*4)
        with wave.open(str(self.root/"narration.wav")) as stream:
            stream.setpos(2000*48 + 9230*48)
            tail = np.frombuffer(stream.readframes(10*48),dtype=np.int16)
        self.assertGreater(np.max(np.abs(tail)),100)

    def test_a_single_extra_decoded_tts_sample_is_never_silently_trimmed(self):
        with wave.open(str(self.paths["voice"]),"rb") as stream:
            params, pcm = stream.getparams(), stream.readframes(stream.getnframes())
        with wave.open(str(self.paths["voice"]),"wb") as stream:
            stream.setparams(params)
            stream.writeframes(pcm + pcm[-4:])
        generated = voice();generated["content_hash"] = file_hash(self.paths["voice"])
        edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},narration_strategy="tts",script="新增台词",voiceover_id=3,subtitle_strategy="none",
                    material_segments=[{"asset_id":2,"source_range":{"start_ms":0,"end_ms":1000}}])
        plan = compile_plan(self.snap,[edit],"新增台词","",manual=False,voiceovers={3:generated})
        self.assertFalse(plan["execution_blockers"])
        with self.assertRaisesRegex(ValueError,"配音样本长于区间"):
            render_preview(self.snap,plan,self.root/"one-sample-too-long.mp4")

    def test_real_frame_budgets_at_other_output_rates_with_mixed_material_fps(self):
        from app.services.refinement_timing import ms_at_frame, required_frames
        subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","color=green:s=240x320:r=25:d=10",
            "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(self.paths["material"])],capture_output=True,check=True,timeout=60)
        subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i","sine=frequency=660:duration=9.24",
            "-ar","48000","-ac","2",str(self.paths["voice"])],capture_output=True,check=True,timeout=60)
        self.snap["material_catalog"][0].update(duration_ms=10000,content_hash=file_hash(self.paths["material"]))
        generated = voice(duration=9240);generated["content_hash"] = file_hash(self.paths["voice"])
        for fps in (24,25,60):
            with self.subTest(fps=fps):
                subprocess.run([_ffmpeg_executable(),"-y","-f","lavfi","-i",f"color=red:s=240x320:r={fps}:d=4",
                    "-f","lavfi","-i","sine=frequency=440:duration=4","-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(self.paths["source"])],capture_output=True,check=True,timeout=60)
                self.snap["source"].update(fps=fps,content_hash=file_hash(self.paths["source"]))
                frames = required_frames(9240,fps)
                edit = item("SEGMENT_INSERT",range={"start_ms":2000,"end_ms":2000},narration_strategy="tts",script="新增台词",voiceover_id=3,subtitle_strategy="none",
                    material_segments=[{"asset_id":2,"source_range":{"start_ms":0,"end_ms":ms_at_frame(frames,fps)}}])
                plan = compile_plan(self.snap,[edit],"新增讲解","",manual=False,voiceovers={3:generated})
                self.assertFalse(plan["execution_blockers"])
                directory = self.root / str(fps);directory.mkdir()
                measured,_ = render_preview(self.snap,plan,directory/"preview.mp4")
                self.assertEqual(measured["decoded_frames"],4*fps+frames)
                self.assertEqual((directory/"audio-1.pcm").stat().st_size,9240*48*4)


if __name__=="__main__":
    unittest.main()
