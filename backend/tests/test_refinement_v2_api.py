from __future__ import annotations

import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from app.models import VideoAssetAnalysis, VideoRefinementRequest, VideoRefinementAnalysisJob, VideoRefinementPlan, VideoRefinementVoiceover
from app.services.refinement_plan import compile_plan, content_hash
from app.schemas.video_refinement import RefinementItem
from app.services.refinement_queue import run_analysis
from app.services.refinement_evaluation import analyze_v2
from app.services.refinement_requirements import parse_requirements
import test_video_refinement as legacy_tests


class RefinementV2ApiTest(unittest.TestCase):
    asset=legacy_tests.VideoRefinementTest.asset
    tearDown=legacy_tests.VideoRefinementTest.tearDown

    def setUp(self):
        legacy_tests.VideoRefinementTest.setUp(self)
        self.snapshot_patch.stop()
        self.ai_patch.stop()
        self.source.burned_subtitles=False
        self.material.burned_subtitles=False
        self.db.add(VideoAssetAnalysis(tenant_id="t1",asset_id=self.material.id,model="vision",status="completed",
            analysis_json={"source_hash":self.material.content_hash,"summary":"同商品画面","highlights":[{"start":0,"end":2,"description":"商品展示"}]}))
        self.db.commit()

    def create(self):
        response=self.client.post("/api/video-refinements",json={"source_asset_id":self.source.id,"user_brief":"仅替换画面, 保留原配音","duration_preference":"preserve"})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def ready_plan(self,task):
        edit=RefinementItem(item_id="visual",type="VISUAL_REPLACE",range={"start_ms":1000,"end_ms":2000},operation="替换展示",reason="商品更清楚",material_segments=[{"asset_id":self.material.id,"source_range":{"start_ms":6000,"end_ms":7000}}])
        plan=compile_plan(task["source_snapshot"],[edit],task["user_brief"],"",manual=False)
        self.db.add(VideoRefinementPlan(task_id=task["id"],revision=0,source_snapshot_id=self.db.query(__import__('app.models',fromlist=['VideoRefinementSnapshot']).VideoRefinementSnapshot).filter_by(task_id=task["id"]).one().id,plan_json=plan,plan_hash=content_hash(plan),origin="human"))
        self.db.commit()
        return self.client.get(f"/api/video-refinements/{task['id']}").json()

    def test_more_than_500_materials_are_frozen_browsable_and_tenant_scoped(self):
        from app.models import VideoAsset
        self.db.add_all([VideoAsset(tenant_id="t1",product_id=self.product.id,asset_id=f"bulk-{i}",filename=f"bulk-{i}.mp4",object_url=f"/bulk-{i}",content_hash="b"*64,duration_seconds=5,width=240,height=320,fps=30) for i in range(505)])
        self.db.commit()
        self.db.add_all([VideoAssetAnalysis(tenant_id="t1",asset_id=a.id,model="vision",status="completed",
            analysis_json={"source_hash":a.content_hash,"highlights":[{"start":0,"end":1,"description":"商品展示"}]})
            for a in self.db.query(VideoAsset).filter(VideoAsset.asset_id.like("bulk-%")).all()])
        self.db.commit()
        self.asset("foreign",tenant="t2")
        task=self.create()
        self.assertEqual(len(task["source_snapshot"]["material_catalog"]),506)
        cursor=None;seen=[]
        while True:
            page=self.client.get(f"/api/video-refinements/{task['id']}/materials",params={"limit":100,**({"cursor":cursor} if cursor else {})}).json()
            seen.extend(row["asset_id"] for row in page["items"])
            cursor=page["next_cursor"]
            if not cursor:break
        self.assertEqual(len(seen),506)
        self.assertEqual(len(set(seen)),506)
        page=self.client.get(f"/api/video-refinements/{task['id']}/materials",params={"search":"bulk-504"}).json()
        self.assertEqual(page["total"],1)
        self.assertEqual(self.client.get(f"/api/video-refinements/{task['id']}/materials",headers={"x-test-tenant":"t2"}).status_code,404)

    def test_trusted_analysis_preserves_later_highlights_and_wrong_hash_is_not_reused(self):
        highlights=[{"start":i,"end":i+1,"description":f"片段{i}"} for i in range(10)]
        self.db.add(VideoAssetAnalysis(tenant_id="t1",asset_id=self.material.id,model="vision",status="completed",analysis_json={"source_hash":self.material.content_hash,"highlights":highlights}))
        self.db.commit()
        task=self.create()
        self.assertEqual(len(task["source_snapshot"]["material_catalog"][0]["analysis"]["data"]["highlights"]),10)
        self.material.content_hash="d"*64;self.db.commit()
        task=self.create()
        self.assertEqual(task["source_snapshot"]["material_catalog"],[])
        self.assertIn("尚无可复用的已完成分析",[r["reason"] for r in task["source_snapshot"]["excluded_materials"]])

    def test_only_completed_analyses_are_available_and_pending_assets_do_not_block(self):
        unfinished = self.asset("未分析.mp4")
        failed = self.asset("分析失败.mp4")
        failed.status = "analysis_failed"
        self.db.commit()
        task = self.create()
        self.assertEqual([m["asset_id"] for m in task["source_snapshot"]["material_catalog"]],[self.material.id])
        excluded = {m["asset_id"]:m["reason"] for m in task["source_snapshot"]["excluded_materials"]}
        self.assertIn(unfinished.id,excluded)
        self.assertIn(failed.id,excluded)
        page = self.client.get(f"/api/video-refinements/{task['id']}/materials").json()
        self.assertEqual(page["total"],1)
        self.assertEqual(task["source_snapshot"]["material_scope"]["selection_policy"],"analyzed_only")

    def test_refresh_keeps_historical_snapshot_and_invalidates_old_plan(self):
        task=self.ready_plan(self.create())
        old_hash=task["snapshot_hash"]
        added = self.asset("new-material")
        self.db.add(VideoAssetAnalysis(tenant_id="t1",asset_id=added.id,model="vision",status="completed",
            analysis_json={"source_hash":added.content_hash,"highlights":[{"start":0,"end":1,"description":"新增画面"}]}))
        self.db.commit()
        refreshed=self.client.post(f"/api/video-refinements/{task['id']}/materials/refresh",json={"revision":0})
        self.assertEqual(refreshed.status_code,200,refreshed.text)
        current=refreshed.json()
        self.assertEqual(current["current_revision"],1)
        self.assertIsNone(current["plan"])
        self.assertNotEqual(current["snapshot_hash"],old_hash)
        self.assertEqual(current["source_snapshot"]["refresh_changes"]["added"],1)
        original=self.db.query(VideoRefinementRequest).filter_by(task_id=task["id"],revision=0).one()
        self.assertEqual(original.snapshot_hash,old_hash)
        self.assertEqual(len(original.snapshot_json["material_catalog"]),1)
        self.assertEqual(self.client.post(f"/api/video-refinements/{task['id']}/plans/confirm",json={"revision":0,"plan_hash":task["plan_hash"],"acknowledge_risks":True}).status_code,409)

    def test_analysis_is_durable_and_late_result_cannot_overwrite_revision(self):
        task=self.create()
        response=self.client.post(f"/api/video-refinements/{task['id']}/analyze",json={"revision":0})
        self.assertEqual(response.status_code,202,response.text)
        job_id=response.json()["analysis_state"]["id"]
        again=self.client.post(f"/api/video-refinements/{task['id']}/analyze",json={"revision":0}).json()
        self.assertEqual(again["analysis_state"]["id"],job_id)
        async def plan(snapshot,focus,brief,expected,**kwargs):
            result=compile_plan(snapshot,[],brief,expected,manual=False,requirements=[{"requirement_id":"req","text":"保持画幅","status":"ALREADY_SATISFIED","reason":"画幅一致","item_ids":[],"decision":"pending"}])
            return result
        with patch("app.services.refinement_queue.SessionLocal",self.sessions),patch("app.services.refinement_analysis.analyze_source",new=plan):
            run_analysis(job_id)
        current=self.client.get(f"/api/video-refinements/{task['id']}").json()
        self.assertEqual(current["current_revision"],1)
        self.assertEqual(current["analysis_state"]["status"],"ready")
        self.assertEqual(current["plan"]["items"],[])
        queued=self.client.post(f"/api/video-refinements/{task['id']}/analyze",json={"revision":1}).json()
        changed=self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":1,"user_brief":"新增独立要求"}).json()
        with patch("app.services.refinement_queue.SessionLocal",self.sessions),patch("app.services.refinement_analysis.analyze_source",new=plan):
            run_analysis(queued["analysis_state"]["id"])
        current=self.client.get(f"/api/video-refinements/{task['id']}").json()
        self.assertEqual(current["current_revision"],changed["current_revision"])
        self.assertIsNone(current["plan"])

    def test_recovered_analysis_runs_despite_an_abandoned_external_lock(self):
        import threading
        from app.services.refinement_queue import _loop
        task = self.create()
        response = self.client.post(f"/api/video-refinements/{task['id']}/analyze",json={"revision":0}).json()
        stop = threading.Event()
        wake = MagicMock(); wake.wait.side_effect = lambda _: stop.set()
        stale_lock = MagicMock(); stale_lock.acquire.return_value = False
        async def plan(snapshot, focus, brief, expected, **kwargs):
            stop.set()
            return compile_plan(snapshot,[],brief,expected,manual=False,requirements=[{"requirement_id":"ready","text":"已满足","status":"ALREADY_SATISFIED","reason":"已有结果","item_ids":[],"decision":"pending"}])
        with patch("app.services.refinement_queue.SessionLocal",self.sessions),patch("app.services.refinement_queue._stop",stop),patch("app.services.refinement_queue._wake",wake),patch("app.services.refinement_queue.task_lock",return_value=stale_lock,create=True),patch("app.services.refinement_analysis.analyze_source",new=plan):
            _loop()
        current = self.client.get(f"/api/video-refinements/{task['id']}").json()
        self.assertEqual(current["analysis_state"]["status"],"ready")
        self.assertEqual(current["current_revision"],1)

    def test_concurrent_workers_publish_an_analysis_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        task = self.create()
        response = self.client.post(f"/api/video-refinements/{task['id']}/analyze",json={"revision":0}).json()
        job_id = response["analysis_state"]["id"]
        barrier = Barrier(2)
        calls = []
        async def plan(snapshot, focus, brief, expected, **kwargs):
            calls.append(job_id)
            return compile_plan(snapshot,[],brief,expected,manual=False,requirements=[{"requirement_id":"ready","text":"已满足","status":"ALREADY_SATISFIED","reason":"已有结果","item_ids":[],"decision":"pending"}])
        def worker():
            barrier.wait(timeout=5)
            run_analysis(job_id)
        with patch("app.services.refinement_queue.SessionLocal",self.sessions),patch("app.services.refinement_analysis.analyze_source",new=plan),ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            for future in futures: future.result(timeout=10)
        self.assertEqual(calls,[job_id])
        self.assertEqual(self.db.query(VideoRefinementPlan).filter_by(task_id=task["id"]).count(),1)
        current = self.client.get(f"/api/video-refinements/{task['id']}").json()
        self.assertEqual(current["analysis_state"]["status"],"ready")

    def test_risk_acknowledgement_cannot_bypass_specific_requirements(self):
        task=self.ready_plan(self.create())
        plan=self.db.query(VideoRefinementPlan).filter_by(task_id=task["id"]).one()
        value=deepcopy(plan.plan_json);value["execution_blockers"]=[{"item_id":"","code":"REQUIREMENT_UNRESOLVED","message":"目标时长冲突"}]
        plan.plan_json=value;plan.plan_hash=content_hash(value);self.db.commit()
        response=self.client.post(f"/api/video-refinements/{task['id']}/plans/confirm",json={"revision":0,"plan_hash":plan.plan_hash,"acknowledge_risks":True})
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":0,"decisions":[{"requirement_id":"invented","decision":"waived"}]}).status_code,422)

    def test_historical_library_analysis_is_bound_to_completed_upload_and_reused(self):
        from datetime import datetime, timedelta
        from app.models import VideoUploadSession
        from app.services.storage import _provider_object_url
        now = datetime.utcnow()
        self.db.query(VideoAssetAnalysis).filter_by(asset_id=self.material.id).delete()
        self.material.object_url = _provider_object_url("aliyun_oss", "video-library/source/unique-object.mp4")
        self.material.content_hash = "aliyun_oss:" + "a" * 32
        self.db.add(VideoUploadSession(id="uploaded",tenant_id="t1",product_id=self.product.id,video_asset_id=self.material.id,
            filename=self.material.filename,size_bytes=100,object_key="video-library/source/unique-object.mp4",
            storage_provider="aliyun_oss",multipart_upload_id="multipart",part_size=100,part_count=1,
            status="completed",completed_at=now-timedelta(minutes=10)))
        row = VideoAssetAnalysis(tenant_id="t1",asset_id=self.material.id,model="vision",prompt_version="video-library-v3-whisper-timestamps",
            status="completed",created_at=now-timedelta(minutes=5),completed_at=now-timedelta(minutes=1),input_video_seconds=self.material.duration_seconds,
            analysis_json={"schema_version":"1.0","asset_id":self.material.asset_id,"summary":"已分析商品展示",
                           "highlights":[{"start":0,"end":2,"description":"使用过程"}]})
        self.db.add(row)
        self.db.commit()
        task = self.create()
        observation = task["source_snapshot"]["material_catalog"][0]["analysis"]
        self.assertEqual(observation["source_binding"]["method"], "completed_upload")
        self.assertEqual(observation["data"]["highlights"][0]["description"], "使用过程")
        self.db.refresh(row)
        self.assertEqual(row.analysis_json["source_hash"], self.material.content_hash)
        self.material.content_hash = "aliyun_oss:" + "b" * 32
        self.db.commit()
        changed = self.create()
        self.assertEqual(changed["source_snapshot"]["material_catalog"], [])

    def test_newer_incompatible_analysis_does_not_hide_completed_observation(self):
        self.db.add(VideoAssetAnalysis(tenant_id="t1",asset_id=self.material.id,model="vision",status="completed",
            analysis_json={"source_hash":self.material.content_hash,"highlights":[{"start":0,"end":1,"description":"可用"}]}))
        self.db.commit()
        self.db.add(VideoAssetAnalysis(tenant_id="t1",asset_id=self.material.id,model="mock",status="completed",
            analysis_json={"source_hash":self.material.content_hash,"highlights":[]}))
        self.db.commit()
        task = self.create()
        self.assertEqual(task["source_snapshot"]["material_catalog"][0]["analysis"]["model"], "vision")

    def test_analysis_created_after_request_is_reused_across_new_opinions(self):
        old_task = self.create()
        old_task["source_snapshot"]["material_catalog"][0]["analysis"] = {}
        self.assertEqual(old_task["source_snapshot"]["material_catalog"][0]["analysis"], {})
        self.db.add(VideoAssetAnalysis(tenant_id="t1",asset_id=self.material.id,model="vision",status="completed",
            analysis_json={"source_hash":self.material.content_hash,"summary":"真实商品展示",
                           "highlights":[{"start":0,"end":1,"description":"使用过程"}]}))
        self.db.commit()
        async def chat(messages, **kwargs):
            import json
            text = messages[0]["content"]
            if isinstance(text, list):
                return {"items":[],"timeline":[],"source_traits":{}}
            if text.startswith("将用户"):
                return {"requirements":[{"text":"保持现状","status":"ALREADY_SATISFIED","reason":"已经满足"}]}
            materials = json.loads(text[text.index('{"requirements"'):])["materials"]
            return {"evaluations":[{"key":m["key"],"score":50,"reason":"可用","segments":[]} for m in materials]}
        client = MagicMock(); client.chat_json = AsyncMock(side_effect=chat)
        settings = SimpleNamespace(llm_mock_mode=False,llm_api_key="test",llm_vision_model="vision")
        with patch("app.services.refinement_evaluation.SessionLocal",self.sessions),patch("app.services.refinement_evaluation.source_context",side_effect=deepcopy),patch("app.services.refinement_evaluation.LLMClient",return_value=client),patch("app.services.refinement_evaluation.get_settings",return_value=settings),patch("app.services.refinement_evaluation.sampled_frames") as frames,patch("app.services.storage.get_storage_by_name",return_value=SimpleNamespace(signed_url=lambda url:"https://media.example/source")):
            for brief in ("检查开头", "检查产品使用展示"):
                result = asyncio.run(analyze_v2(old_task["source_snapshot"],[],brief,"",options={},progress=None,batches={}))
                self.assertEqual(result["material_evaluation"]["reused_analysis"],1)
                self.assertEqual(result["material_evaluation"]["new_analysis"],0)
            frames.assert_not_called()
        self.assertEqual(old_task["source_snapshot"]["material_catalog"][0]["analysis"], {})

    def test_partial_coverage_decision_survives_replanning_but_not_goal_or_library_changes(self):
        task = self.create()
        response = self.client.post(f"/api/video-refinements/{task['id']}/analyze",json={"revision":0}).json()
        job = self.db.get(VideoRefinementAnalysisJob,response["analysis_state"]["id"])
        job.batches_json = {"successful-observation":{"score":50}}
        self.db.commit()
        accepted = self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":0,"accept_partial_evaluation":True}).json()
        self.assertTrue(accepted["options"]["accept_partial_evaluation"])
        self.client.post(f"/api/video-refinements/{task['id']}/analyze",json={"revision":1})
        current = self.db.query(VideoRefinementAnalysisJob).filter_by(task_id=task["id"],revision=1).one()
        self.assertEqual(current.batches_json,job.batches_json)
        changed = self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":1,"user_brief":"新目标"}).json()
        self.assertNotIn("accept_partial_evaluation",changed["options"])
        accepted = self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":2,"accept_partial_evaluation":True}).json()
        refreshed = self.client.post(f"/api/video-refinements/{task['id']}/materials/refresh",json={"revision":3}).json()
        self.assertNotIn("accept_partial_evaluation",refreshed["options"])

    def test_voiceover_requests_are_idempotent_and_stale_drafts_are_rejected(self):
        task=self.create()
        edit=RefinementItem(item_id="insert",type="SEGMENT_INSERT",range={"start_ms":0,"end_ms":0},operation="新增段落",reason="新增内容",script="真实台词",narration_strategy="tts",boundary_reviewed=True,material_segments=[{"asset_id":self.material.id,"source_range":{"start_ms":0,"end_ms":4000}}])
        response=self.client.patch(f"/api/video-refinements/{task['id']}/draft",json={"revision":0,"items":[edit.model_dump()]})
        self.assertEqual(response.status_code,200,response.text)
        task=response.json();request={"revision":task["current_revision"],"plan_hash":task["plan_hash"],"item_id":"insert"}
        first=self.client.post(f"/api/video-refinements/{task['id']}/voiceovers",json=request)
        second=self.client.post(f"/api/video-refinements/{task['id']}/voiceovers",json=request)
        self.assertEqual(first.status_code,202,first.text)
        self.assertEqual(first.json()["voiceovers"][0]["id"],second.json()["voiceovers"][0]["id"])
        self.assertEqual(self.db.query(VideoRefinementVoiceover).count(),1)
        self.assertEqual(self.client.get(f"/api/video-refinements/{task['id']}/voiceovers",headers={"x-test-tenant":"t2"}).status_code,404)
        request["revision"]=0
        self.assertEqual(self.client.post(f"/api/video-refinements/{task['id']}/voiceovers",json=request).status_code,409)

    def test_automatic_preview_is_idempotent_and_scoped_to_current_tenant_and_revision(self):
        task = self.create()
        url = f"/api/video-refinements/{task['id']}/auto-preview"
        first = self.client.post(url,json={"revision":0})
        second = self.client.post(url,json={"revision":0})
        self.assertEqual(first.status_code,202,first.text)
        self.assertEqual(first.json()["analysis_state"]["id"],second.json()["analysis_state"]["id"])
        job = self.db.query(VideoRefinementAnalysisJob).one()
        self.assertTrue(job.options_json["auto_preview"])
        self.assertEqual(self.client.post(url,json={"revision":1}).status_code,409)
        self.assertEqual(self.client.post(url,json={"revision":0},headers={"x-test-tenant":"t2"}).status_code,404)
        old_fingerprint = job.fingerprint
        job.status = "failed";self.db.commit()
        # Switching a failed manual analysis to automatic mode recomputes its identity.
        job.options_json = {"duration_preference":"preserve"};job.fingerprint = "manual";self.db.commit()
        self.client.post(url,json={"revision":0})
        self.db.refresh(job)
        self.assertEqual(job.status,"queued")
        self.assertEqual(job.fingerprint,old_fingerprint)

    def test_automatic_plan_is_confirmed_and_preview_queued_atomically(self):
        from app.models import VideoRefinementPreview
        task = self.create()
        queued = self.client.post(f"/api/video-refinements/{task['id']}/auto-preview",json={"revision":0}).json()
        async def analyze(context,focus,brief,expected,**kwargs):
            edit = RefinementItem(item_id="visual",type="VISUAL_REPLACE",range={"start_ms":1000,"end_ms":2000},
                operation="替换商品展示",reason="落实商品画面",confidence_level="MEDIUM",requirement_ids=["req"],
                material_segments=[{"asset_id":self.material.id,"source_range":{"start_ms":6000,"end_ms":7000}}])
            context = deepcopy(context)
            context["material_evaluation"] = {"complete":True,"records":[]}
            plan = compile_plan(context,[edit],brief,expected,manual=False,
                requirements=[{"requirement_id":"req","text":"商品展示","status":"EXECUTABLE","reason":"补充画面","item_ids":[]}])
            plan["analysis"] = {"context":context}
            plan["automation_options"] = kwargs["options"]
            return plan
        client = MagicMock();client.chat_json = AsyncMock(return_value={"usable":True,"burned_subtitles":False})
        with patch("app.services.refinement_queue.SessionLocal",self.sessions),patch("app.services.refinement_analysis.analyze_source",new=analyze),patch("app.services.refinement_automation.LLMClient",return_value=client),patch("app.services.refinement_evaluation.sampled_frames",return_value=[]):
            run_analysis(queued["analysis_state"]["id"])
            run_analysis(queued["analysis_state"]["id"])
        current = self.client.get(f"/api/video-refinements/{task['id']}").json()
        self.assertEqual(current["status"],"PLAN_CONFIRMED")
        self.assertEqual(current["current_revision"],1)
        self.assertEqual(current["confirmed_revision"],1)
        self.assertTrue(current["plan"]["automatic_preview"])
        self.assertEqual(current["previews"][0]["status"],"queued")
        again = self.client.post(f"/api/video-refinements/{task['id']}/auto-preview",json={"revision":1})
        self.assertEqual(again.status_code,202,again.text)
        self.assertEqual(self.db.query(VideoRefinementPreview).count(),1)

    def test_automatic_narration_cannot_publish_after_the_user_changes_the_goal(self):
        from app.models import VideoRefinementPreview
        task = self.create()
        queued = self.client.post(f"/api/video-refinements/{task['id']}/auto-preview",json={"revision":0}).json()
        async def analyze(context,focus,brief,expected,**kwargs):
            return compile_plan(context,[],brief,expected,manual=False)
        def prepare(db,current,plan,progress):
            changed = self.client.patch(f"/api/video-refinements/{task['id']}/requirements",json={"revision":0,"user_brief":"新的修改目标"})
            self.assertEqual(changed.status_code,200,changed.text)
            progress("voiceover")
            return {**plan,"automatic_preview":True}
        with patch("app.services.refinement_queue.SessionLocal",self.sessions),patch("app.services.refinement_analysis.analyze_source",new=analyze),patch("app.services.refinement_automation.prepare_narration",side_effect=prepare):
            run_analysis(queued["analysis_state"]["id"])
        current = self.client.get(f"/api/video-refinements/{task['id']}").json()
        self.assertEqual(current["current_revision"],1)
        self.assertEqual(current["user_brief"],"新的修改目标")
        self.assertIsNone(current["plan"])
        self.assertEqual(current["analysis_state"]["status"],"superseded")
        self.assertEqual(self.db.query(VideoRefinementPreview).count(),0)

    def test_automatic_narration_binds_measured_audio_and_reuses_the_same_asset(self):
        from app.models import VideoRefinementTask
        from app.services.refinement_automation import prepare_narration
        task_data = self.create();task = self.db.get(VideoRefinementTask,task_data["id"])
        context = deepcopy(task_data["source_snapshot"])
        context["material_evaluation"] = {"complete":True,"records":[{"asset_id":self.material.id,"status":"evaluated","score":100,
            "batches":[{"segments":[{"start_ms":0,"end_ms":5000}]}]}]}
        edit = RefinementItem(item_id="insert",type="SEGMENT_INSERT",range={"start_ms":context["source"]["duration_ms"],"end_ms":context["source"]["duration_ms"]},
            operation="补充画面",reason="展示商品",script="真实补充台词",narration_strategy="tts",subtitle_strategy="regenerate",
            material_segments=[{"asset_id":self.material.id,"source_range":{"start_ms":0,"end_ms":2000}}],requirement_ids=["req"])
        plan = compile_plan(context,[edit],task.user_brief,"",manual=False,
            requirements=[{"requirement_id":"req","text":"商品展示","status":"EXECUTABLE","reason":"商品画面需要补充","item_ids":[]}])
        plan["analysis"] = {"context":context};plan["automation_options"] = {"auto_preview":True,"accept_partial_evaluation":True}
        def generate(voice_id):
            row = self.db.get(VideoRefinementVoiceover,voice_id)
            row.status="ready";row.duration_ms=1234;row.content_hash="c"*64;row.object_url="/voice.wav"
            row.timing_json={"segments":[{"start":0,"end":1.234,"text":row.script}]};self.db.commit()
        client = MagicMock();client.chat_json = AsyncMock(return_value={"usable":True,"burned_subtitles":False})
        with patch("app.services.refinement_voiceover.run_voiceover",side_effect=generate),patch("app.services.refinement_automation.LLMClient",return_value=client),patch("app.services.refinement_evaluation.sampled_frames",return_value=[]):
            first = prepare_narration(self.db,task,plan,lambda stage:None)
            second = prepare_narration(self.db,task,plan,lambda stage:None)
        self.assertEqual(self.db.query(VideoRefinementVoiceover).count(),1)
        self.assertEqual(first["items"][0]["voiceover_id"],second["items"][0]["voiceover_id"])
        audio = next(clip for clip in first["narration_track"] if clip["source_kind"]=="tts")
        self.assertEqual(audio["source_range"],{"start_ms":0,"end_ms":1234})
        self.assertGreaterEqual(audio["output_range"]["end_ms"]-audio["output_range"]["start_ms"],1234)
        self.assertEqual(first["requirements_resolution"][0]["status"],"EXECUTABLE")
        self.assertEqual(first["requirements_resolution"][0]["reason"],"商品画面需要补充")
        self.assertTrue(first["material_evaluation"]["partial_accepted"])
        self.assertFalse(first["execution_blockers"])


