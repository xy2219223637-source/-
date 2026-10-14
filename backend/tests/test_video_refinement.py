from __future__ import annotations

import tempfile
import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.api.video_refinement import router
from app.auth import AuthContext, current_auth
from app.database import Base, get_db, migrate_refinement_workspace_schema
from app.models import EcommerceProduct, EcommerceVideoAsset, EcommerceVideoLineageSegment, EcommerceVideoRefinementPlan, EcommerceVideoRefinementPreview, EcommerceVideoRefinementSnapshot, EcommerceVideoRefinementTask, EcommerceVideoRefinementVersion
from app.models import Product, VideoAsset, VideoRefinementPlan, VideoRefinementSnapshot, VideoRefinementTask
from app.services.refinement_plan import compile_plan
from refinement_fixtures import analyze_mock


class VideoRefinementTest(unittest.TestCase):
    def setUp(self):
        self.worker_patch = patch("app.services.refinement_queue.refinement_worker_state",return_value={"status":"running","message":""})
        self.worker_patch.start()
        self.addCleanup(self.worker_patch.stop)
        async def test_ai(snapshot, focus, brief, expected):
            fixture = deepcopy(snapshot)
            fixture["timeline"] = [{"shot_id": f"mock-shot-{index+1:03d}", "start_ms": start, "end_ms": end} for index, (start, end) in enumerate([(0,5239),(5239,17768),(17768,22780)])]
            fixture["transcript"] = [{"text": "测试台词"}]
            fixture["subtitles"] = [{"text": "测试字幕"}]
            plan = compile_plan(snapshot, analyze_mock(fixture, focus, brief), brief, expected, manual=False)
            plan["analysis"] = {"model":"test-vision", "mode":"full_video", "timeline":[], "source_hash":snapshot["source"]["content_hash"]}
            return plan
        # This suite freezes the historical v1 contract; v2 has a separate suite.
        from app.services.refinement_analysis import build_snapshot as real_snapshot
        def legacy_snapshot(*args):
            value = real_snapshot(*args, analyzed_only=False)
            value["schema_version"] = "video_refinement_snapshot.v1"
            value["fixture"] = "system.v1"
            return value
        self.snapshot_patch = patch("app.api.video_refinement.build_snapshot",side_effect=legacy_snapshot)
        self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)
        self.ai_patch = patch("app.api.video_refinement.analyze_source", new=test_ai)
        self.ai_patch.start()
        self.addCleanup(self.ai_patch.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="enrui-refinement-test-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name).as_posix()}/test.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.sessions()
        self.product = EcommerceProduct(tenant_id="t1", name="国光湿巾")
        self.other = EcommerceProduct(tenant_id="t2", name="其他商品")
        self.db.add_all([self.product, self.other])
        self.db.commit()
        self.source = self.asset("国光湿巾_商品成片_001.mp4", kind="final_output")
        self.material = self.asset("同商品素材.mov")
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")

        def database():
            with self.sessions() as db:
                yield db

        def auth(x_test_tenant: str = Header(default="t1")):
            return AuthContext("owner", x_test_tenant, "owner", "test@example.com", "测试企业")

        self.app.dependency_overrides[get_db] = database
        self.app.dependency_overrides[current_auth] = auth
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def asset(self, filename, kind="material", tenant="t1", **kwargs):
        row = EcommerceVideoAsset(tenant_id=tenant, product_id=self.product.id if tenant == "t1" else self.other.id,
            asset_id=filename, filename=filename, asset_kind=kind, library_type="finished",
            object_url=f"/{filename}", content_hash="a" * 64, width=720, height=1280,
            duration_seconds=22.78, fps=30, has_audio=True, burned_subtitles=True,
            status="analyzed", **kwargs)
        self.db.add(row)
        self.db.commit()
        return row

    def create(self, focus=None):
        response = self.client.post("/api/video-refinements", json={"source_asset_id": self.source.id,
            "user_brief": "提升开头吸引力并加快使用演示", "focus": focus or []})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def analyze(self, task):
        response = self.client.post(f"/api/video-refinements/{task['id']}/analyze", json={"revision": task["current_revision"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def save(self, task, items):
        return self.client.patch(f"/api/video-refinements/{task['id']}/draft", json={"revision": task["current_revision"], "items": items})

    def confirm(self, task, **kwargs):
        return self.client.post(f"/api/video-refinements/{task['id']}/plans/confirm", json={
            "revision": task["current_revision"], "plan_hash": task["plan_hash"], "acknowledge_risks": True, **kwargs})

    def legacy_task(self):
        product = Product(tenant_id="t1", name="旧商品")
        self.db.add(product); self.db.flush()
        asset = VideoAsset(tenant_id="t1", product_id=product.id, asset_id="legacy-source",
                           filename="旧成片.mp4", asset_kind="final_output", library_type="finished",
                           object_url="/legacy.mp4", content_hash="c" * 64)
        self.db.add(asset); self.db.flush()
        task = VideoRefinementTask(tenant_id="t1", source_asset_id=asset.id, product_id=product.id,
                                   source_hash=asset.content_hash, source_snapshot={}, user_brief="旧商品精修意见", created_by="owner")
        self.db.add(task); self.db.flush()
        snapshot = VideoRefinementSnapshot(task_id=task.id, schema_version="legacy", is_mock=False,
                                           snapshot_json={}, content_hash="d" * 64)
        self.db.add(snapshot); self.db.flush()
        self.db.add(VideoRefinementPlan(task_id=task.id, revision=0, source_snapshot_id=snapshot.id,
                                       plan_json={}, plan_hash="e" * 64))
        self.db.commit()
        return task.id

    def test_legacy_review_schema_and_related_data_remain_untouched(self):
        # Reproduce an installation with the earlier review-driven repair tables.
        tables = [VideoRefinementTask.__table__, VideoRefinementSnapshot.__table__, VideoRefinementPlan.__table__]
        Base.metadata.drop_all(self.engine, tables=tables)
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE video_refinement_tasks (id INTEGER PRIMARY KEY, review_id TEXT NOT NULL, source_asset_id INTEGER REFERENCES video_assets(id))"))
            connection.execute(text("CREATE TABLE video_refinement_candidates (id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES video_refinement_tasks(id))"))
            connection.execute(text("CREATE TABLE video_refinement_snapshots (id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES video_refinement_tasks(id), snapshot_json JSON)"))
            connection.execute(text("CREATE TABLE video_refinement_plans (id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES video_refinement_tasks(id))"))
            connection.execute(text("INSERT INTO video_refinement_tasks VALUES (41, 'legacy-review', :asset)"), {"asset": self.source.id})
            connection.execute(text("INSERT INTO video_refinement_candidates VALUES (42, 41)"))
            connection.execute(text("INSERT INTO video_refinement_snapshots VALUES (43, 41, '{}')"))
            connection.execute(text("INSERT INTO video_refinement_plans VALUES (44, 41)"))
        migrate_refinement_workspace_schema(self.engine)
        Base.metadata.create_all(self.engine)
        migrate_refinement_workspace_schema(self.engine)
        self.assertEqual(self.client.get("/api/video-refinements").json(), [])
        task = self.analyze(self.create(["pace"]))
        self.assertEqual(self.confirm(task).status_code, 200)
        response = self.client.get("/api/video-refinements")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()], [task["id"]])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT review_id FROM video_refinement_tasks WHERE id = 41")).scalar(), "legacy-review")
            for name in ("video_refinement_candidates", "video_refinement_snapshots", "video_refinement_plans"):
                self.assertEqual(connection.execute(text(f"SELECT task_id FROM {name}")).scalar(), 41)
            self.assertEqual(inspect(connection).get_foreign_keys("video_refinement_candidates")[0]["referred_table"], "video_refinement_tasks")

    def test_compatible_phase1_schema_preserves_tasks_snapshots_and_plans(self):
        task = self.analyze(self.create(["pace"]))
        legacy_id = self.legacy_task()
        for suffix in ("tasks", "snapshots", "plans"):
            with self.engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE video_refinement_workspace_{suffix} RENAME TO video_refinement_{suffix}"))
        migrate_refinement_workspace_schema(self.engine)
        Base.metadata.create_all(self.engine)
        migrate_refinement_workspace_schema(self.engine)
        response = self.client.get(f"/api/video-refinements/{task['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), task)
        self.assertEqual(self.confirm(task).status_code, 200)
        self.assertEqual(self.db.query(EcommerceVideoRefinementSnapshot).count(), 1)
        self.assertEqual(self.db.query(EcommerceVideoRefinementPlan).count(), 1)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT user_brief FROM video_refinement_workspace_tasks WHERE id = :id"), {"id": legacy_id}).scalar(), "旧商品精修意见")
        names = inspect(self.engine).get_table_names()
        self.assertNotIn("video_refinement_tasks", names)
        self.assertEqual(inspect(self.engine).get_foreign_keys(EcommerceVideoRefinementPlan.__tablename__)[0]["referred_table"], EcommerceVideoRefinementTask.__tablename__)

    def test_conflicting_workspace_tables_stop_migration_without_overwriting_data(self):
        task = self.create()
        legacy_id = self.legacy_task()
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE video_refinement_tasks AS SELECT * FROM video_refinement_workspace_tasks"))
        with self.assertRaisesRegex(RuntimeError, "新旧表同时存在"):
            migrate_refinement_workspace_schema(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT id FROM video_refinement_tasks")).scalar(), legacy_id)
        self.assertEqual(self.client.get(f"/api/video-refinements/{task['id']}").json(), task)

    def test_source_picker_and_frozen_mock_fixture(self):
        self.asset("foreign.mp4", kind="final_output", tenant="t2")
        self.asset("blocked.mov", reusable_status="blocked")
        self.asset("excluded.mov", excluded=True)
        response = self.client.get("/api/video-refinements/sources")
        self.assertEqual([row["id"] for row in response.json()], [self.source.id])
        task = self.create()
        snapshot = task["source_snapshot"]
        self.assertEqual(snapshot["fixture"], "system.v1")
        self.assertFalse(snapshot["is_mock"])
        self.assertEqual(snapshot["transcript"], [])
        self.assertEqual(snapshot["source"]["aspect_ratio"], "9:16")
        self.assertEqual(snapshot["source"]["duration_ms"], 22780)
        self.assertEqual(snapshot["timeline"][-1]["end_ms"], 22780)
        self.assertEqual([row["asset_id"] for row in snapshot["material_catalog"]], [self.material.id])
        self.assertEqual(snapshot["capabilities"]["subtitles"]["state"], "HUMAN_REQUIRED")
        self.assertEqual(task["status"], "CREATED")
        self.assertEqual(task["current_revision"], 0)
        self.assertIsNone(task["plan"])
        self.assertTrue(task["preview_supported"])
        self.assertEqual(self.db.query(EcommerceVideoRefinementSnapshot).count(), 1)

    def test_empty_brief_material_and_unverified_media_are_rejected(self):
        material = self.asset("商品素材.mp4")
        for payload, status in [({"source_asset_id": self.source.id, "user_brief": "   "}, 422),
                                ({"source_asset_id": material.id, "user_brief": "修改画面"}, 404)]:
            self.assertEqual(self.client.post("/api/video-refinements", json=payload).status_code, status)
        self.source.fps = 0
        self.db.commit()
        response = self.client.post("/api/video-refinements", json={"source_asset_id": self.source.id, "user_brief": "修改画面"})
        self.assertEqual(response.status_code, 422)

    def test_visual_focus_keeps_duration_and_requires_explicit_material_mapping(self):
        task = self.analyze(self.create(["product_visibility"]))
        self.assertEqual([row["type"] for row in task["plan"]["items"]], ["VISUAL_REPLACE"])
        self.assertEqual(task["plan"]["summary"]["duration_delta_ms"], 0)
        self.assertEqual(task["status"], "MATERIAL_REQUIRED")
        self.assertIsNone(task["plan"]["items"][0]["material_asset_id"])
        self.assertEqual(self.confirm(task, acknowledge_risks=False).status_code, 422)
        self.assertEqual(self.confirm(task).json()["status"], "PLAN_CONFIRMED")

    def test_pace_toggle_recalculates_duration_and_preserves_prior_revision(self):
        task = self.analyze(self.create(["pace"]))
        self.assertEqual(task["plan"]["summary"]["duration_delta_ms"], -800)
        original_hash = task["plan_hash"]
        items = deepcopy(task["plan"]["items"])
        items[0]["enabled"] = False
        response = self.save(task, items)
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["current_revision"], 2)
        self.assertEqual(updated["plan"]["summary"]["expected_duration_ms"], 22780)
        self.assertNotEqual(updated["plan_hash"], original_hash)
        first = self.db.query(EcommerceVideoRefinementPlan).filter_by(task_id=task["id"], revision=1).one()
        self.assertEqual(first.plan_hash, original_hash)
        self.assertTrue(first.plan_json["items"][0]["enabled"])
        self.assertEqual(self.confirm(updated).status_code, 422)

    def test_voice_and_subtitle_edits_retain_execution_blockers(self):
        task = self.analyze(self.create(["voice", "subtitle"]))
        self.assertEqual(task["status"], "NEEDS_HUMAN_CONFIRMATION")
        items = deepcopy(task["plan"]["items"])
        items[0]["script"] = "人工核对后的台词"
        items[0]["risk"] = ""
        response = self.save(task, items)
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated["plan"]["items"][0]["script"], "人工核对后的台词")
        self.assertIn("混合音轨", updated["plan"]["items"][0]["risk"])
        self.assertEqual(len(updated["plan"]["execution_blockers"]), 2)
        self.assertEqual(updated["plan"]["items"][0]["status"], "HUMAN_EDITED")
        self.assertEqual(updated["plan"]["items"][1]["status"], "PROPOSED")

    def test_stale_edits_analysis_and_confirmation_cannot_overwrite_current_plan(self):
        task = self.analyze(self.create(["pace"]))
        items = deepcopy(task["plan"]["items"])
        items[0]["operation"] = "人工修改后的剪辑说明"
        next_task = self.save(task, items).json()
        self.assertEqual(self.save(task, task["plan"]["items"]).status_code, 409)
        stale_analysis = self.client.post(f"/api/video-refinements/{task['id']}/analyze", json={"revision": 1})
        self.assertEqual(stale_analysis.status_code, 409)
        self.assertEqual(self.confirm(task).status_code, 409)
        current = self.client.get(f"/api/video-refinements/{task['id']}").json()
        self.assertEqual(current["plan_hash"], next_task["plan_hash"])
        self.assertEqual(self.db.query(EcommerceVideoRefinementPlan).count(), 2)

    def test_duplicate_analysis_returns_published_plan_without_calling_ai_again(self):
        created = self.create(["pace"])
        first = self.analyze(created)
        with patch("app.api.video_refinement.analyze_source", new_callable=AsyncMock) as model:
            duplicate = self.client.post(f"/api/video-refinements/{created['id']}/analyze", json={"revision":0})
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["plan_hash"], first["plan_hash"])
        self.assertEqual(duplicate.json()["current_revision"], 1)
        model.assert_not_awaited()
        self.assertEqual(self.db.query(EcommerceVideoRefinementPlan).count(), 1)

    def test_overlapping_analysis_requests_return_one_published_revision(self):
        task = self.create(["pace"])
        from app.api.video_refinement import analyze_source
        barrier = Barrier(2)

        async def synchronized_model(*args):
            await asyncio.to_thread(barrier.wait, 10)
            return await analyze_source(*args)

        def submit(_):
            return self.client.post(f"/api/video-refinements/{task['id']}/analyze", json={"revision":0})

        with patch("app.api.video_refinement.analyze_source", new=synchronized_model), ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(submit, range(2)))
        self.assertEqual([row.status_code for row in responses], [200,200], [row.text for row in responses])
        self.assertEqual(responses[0].json()["plan_hash"], responses[1].json()["plan_hash"])
        self.assertEqual(self.db.query(EcommerceVideoRefinementPlan).count(), 1)

    def test_cancelled_task_cannot_be_restored_by_old_analysis_request(self):
        created = self.create(["pace"])
        first = self.analyze(created)
        self.client.post(f"/api/video-refinements/{created['id']}/cancel", json={"revision":1})
        response = self.client.post(f"/api/video-refinements/{created['id']}/analyze", json={"revision":0})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.client.get(f"/api/video-refinements/{created['id']}").json()["status"],"CANCELLED")
        self.assertEqual(self.db.query(EcommerceVideoRefinementPlan).count(),1)

    def test_adding_material_does_not_invalidate_existing_plan(self):
        task = self.analyze(self.create(["pace"]))
        added = self.asset("新上传素材.mov")
        added.status = "analysis_failed"
        added.content_hash = "aliyun_oss:" + "b"*32 + "-1"
        self.db.commit()
        response = self.confirm(task)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["plan_hash"],task["plan_hash"])
        self.assertNotIn(added.id,[row["asset_id"] for row in response.json()["source_snapshot"]["material_catalog"]])

    def test_selected_upload_etag_records_byte_hash_and_accepts_validation_upgrade(self):
        failed = self.asset("未完成入库校验素材.mov")
        failed.content_hash = "aliyun_oss:" + "b"*32 + "-1"
        failed.status = "analysis_failed"
        self.db.commit()
        task = self.analyze(self.create(["product_visibility"]))
        items = deepcopy(task["plan"]["items"])
        items[0]["material_asset_id"] = failed.id
        raw_hash = failed.content_hash
        media_file = Path(self.temp.name) / "failed-material.mov"
        media_file.write_bytes(b"actual failed-analysis material bytes")
        from app.services.refinement_media import file_hash
        with patch("app.services.refinement_media.verified_path", return_value=media_file):
            saved = self.save(task, items)
        self.assertEqual(saved.status_code,200,saved.text)
        plan = saved.json()
        self.assertEqual(plan["plan"]["media_verifications"],[{"asset_id":failed.id,"content_hash":raw_hash,"file_sha256":file_hash(media_file)}])
        self.db.refresh(failed)
        self.assertEqual(failed.content_hash,raw_hash)
        failed.content_hash = file_hash(media_file)
        self.db.commit()
        self.assertEqual(self.confirm(plan).status_code,200)
        failed.content_hash = "f"*64
        self.db.commit()
        response = self.client.post(f"/api/video-refinements/{plan['id']}/previews",json={"revision":plan["current_revision"],"plan_hash":plan["plan_hash"]})
        self.assertEqual(response.status_code,409,response.text)

    def test_simultaneous_drafts_only_publish_one_revision(self):
        from app.api.video_refinement import _get_task

        task = self.analyze(self.create(["pace"]))
        barrier = Barrier(2)

        def synchronized_read(*args):
            row = _get_task(*args)
            barrier.wait(timeout=10)
            return row

        def submit(label):
            items = deepcopy(task["plan"]["items"])
            items[0]["operation"] = label
            return self.save(task, items).status_code

        with patch("app.api.video_refinement._get_task", side_effect=synchronized_read), ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, ["第一个页面的修改", "第二个页面的修改"]))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(self.db.query(EcommerceVideoRefinementPlan).count(), 2)

    def test_confirmation_is_idempotent_and_new_draft_invalidates_it(self):
        task = self.analyze(self.create(["pace"]))
        self.assertEqual(self.confirm(task, plan_hash="f"*64).status_code, 409)
        first = self.confirm(task).json()
        second = self.confirm(task).json()
        self.assertEqual(first["confirmed_revision"], second["confirmed_revision"])
        items = deepcopy(task["plan"]["items"])
        items[0]["duration_delta_ms"] = -400
        changed = self.save(task, items).json()
        self.assertIsNone(changed["confirmed_revision"])
        self.assertEqual(changed["status"], "CONFIGURING")
        self.assertEqual(self.confirm(task).status_code, 409)
        self.assertEqual(self.db.query(EcommerceVideoAsset).count(), 2)
        self.db.refresh(self.source)
        self.assertEqual(self.source.content_hash, "a"*64)

    def test_source_hash_or_metadata_change_blocks_confirmation(self):
        task = self.analyze(self.create(["pace"]))
        for field, value in [("content_hash", "b"*64), ("width", 1080), ("asset_kind", "material")]:
            original = getattr(self.source, field)
            setattr(self.source, field, value)
            self.db.commit()
            self.assertEqual(self.confirm(task).status_code, 409)
            setattr(self.source, field, original)
            self.db.commit()

    def test_foreign_material_and_changed_material_are_rejected(self):
        foreign = self.asset("foreign.mov", tenant="t2")
        task = self.analyze(self.create(["product_visibility"]))
        items = deepcopy(task["plan"]["items"])
        items[0]["material_asset_id"] = foreign.id
        self.assertEqual(self.save(task, items).status_code, 422)
        items[0]["material_asset_id"] = self.material.id
        updated = self.save(task, items).json()
        self.assertFalse(updated["plan"]["execution_blockers"])
        self.material.content_hash = "c"*64
        self.db.commit()
        self.assertEqual(self.confirm(updated).status_code, 409)

    def test_invalid_time_ranges_duplicate_ids_and_unsupported_operations(self):
        task = self.analyze(self.create(["pace"]))
        original = task["plan"]["items"]
        invalids = []
        for patch in [{"range": {"start_ms": 2000, "end_ms": 1000}}, {"range": {"start_ms": 0, "end_ms": 23000}},
                      {"duration_delta_ms": 100}, {"duration_delta_ms": -999999}, {"operation": " "}, {"type": "ARBITRARY_SHELL"}]:
            items = deepcopy(original)
            items[0].update(patch)
            invalids.append(items)
        invalids.append(deepcopy(original)*2)
        for items in invalids:
            self.assertEqual(self.save(task, items).status_code, 422, items)
        self.assertEqual(self.db.query(EcommerceVideoRefinementPlan).count(), 1)

    def test_cross_tenant_task_access_and_cancellation(self):
        task = self.analyze(self.create(["pace"]))
        path = f"/api/video-refinements/{task['id']}"
        header = {"x-test-tenant": "t2"}
        self.assertEqual(self.client.get(path, headers=header).status_code, 404)
        self.assertEqual(self.client.get("/api/video-refinements", headers=header).json(), [])
        self.assertEqual(self.client.post(path+"/cancel", json={"revision": 1}, headers=header).status_code, 404)
        self.assertEqual(self.client.post(path+"/cancel", json={"revision": 1}).json()["status"], "CANCELLED")
        self.assertEqual(self.client.post(path+"/cancel", json={"revision": 1}).status_code, 200)
        self.assertEqual(self.save(task, task["plan"]["items"]).status_code, 409)
        self.assertEqual(self.confirm(task).status_code, 409)

    def test_list_returns_summaries_with_one_query_and_preserves_detail(self):
        first = self.create()
        second = self.create()
        foreign = EcommerceVideoRefinementTask(
            tenant_id="t2", source_asset_id=self.source.id, product_id=self.product.id,
            source_hash="a" * 64, source_snapshot={"filename": "foreign.mp4"},
            user_brief="foreign", created_by="owner",
        )
        self.db.add(foreign)
        self.db.commit()
        statements = []
        def record_query(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record_query)
        try:
            with patch("app.api.video_refinement._serialize", side_effect=AssertionError("detail loaded")):
                response = self.client.get("/api/video-refinements")
        finally:
            event.remove(self.engine, "before_cursor_execute", record_query)
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual([row["id"] for row in rows], [second["id"], first["id"]])
        self.assertEqual(len(statements), 1)
        self.assertEqual(set(rows[0]), {"id", "source_filename", "user_brief", "status", "current_revision", "created_at", "updated_at"})
        self.assertEqual(rows[0]["source_filename"], second["source_snapshot"]["source"]["filename"])
        for key in ("user_brief", "status", "current_revision", "created_at", "updated_at"):
            self.assertEqual(rows[0][key], second[key])
        self.assertEqual(self.client.get(f"/api/video-refinements/{second['id']}").json(), second)

    def test_failed_analysis_materials_are_candidates_without_fabricated_metadata(self):
        failed = self.asset("failed-analysis.mov")
        failed.status = "analysis_failed"
        failed.duration_seconds = 0
        self.db.commit()
        task = self.analyze(self.create(["product_visibility"]))
        material = next(row for row in task["source_snapshot"]["material_catalog"] if row["asset_id"] == failed.id)
        self.assertEqual(material["analysis_status"], "analysis_failed")
        self.assertEqual(material["analysis"], {})
        self.assertFalse(material["is_mock_mapping"])
        items = deepcopy(task["plan"]["items"])
        items[0]["material_asset_id"] = failed.id
        self.assertEqual(self.save(task, items).status_code, 200)

    def test_preview_requires_confirmation_is_idempotent_and_editing_invalidates_it(self):
        task = self.analyze(self.create(["pace"]))
        path = f"/api/video-refinements/{task['id']}/previews"
        payload = {"revision":1,"plan_hash":task["plan_hash"]}
        self.assertEqual(self.client.post(path,json=payload).status_code,409)
        self.confirm(task)
        with patch("app.services.refinement_queue.enqueue_preview"):
            first = self.client.post(path,json=payload)
            self.assertEqual(first.status_code,202,first.text)
            second = self.client.post(path,json=payload)
        self.assertEqual(first.json()["previews"][0]["id"],second.json()["previews"][0]["id"])
        preview_id = first.json()["previews"][0]["id"]
        items = deepcopy(task["plan"]["items"])
        items[0]["operation"] = "修改后旧预览失效"
        self.assertEqual(self.save(task,items).status_code,200)
        self.db.expire_all()
        self.assertEqual(self.db.get(EcommerceVideoRefinementPreview,preview_id).status,"superseded")
        self.assertEqual(self.client.post(path+f"/{preview_id}/accept",json=payload).status_code,409)

    def test_accept_preview_verifies_file_bytes_and_creates_only_one_child_version(self):
        task = self.analyze(self.create(["pace"]))
        self.confirm(task)
        path = f"/api/video-refinements/{task['id']}/previews"
        payload = {"revision":1,"plan_hash":task["plan_hash"]}
        with patch("app.services.refinement_queue.enqueue_preview"):
            preview_id = self.client.post(path,json=payload).json()["previews"][0]["id"]
        preview = self.db.get(EcommerceVideoRefinementPreview,preview_id)
        preview.status = "ready"
        preview.output_url = "/immutable-preview.mp4"
        preview.output_hash = "b"*64
        preview.storage_provider = "aliyun_oss"
        preview.validation_json = {"passed":True,"duration_seconds":21.98,"width":720,"height":1280,"fps":30,"has_audio":True,"burned_subtitles":True,"has_background_music":False}
        preview.recipe_json = [{"video_asset_id":self.source.id,"video_start_ms":800,"duration_ms":21980,"audio_asset_id":self.source.id,"audio_start_ms":800,"item_id":None}]
        self.db.commit()
        accept = path+f"/{preview_id}/accept"
        with patch("app.api.video_refinement.verified_path",side_effect=ValueError("文件内容已变化")):
            self.assertEqual(self.client.post(accept,json=payload).status_code,409)
        self.assertEqual(self.db.query(EcommerceVideoRefinementVersion).count(),0)
        with patch("app.api.video_refinement.verified_path",return_value=Path(self.temp.name)/"preview.mp4"):
            first = self.client.post(accept,json=payload)
            self.assertEqual(first.status_code,200,first.text)
            second = self.client.post(accept,json=payload)
        self.assertEqual(first.json()["output_asset_id"],second.json()["output_asset_id"])
        self.assertEqual(first.json()["status"],"COMPLETED")
        version = self.db.query(EcommerceVideoRefinementVersion).one()
        self.assertEqual(version.parent_asset_id,self.source.id)
        self.assertEqual(version.version,2)
        lineage = self.db.query(EcommerceVideoLineageSegment).filter_by(target_asset_id=version.output_asset_id).one()
        self.assertEqual(lineage.source_asset_id,self.source.id)
        self.assertAlmostEqual(lineage.target_end,21.98)
        self.assertEqual(self.db.get(EcommerceVideoAsset,version.output_asset_id).source,"video_refinement_output")
        self.db.refresh(self.source)
        self.assertEqual(self.source.content_hash,"a"*64)

    def test_real_ai_failure_does_not_publish_example_or_replace_prior_plan(self):
        task = self.analyze(self.create(["pace"]))
        with patch("app.api.video_refinement.analyze_source",side_effect=ValueError("AI 未返回有效方案")):
            response = self.client.post(f"/api/video-refinements/{task['id']}/analyze",json={"revision":1})
        self.assertEqual(response.status_code,422)
        current = self.client.get(f"/api/video-refinements/{task['id']}").json()
        self.assertEqual(current["plan_hash"],task["plan_hash"])
        self.assertEqual(current["current_revision"],1)


if __name__ == "__main__":
    unittest.main()
