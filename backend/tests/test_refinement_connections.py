import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.responses import Response

from app.models import EcommerceVideoRefinementAnalysisJob, EcommerceVideoRefinementPreview, EcommerceVideoRefinementVoiceover
from app.services.refinement_plan import compile_plan
from app.services.refinement_queue import run_analysis, run_preview
import test_refinement_v2_api as api_tests


class RefinementConnectionsTest(unittest.TestCase):
    setUp = api_tests.RefinementV2ApiTest.setUp
    tearDown = api_tests.RefinementV2ApiTest.tearDown
    asset = api_tests.RefinementV2ApiTest.asset
    create = api_tests.RefinementV2ApiTest.create
    ready_plan = api_tests.RefinementV2ApiTest.ready_plan

    def queue(self):
        task = self.create(user_brief="增加商品使用细节")
        response = self.client.post(f"/api/video-refinements/{task['id']}/auto-preview", json={"revision": 0})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["analysis_state"]["id"]
        self.db.rollback()
        return job_id

    def test_ai_and_preparation_do_not_hold_a_connection_between_checkpoints(self):
        job_id = self.queue()

        async def analyze(context, focus, brief, expected, *, progress, batches, options):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            progress({"relevance-saved": {"score": 90}}, {"stage": "matching", "evaluated": 1})
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return compile_plan(context, [], brief, expected, manual=False)

        def prepare(db, task, plan, progress, *, checkpoint):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            progress("fitting")
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return plan

        with patch("app.services.refinement_queue.SessionLocal", self.sessions), \
             patch("app.services.refinement_analysis.analyze_source", new=analyze), \
             patch("app.services.refinement_automation.prepare_narration", side_effect=prepare):
            run_analysis(job_id)
        self.assertEqual(self.engine.pool.checkedout(), 0)
        job = self.db.get(EcommerceVideoRefinementAnalysisJob, job_id)
        self.assertEqual(job.status, "ready")
        self.assertEqual(job.batches_json["relevance-saved"]["score"], 90)

    def test_analysis_failure_persists_status_and_keeps_saved_evaluations(self):
        job_id = self.queue()

        async def analyze(context, focus, brief, expected, *, progress, **kwargs):
            progress({"relevance-saved": {"score": 90}}, {"stage": "matching", "evaluated": 1})
            self.assertEqual(self.engine.pool.checkedout(), 0)
            raise ValueError("匹配请求失败")

        with patch("app.services.refinement_queue.SessionLocal", self.sessions), \
             patch("app.services.refinement_analysis.analyze_source", new=analyze), \
             self.assertLogs("app.services.refinement_queue", level="ERROR"):
            run_analysis(job_id)
        self.assertEqual(self.engine.pool.checkedout(), 0)
        job = self.db.get(EcommerceVideoRefinementAnalysisJob, job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error_message, "匹配请求失败")
        self.assertEqual(job.batches_json["relevance-saved"]["score"], 90)

    def test_rendering_and_upload_release_database_connection(self):
        task = self.ready_plan(self.create())
        response = self.client.post(f"/api/video-refinements/{task['id']}/plans/confirm", json={
            "revision": 0, "plan_hash": task["plan_hash"], "acknowledge_risks": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(f"/api/video-refinements/{task['id']}/previews", json={"revision": 0, "plan_hash": task["plan_hash"]})
        preview_id = response.json()["previews"][0]["id"]
        self.db.rollback()

        def render(snapshot, plan, output):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            output.write_bytes(b"rendered")
            return {"passed": True}, []

        async def upload(*args):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return "/preview.mp4"

        storage = SimpleNamespace(name="aliyun_oss", upload_path=AsyncMock(side_effect=upload))
        with patch("app.services.refinement_queue.SessionLocal", self.sessions), \
             patch("app.services.refinement_queue.render_preview", side_effect=render), \
             patch("app.services.refinement_queue.verified_path"), \
             patch("app.services.refinement_queue.get_storage_by_name", return_value=storage):
            run_preview(preview_id)
        self.assertEqual(self.engine.pool.checkedout(), 0)
        self.assertEqual(self.db.get(EcommerceVideoRefinementPreview, preview_id).status, "ready")

    def test_tts_wait_releases_database_connection_and_failure_is_saved(self):
        from app.services.refinement_voiceover import run_voiceover

        task = self.create()
        row = EcommerceVideoRefinementVoiceover(
            tenant_id="t1", task_id=task["id"], revision=0, item_id="speech",
            fingerprint="test", script="新增台词", parameters_json={"speaker": "test"}, status="queued_v3",
        )
        self.db.add(row)
        self.db.commit()
        voice_id = row.id
        self.db.rollback()

        def synthesize(*args, **kwargs):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            raise ValueError("配音请求失败")

        with patch("app.services.refinement_voiceover.SessionLocal", self.sessions), \
             patch("app.services.refinement_voiceover.synthesize_speech", side_effect=synthesize):
            run_voiceover(voice_id)
        self.assertEqual(self.engine.pool.checkedout(), 0)
        voice = self.db.get(EcommerceVideoRefinementVoiceover, voice_id)
        self.assertEqual(voice.status, "failed")
        self.assertEqual(voice.error_message, "配音请求失败")

    def test_polling_endpoint_reads_only_runtime_fields_and_is_tenant_scoped(self):
        from sqlalchemy import event

        task = self.create()
        self.client.post(f"/api/video-refinements/{task['id']}/auto-preview", json={"revision": 0})
        statements = []
        def record(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            response = self.client.get(f"/api/video-refinements/{task['id']}/status")
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["analysis_state"]["status"], "queued")
        self.assertNotIn("source_snapshot", data)
        self.assertNotIn("plan", data)
        sql = "\n".join(statements)
        for column in ("source_snapshot", "snapshot_json", "batches_json", "plan_json", "recipe_json"):
            self.assertNotIn(column, sql)
        self.assertEqual(self.client.get(f"/api/video-refinements/{task['id']}/status", headers={"x-test-tenant": "t2"}).status_code, 404)


class AuditConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_database_wait_in_audit_does_not_block_event_loop(self):
        from app.main import audit_requests

        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()

        def write(*args):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            entered.set()
            self.assertTrue(release.wait(2))

        request = SimpleNamespace(method="GET", url=SimpleNamespace(path="/api/video-refinements/22"))
        with patch("app.main.trace_fields", return_value={"request_id": "test"}), \
             patch("app.main.write_request_audit", side_effect=write):
            work = asyncio.create_task(audit_requests(request, AsyncMock(return_value=Response())))
            try:
                await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), 1.5)
                await asyncio.wait_for(asyncio.sleep(.01), .2)
                self.assertFalse(work.done())
            finally:
                release.set()
                await asyncio.wait_for(work, 2)


if __name__ == "__main__":
    unittest.main()
