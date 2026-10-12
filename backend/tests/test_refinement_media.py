from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.video_refinement import RefinementItem
from app.services.director_workflow import _ffmpeg_executable
from app.services.refinement_analysis import analyze_source
from app.services.refinement_media import file_hash, pin_plan_media, render_preview, verified_path
from app.services.refinement_plan import compile_plan


class RefinementMediaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="refinement-real-media-test-")
        self.root = Path(self.temp.name)
        ffmpeg = _ffmpeg_executable()
        self.source = self.root / "source.mp4"
        raw_material = self.root / "raw-material.mp4"
        self.material = self.root / "rotated-material.mp4"
        for path, color, size, frequency in [(self.source, "red", "240x320", 440), (raw_material, "green", "320x240", 880)]:
            subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", f"color={color}:s={size}:r=30:d=2", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], capture_output=True, check=True, timeout=60)
        subprocess.run([ffmpeg, "-y", "-display_rotation", "90", "-i", str(raw_material), "-c", "copy", str(self.material)], capture_output=True, check=True, timeout=60)
        self.snapshot = {"is_mock": False, "source": {"asset_id": 1, "duration_ms":2000,"width":240,"height":320,"fps":30,"has_audio":True,"object_url":"source","content_hash":file_hash(self.source),"storage_provider":"aliyun_oss"},
            "timeline":[{"shot_id":"source-video"}], "material_catalog":[{"asset_id":2,"duration_ms":0,"has_audio":False,"object_url":"material","content_hash":file_hash(self.material),"storage_provider":"aliyun_oss"}],
            "capabilities":{"voice":{"reason":"需要独立配音"},"subtitles":{"reason":"需要独立字幕"}}}
        storage = SimpleNamespace(local_path=lambda url:{"source":self.source,"material":self.material}[url],signed_url=lambda _:"https://media.example/real-source.mp4")
        self.storage_patch = patch("app.services.refinement_media.get_storage_by_name", return_value=storage)
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_render_real_cut_replacement_and_insertion_preserves_orientation_and_source(self):
        items = [RefinementItem(item_id="cut",type="PACE_RECUT",range={"start_ms":0,"end_ms":500},operation="删除开头 200ms",reason="去除停顿",duration_delta_ms=-200,cut_from="start"),
            RefinementItem(item_id="replace",type="VISUAL_REPLACE",range={"start_ms":500,"end_ms":1000},operation="替换画面",reason="展示商品",material_asset_id=2,material_start_ms=100),
            RefinementItem(item_id="insert",type="SEGMENT_INSERT",range={"start_ms":1000,"end_ms":1000},operation="插入片段",reason="补充演示",material_asset_id=2,duration_delta_ms=400)]
        plan = compile_plan(self.snapshot,items,"调整节奏和画面","",manual=False)
        validation, recipe = render_preview(self.snapshot,plan,self.root/"preview.mp4")
        self.assertTrue(validation["passed"])
        self.assertTrue(validation["decode_checked"])
        self.assertTrue(validation["has_audio"])
        self.assertEqual((validation["width"],validation["height"]),(240,320))
        self.assertAlmostEqual(validation["duration_seconds"],2.2,delta=.15)
        self.assertEqual(sum(row["duration_ms"] for row in recipe),2200)
        self.assertEqual(file_hash(self.source),self.snapshot["source"]["content_hash"])
        # Decode a pixel from the replacement interval to verify actual green media.
        frame = subprocess.run([_ffmpeg_executable(),"-v","error","-ss","0.5","-i",str(self.root/"preview.mp4"),"-frames:v","1","-vf","scale=1:1","-pix_fmt","rgb24","-f","rawvideo","-"],capture_output=True,check=True,timeout=60).stdout
        self.assertGreater(frame[1],frame[0]+50)
        self.assertGreater(frame[1],frame[2]+50)

    def test_verified_media_rejects_changed_file_bytes(self):
        changed = deepcopy(self.snapshot["source"])
        changed["content_hash"] = "0"*64
        with self.assertRaisesRegex(ValueError,"内容已变化"):
            verified_path(changed)

    def test_failed_upload_etag_is_pinned_and_rendered_without_changing_library_hash(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["material_catalog"][0]["content_hash"] = "aliyun_oss:" + "B"*32 + "-2"
        marker = snapshot["material_catalog"][0]["content_hash"]
        storage = SimpleNamespace(local_path=lambda url:{"source":self.source,"material":self.material}[url],object_etag=lambda _:"b"*32+"-2")
        item = RefinementItem(item_id="insert",type="SEGMENT_INSERT",range={"start_ms":0,"end_ms":0},operation="使用分析失败素材",reason="展示商品",material_asset_id=2,duration_delta_ms=400)
        plan = compile_plan(snapshot,[item],"补充商品画面","",manual=False)
        with patch("app.services.refinement_media.get_storage_by_name",return_value=storage):
            pin_plan_media(snapshot,plan)
            validation,_ = render_preview(snapshot,plan,self.root/"etag-preview.mp4")
            self.assertTrue(validation["passed"])
            self.assertEqual(snapshot["material_catalog"][0]["content_hash"],marker)
            self.assertEqual(plan["media_verifications"][0]["file_sha256"],file_hash(self.material))
            # An unchanged upload identifier cannot hide changed local file bytes.
            self.material.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError,"内容已变化"):
                render_preview(snapshot,plan,self.root/"changed-preview.mp4")
            with self.assertRaisesRegex(ValueError,"内容已变化"):
                pin_plan_media(snapshot,plan,plan)

    def test_changed_upload_version_is_rejected_before_pinning(self):
        asset = {**self.snapshot["material_catalog"][0],"content_hash":"aliyun_oss:"+"b"*32+"-2"}
        storage = SimpleNamespace(local_path=lambda _:self.material,object_etag=lambda _:"c"*32+"-2")
        with patch("app.services.refinement_media.get_storage_by_name",return_value=storage):
            with self.assertRaisesRegex(ValueError,"上传版本已变化"):
                verified_path(asset)

    def test_real_analysis_calls_video_model_and_rejects_invented_evidence(self):
        result = {"timeline":[{"shot_id":"actual-shot","start_ms":0,"end_ms":2000,"label":"真实画面"}],"items":[{"item_id":"cut","type":"PACE_RECUT","range":{"start_ms":0,"end_ms":500},"operation":"删除停顿","reason":"开场节奏","evidence":["actual-shot"],"duration_delta_ms":-100,"cut_from":None,"script":None}]}
        client = MagicMock()
        client.chat_json = AsyncMock(return_value=result)
        settings = SimpleNamespace(llm_mock_mode=False,llm_api_key="test",llm_vision_model="vision-test")
        with patch("app.services.refinement_analysis.get_settings",return_value=settings), patch("app.services.refinement_analysis.LLMClient",return_value=client), patch("app.services.refinement_analysis._material_media",return_value=[]), patch("app.services.refinement_analysis.get_storage_by_name",return_value=SimpleNamespace(signed_url=lambda _:"https://media.example/video.mp4")):
            plan = asyncio.run(analyze_source(self.snapshot,["pace"],"压缩开场",""))
            self.assertFalse(plan["summary"]["is_mock"])
            self.assertEqual(plan["analysis"]["mode"],"full_video")
            self.assertEqual(client.chat_json.call_args.kwargs["model"],"vision-test")
            self.assertEqual(client.chat_json.call_args.args[0][0]["content"][1]["type"],"video_url")
            result["items"][0]["evidence"] = ["fabricated-shot"]
            with self.assertRaisesRegex(ValueError,"不存在的镜头"):
                asyncio.run(analyze_source(self.snapshot,["pace"],"压缩开场",""))

    def test_mock_configuration_cannot_generate_real_plan(self):
        with patch("app.services.refinement_analysis.get_settings",return_value=SimpleNamespace(llm_mock_mode=True,llm_api_key="test")):
            with self.assertRaisesRegex(ValueError,"真实 AI"):
                asyncio.run(analyze_source(self.snapshot,[],"修改画面",""))


if __name__ == "__main__":
    unittest.main()