class RefinementEvaluationTest(unittest.TestCase):
    def test_subtitle_alias_is_only_normalized_at_the_ai_boundary(self):
        from pydantic import ValidationError
        from app.services.refinement_evaluation import normalize_plan_item
        raw = {"item_id":"visual","type":"VISUAL_REPLACE","range":{"start_ms":1000,"end_ms":2000},
               "operation":"替换展示","reason":"商品更清楚","subtitle_strategy":"keep_source"}
        self.assertEqual(normalize_plan_item(raw).subtitle_strategy,"keep_visible")
        self.assertEqual(raw["subtitle_strategy"],"keep_source")
        with self.assertRaises(ValidationError):
            RefinementItem.model_validate(raw)
        with self.assertRaises(ValidationError):
            normalize_plan_item({**raw,"subtitle_strategy":"unknown"})

    def test_all_materials_and_all_highlight_batches_are_evaluated_without_reanalysis(self):
        from test_refinement_v2 import snapshot
        context=snapshot();context["source"]["product_name"]="商品";context["timeline"]=[]
        rows=[]
        for i in range(501):
            row=deepcopy(context["material_catalog"][0]);row["asset_id"]=i+2
            row["analysis"]={"content_hash":row["content_hash"],"model":"vision","data":{"summary":"商品展示","highlights":[{"start":n/10,"end":(n+1)/10,"description":f"区间{n}"} for n in range(13)]}}
            rows.append(row)
        context["material_catalog"]=rows;context["excluded_materials"]=[];context["material_scope"]={"total":501}
        calls=[]
        async def chat(messages,**kwargs):
            text=messages[0]["content"]
            if isinstance(text,list):return {"items":[],"timeline":[],"source_traits":{}}
            if text.startswith("将用户"):return {"requirements":[{"text":"保持现状","status":"ALREADY_SATISFIED","reason":"已经满足"}]}
            calls.append(text)
            import json
            materials=json.loads(text[text.index('{"requirements"'):])["materials"]
            return {"evaluations":[{"key":u["key"],"score":50,"reason":"有商品展示","segments":[]} for u in materials]}
        client=MagicMock();client.chat_json=AsyncMock(side_effect=chat)
        settings=SimpleNamespace(llm_mock_mode=False,llm_api_key="test",llm_vision_model="test-vision")
        with patch("app.services.refinement_evaluation.source_context",return_value=context),patch("app.services.refinement_evaluation.LLMClient",return_value=client),patch("app.services.refinement_evaluation.get_settings",return_value=settings),patch("app.services.storage.get_storage_by_name",return_value=SimpleNamespace(signed_url=lambda url:"https://media.example/source")),patch("app.services.refinement_evaluation.sampled_frames") as frames:
            batches={}
            result=asyncio.run(analyze_v2(context,[],"保持现状","",options={},progress=lambda b,s:batches.update(b),batches={}))
            self.assertEqual(result["material_evaluation"]["evaluated"],501)
            self.assertEqual(result["material_evaluation"]["reused_analysis"],501)
            self.assertEqual(result["material_evaluation"]["new_analysis"],0)
            self.assertEqual(len(calls),51)
            self.assertIn("区间12",calls[0])
            frames.assert_not_called()
            calls.clear()
            asyncio.run(analyze_v2(context,[],"保持现状","",options={},progress=None,batches=batches))
            self.assertEqual(len(calls),0)

    def test_partial_coverage_keeps_failed_record_and_does_not_repeat_successful_batches(self):
        from test_refinement_v2 import snapshot
        import json
        context = snapshot()
        context["source"]["product_name"] = "商品"
        context["material_catalog"] = []
        for i in range(21):
            context["material_catalog"].append({"asset_id":i+2,"duration_ms":1000,"content_hash":"b"*64,"object_url":"material",
                "analysis":{"data":{"summary":"商品展示","highlights":[{"start":0,"end":1,"description":"使用"}]}}})
        attempts = []
        async def chat(messages, **kwargs):
            text = messages[0]["content"]
            if isinstance(text, list):return {"items":[],"timeline":[],"source_traits":{}}
            if text.startswith("将用户"):return {"requirements":[{"text":"保持","status":"ALREADY_SATISFIED","reason":"符合"}]}
            materials = json.loads(text[text.index('{"requirements"'):])["materials"]
            attempts.append([m["asset_id"] for m in materials])
            if any(m["asset_id"]==22 for m in materials):raise ValueError("这一条未完成")
            return {"evaluations":[{"key":m["key"],"score":50,"reason":"符合","segments":[]} for m in materials]}
        client = MagicMock();client.chat_json = AsyncMock(side_effect=chat)
        settings = SimpleNamespace(llm_mock_mode=False,llm_api_key="test",llm_vision_model="vision")
        saved = {}
        with patch("app.services.refinement_evaluation.source_context",return_value=context),patch("app.services.refinement_evaluation.LLMClient",return_value=client),patch("app.services.refinement_evaluation.get_settings",return_value=settings),patch("app.services.storage.get_storage_by_name",return_value=SimpleNamespace(signed_url=lambda url:"https://media.example/source")):
            first = asyncio.run(analyze_v2(context,[],"保持","",options={},progress=lambda b,s:saved.update(b),batches={}))
            self.assertIn("EVALUATION_INCOMPLETE",[b["code"] for b in first["execution_blockers"]])
            attempts.clear()
            accepted = asyncio.run(analyze_v2(context,[],"保持","",options={"accept_partial_evaluation":True},progress=None,batches=saved))
            self.assertEqual(attempts,[[22],[22]])
            self.assertEqual(accepted["material_evaluation"]["evaluated"],20)
            self.assertEqual(accepted["material_evaluation"]["failed"],1)
            self.assertTrue(accepted["material_evaluation"]["partial_accepted"])
            self.assertFalse(accepted["material_evaluation"]["complete"])
            self.assertNotIn("EVALUATION_INCOMPLETE",[b["code"] for b in accepted["execution_blockers"]])

    def test_one_invalid_evaluation_retries_only_that_unit_and_uses_frozen_highlight_times(self):
        from test_refinement_v2 import snapshot
        import json
        context = snapshot();context["source"]["product_name"] = "商品"
        context["material_catalog"] = [{"asset_id":i+2,"duration_ms":1000,"content_hash":"b"*64,"object_url":"material",
            "analysis":{"data":{"summary":"展示","highlights":[{"start":.1,"end":.8,"description":"使用"}]}}} for i in range(20)]
        attempts = []
        async def chat(messages, **kwargs):
            text = messages[0]["content"]
            if isinstance(text,list):return {"items":[],"timeline":[],"source_traits":{}}
            if text.startswith("将用户"):return {"requirements":[{"text":"保持","status":"ALREADY_SATISFIED","reason":"符合"}]}
            materials = json.loads(text[text.index('{"requirements"'):])["materials"]
            attempts.append([m["asset_id"] for m in materials])
            return {"evaluations":[{"key":m["key"],"score":80,"reason":"使用过程",
                "segments":[{"highlight_index":99 if len(materials)>1 and m["asset_id"]==2 else 0,"reason":"片段"}]} for m in materials]}
        client = MagicMock();client.chat_json = AsyncMock(side_effect=chat)
        settings = SimpleNamespace(llm_mock_mode=False,llm_api_key="test",llm_vision_model="vision")
        saved = {}
        with patch("app.services.refinement_evaluation.source_context",return_value=context),patch("app.services.refinement_evaluation.LLMClient",return_value=client),patch("app.services.refinement_evaluation.get_settings",return_value=settings),patch("app.services.storage.get_storage_by_name",return_value=SimpleNamespace(signed_url=lambda url:"https://media.example/source")):
            result = asyncio.run(analyze_v2(context,[],"保持","",options={},progress=lambda b,s:saved.update(b),batches={}))
        self.assertEqual(len(attempts[0]),20)
        self.assertEqual(attempts[1],[2])
        self.assertEqual(result["material_evaluation"]["evaluated"],20)
        self.assertEqual(result["material_evaluation"]["failed"],0)
        record = result["material_evaluation"]["records"][0]["batches"][0]
        self.assertEqual(record["segments"],[{"start_ms":100,"end_ms":800,"reason":"片段"}])
        self.assertEqual(len([k for k in saved if k.startswith("relevance-")]),20)

    def test_real_model_field_variants_compile_without_repeating_the_planning_call(self):
        from test_refinement_v2 import snapshot
        import hashlib
        import json
        context = snapshot();context["source"]["product_name"] = "商品"
        context["material_catalog"][0]["analysis"] = {"data":{"summary":"商品展示","highlights":[{"start":6,"end":7,"description":"特写"}]}}
        rid = "req-" + hashlib.sha256("0:替换画面".encode()).hexdigest()[:16]
        planning_calls = []
        async def chat(messages, **kwargs):
            text = messages[0]["content"]
            if isinstance(text,list):
                if text[0]["text"].startswith("核对"):
                    return {"usable":True,"reason":"画面一致","burned_subtitles":False,"lip_visible":False}
                planning_calls.append(True)
                return {"timeline":[],"source_traits":{},"items":[{"item_id":"visual","type":"VISUAL_REPLACE","range":{"start_ms":1000,"end_ms":2000},
                    "operation":"替换展示","reason":"商品更清楚","evidence":"material-2","confidence_level":"high","edit_intent":"补充产品特写","subtitle_strategy":"keep_source",
                    "requirement_ids":[rid],"material_segments":[{"asset_id":2,"source_range":{"start_ms":6000,"end_ms":7000}}]}]}
            if text.startswith("将用户"):return {"requirements":[{"text":"替换画面","status":"EXECUTABLE","reason":"可替换"}]}
            materials = json.loads(text[text.index('{"requirements"'):])["materials"]
            return {"evaluations":[{"key":m["key"],"score":90,"reason":"特写","segments":[{"highlight_index":0}]} for m in materials]}
        client = MagicMock();client.chat_json = AsyncMock(side_effect=chat)
        settings = SimpleNamespace(llm_mock_mode=False,llm_api_key="test",llm_vision_model="vision")
        saved = {}
        with patch("app.services.refinement_evaluation.source_context",return_value=context),patch("app.services.refinement_evaluation.LLMClient",return_value=client),patch("app.services.refinement_evaluation.get_settings",return_value=settings),patch("app.services.refinement_evaluation.sampled_frames",return_value=[]),patch("app.services.storage.get_storage_by_name",return_value=SimpleNamespace(signed_url=lambda url:"https://media.example/source")):
            result = asyncio.run(analyze_v2(context,[],"替换画面","",options={},progress=lambda b,s:saved.update(b),batches={}))
            second = asyncio.run(analyze_v2(context,[],"替换画面","",options={},progress=None,batches=saved))
        self.assertEqual(planning_calls,[True])
        self.assertFalse(result["execution_blockers"])
        self.assertEqual(result["summary"]["expected_duration_ms"],4000)
        self.assertEqual(result["items"][0]["evidence"],["material-2"])
        self.assertEqual(result["items"][0]["confidence_level"],"HIGH")
        self.assertEqual(result["items"][0]["subtitle_strategy"],"keep_visible")
        self.assertEqual(result["items"][0]["narration_strategy"],"keep_source")
        self.assertEqual(second["video_track"],result["video_track"])

    def test_unknown_subtitle_strategy_retries_planning_and_preserves_matching(self):
        from test_refinement_v2 import snapshot
        import hashlib
        import json
        context = snapshot();context["source"]["product_name"] = "商品"
        context["material_catalog"][0]["analysis"] = {"data":{"summary":"商品展示","highlights":[{"start":6,"end":7,"description":"特写"}]}}
        rid = "req-" + hashlib.sha256("0:替换画面".encode()).hexdigest()[:16]
        planning_calls, matching_calls = [], []
        async def chat(messages, **kwargs):
            text = messages[0]["content"]
            if isinstance(text,list):
                if text[0]["text"].startswith("核对"):
                    return {"usable":True,"reason":"画面一致","burned_subtitles":False}
                planning_calls.append(True)
                return {"timeline":[],"source_traits":{},"items":[{"item_id":"visual","type":"VISUAL_REPLACE","range":{"start_ms":1000,"end_ms":2000},
                    "operation":"替换展示","reason":"商品更清楚","requirement_ids":[rid],"subtitle_strategy":"unknown" if len(planning_calls)==1 else "keep_visible",
                    "material_segments":[{"asset_id":2,"source_range":{"start_ms":6000,"end_ms":7000}}]}]}
            if text.startswith("将用户"):return {"requirements":[{"text":"替换画面","status":"EXECUTABLE","reason":"可替换"}]}
            matching_calls.append(True)
            materials = json.loads(text[text.index('{"requirements"'):])["materials"]
            return {"evaluations":[{"key":m["key"],"score":90,"reason":"特写","segments":[{"highlight_index":0}]} for m in materials]}
        client = MagicMock();client.chat_json = AsyncMock(side_effect=chat)
        settings = SimpleNamespace(llm_mock_mode=False,llm_api_key="test",llm_vision_model="vision")
        saved = {}
        def progress(batches, stats):
            saved.clear();saved.update(batches)
        with patch("app.services.refinement_evaluation.source_context",return_value=context),patch("app.services.refinement_evaluation.LLMClient",return_value=client),patch("app.services.refinement_evaluation.get_settings",return_value=settings),patch("app.services.refinement_evaluation.sampled_frames",return_value=[]),patch("app.services.storage.get_storage_by_name",return_value=SimpleNamespace(signed_url=lambda url:"https://media.example/source")):
            with self.assertRaisesRegex(ValueError,"AI 方案格式无效"):
                asyncio.run(analyze_v2(context,[],"替换画面","",options={},progress=progress,batches={}))
            self.assertEqual(len([key for key in saved if key.startswith("relevance-")]),1)
            self.assertFalse(any(key.startswith("planning-") for key in saved))
            result = asyncio.run(analyze_v2(context,[],"替换画面","",options={},progress=None,batches=saved))
        self.assertEqual(planning_calls,[True,True])
        self.assertEqual(matching_calls,[True])
        self.assertFalse(result["execution_blockers"])

    def test_adding_only_a_visual_preserves_original_narration_without_tts(self):
        from test_refinement_v2 import snapshot
        from app.services.refinement_evaluation import apply_visual_only_defaults
        context = snapshot();context["asr_state"] = "unconfigured"
        edit = RefinementItem(item_id="visual",type="SEGMENT_INSERT",range={"start_ms":1000,"end_ms":1000},
            operation="补充湿巾拿出来的画面",reason="补充使用前动作",narration_strategy="tts",script="",requirement_ids=["visual-requirement"],
            material_segments=[{"asset_id":2,"source_range":{"start_ms":6000,"end_ms":7000}}])
        requirements = [{"requirement_id":"visual-requirement","text":"补充湿巾拿出来的画面","status":"EXECUTABLE","reason":"有已分析画面","item_ids":[],"decision":"pending"}]
        apply_visual_only_defaults([edit],requirements,context["source"]["duration_ms"])
        plan = compile_plan(context,[edit],"补充湿巾拿出来的画面","",manual=False,requirements=requirements)
        self.assertFalse(plan["execution_blockers"])
        self.assertEqual(plan["summary"]["expected_duration_ms"],4000)
        self.assertEqual(plan["narration_track"][0]["source_range"],{"start_ms":0,"end_ms":4000})
        self.assertEqual(plan["items"][0]["type"],"VISUAL_REPLACE")
        self.assertEqual(plan["items"][0]["narration_strategy"],"keep_source")
        self.assertEqual(plan["items"][0]["range"],{"start_ms":1000,"end_ms":2000})
        explicit = edit.model_copy(update={"type":"SEGMENT_INSERT","narration_strategy":"tts"},deep=True)
        apply_visual_only_defaults([explicit],[{**requirements[0],"text":"增加新台词和产品使用画面"}],4000)
        self.assertEqual(explicit.type,"SEGMENT_INSERT")

    def test_full_narration_and_shorter_target_conflict_is_detected(self):
        from test_refinement_v2 import snapshot
        context=snapshot();context["transcript"]=[{"start_ms":0,"end_ms":20000,"text":"完整配音"}]
        client=MagicMock();client.chat_json=AsyncMock(return_value={"requirements":[{"text":"保留全部配音","status":"EXECUTABLE","reason":"保留"},{"text":"压缩到10秒","status":"EXECUTABLE","reason":"缩短"}]})
        result=asyncio.run(parse_requirements(client,context,"保留全部配音且不加速, 压缩到10秒","",{}))
        self.assertTrue(all(r["status"]=="CONFLICT" for r in result))


if __name__=="__main__":
    unittest.main()
