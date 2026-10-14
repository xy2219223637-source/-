from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import ecommerce_video_library, video_refinement
from app.auth import AuthContext, create_token, current_auth
from app.database import Base, get_db
from app.models import (
    EcommerceProduct, EcommerceVideoAsset, EcommerceVideoAssetAnalysis, EcommerceVideoAssetTag,
    EcommerceVideoLineageSegment, EcommerceVideoRefinementPlan, EcommerceVideoRefinementPreview,
    EcommerceVideoRefinementRequest, EcommerceVideoRefinementSnapshot, EcommerceVideoRefinementTask, EcommerceVideoRefinementVersion,
    EcommerceVideoTag, Product, VideoAsset, VideoAssetAnalysis, VideoRefinementTask,
)
from app.schemas.video_refinement import RefinementItem
from app.services.ecommerce_video_analysis_cache import load_cached_analyses
from app.services.refinement_evaluation import cache_analysis, cached_material_analyses
from app.services.refinement_media import render_recipe
from app.services.refinement_plan import compile_plan, content_hash
from scripts.init_ecommerce_refinement import init_ecommerce_refinement


class EcommerceRefinementTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine, tables=[
            table for table in Base.metadata.sorted_tables if table.name.startswith("ecommerce_")
        ])
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.sessions()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.product = EcommerceProduct(tenant_id="t1", name="电商商品")
        self.db.add(self.product); self.db.commit()
        self.source = self.asset("商品成片.mp4", asset_kind="final_output", reusable_status="blocked", status="uploaded")
        self.material = self.asset("商品细节.mp4")
        self.db.add(EcommerceVideoAssetAnalysis(tenant_id="t1", asset_id=self.material.id,
            model="vision", prompt_version="video-library-v4-configured-asr", status="completed",
            analysis_json={"source_hash": self.material.content_hash, "summary": "同商品细节", "highlights": [
                {"start": 0, "end": 8, "description": "商品展示"},
            ]}))
        self.db.commit()
        app = FastAPI()
        app.include_router(video_refinement.router, prefix="/api")
        app.include_router(ecommerce_video_library.router, prefix="/api")

        def database():
            with self.sessions() as db:
                yield db

        def auth(x_test_tenant: str = Header(default="t1")):
            return AuthContext("owner", x_test_tenant, "owner", "test@example.com", "Test")

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[current_auth] = auth
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for target in ("app.services.refinement_evaluation.SessionLocal",):
            scoped = patch(target, self.sessions)
            scoped.start(); self.addCleanup(scoped.stop)

    def asset(self, filename, **overrides):
        values = dict(tenant_id="t1", product_id=self.product.id, asset_id=filename,
                      filename=filename, object_url=f"/{filename}", content_hash="a" * 64,
                      library_type="finished", asset_kind="material", duration_seconds=8,
                      width=720, height=1280, fps=30, has_audio=True, status="analyzed")
        values.update(overrides)
        asset = EcommerceVideoAsset(**values)
        self.db.add(asset); self.db.commit()
        return asset

    def create(self):
        response = self.client.post("/api/video-refinements", json={
            "source_asset_id": self.source.id, "user_brief": "替换商品画面, 保留原配音",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def seed_legacy(self):
        Base.metadata.create_all(self.engine)
        product = Product(id=self.product.id, tenant_id="t1", name="旧商品")
        self.db.add(product); self.db.flush()
        asset = VideoAsset(id=self.source.id, tenant_id="t1", product_id=product.id,
                          asset_id="legacy", filename="旧成片.mp4", library_type="finished",
                          asset_kind="final_output", object_url="/legacy.mp4", content_hash="b" * 64)
        self.db.add(asset); self.db.flush()
        self.db.add(VideoRefinementTask(id=1, tenant_id="t1", product_id=product.id, source_asset_id=asset.id,
                                       source_hash=asset.content_hash, source_snapshot={}, user_brief="旧精修", created_by="owner"))
        self.db.commit()
        return asset

    def test_sources_only_select_product_finals_without_material_reuse_gating(self):
        final = self.asset("其他商品成片.mp4", asset_kind="final_output")
        blocked = self.asset("禁止复用.mp4", asset_kind="final_output", reusable_status="blocked")
        excluded = self.asset("已排除.mp4", asset_kind="final_output", excluded=True)
        semi = self.asset("半成品.mp4", asset_kind="final_output", library_type="semi_finished")
        self.asset("其他租户.mp4", asset_kind="final_output", tenant_id="t2")
        rows = self.client.get("/api/video-refinements/sources").json()
        self.assertEqual({row["id"] for row in rows}, {self.source.id, final.id, blocked.id})
        self.assertEqual({row["product_name"] for row in rows}, {"电商商品"})
        response = self.client.post("/api/video-refinements", json={"source_asset_id": blocked.id, "user_brief": "修改画面"})
        self.assertEqual(response.status_code, 201, response.text)
        for asset, status in ((self.material, 404), (semi, 404), (excluded, 409)):
            response = self.client.post("/api/video-refinements", json={"source_asset_id": asset.id, "user_brief": "修改画面"})
            self.assertEqual(response.status_code, status, response.text)
        response = self.client.post("/api/video-refinements", headers={"x-test-tenant": "t2"},
                                    json={"source_asset_id": self.source.id, "user_brief": "修改画面"})
        self.assertEqual(response.status_code, 404)

    def test_shortening_rejects_equal_or_longer_targets_before_creating_records(self):
        for target in (8000, 20000):
            with self.subTest(target=target):
                response = self.client.post("/api/video-refinements", json={
                    "source_asset_id":self.source.id, "user_brief":"缩短成片", "duration_preference":"shorten",
                    "target_duration_ms":target,
                })
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("必须小于原片时长", response.json()["detail"])
        self.assertEqual(self.db.query(EcommerceVideoRefinementTask).count(), 0)
        self.assertEqual(self.db.query(EcommerceVideoRefinementSnapshot).count(), 0)
        response = self.client.post("/api/video-refinements", json={
            "source_asset_id":self.source.id, "user_brief":"缩短成片", "duration_preference":"shorten",
            "target_duration_ms":7999,
        })
        self.assertEqual(response.status_code, 201, response.text)

    def test_shortening_target_updates_use_effective_preferences_and_leave_revision_unchanged_on_error(self):
        task = self.create()
        endpoint = f"/api/video-refinements/{task['id']}/requirements"
        response = self.client.patch(endpoint, json={"revision":0, "duration_preference":"shorten", "target_duration_ms":7000})
        self.assertEqual(response.status_code, 200, response.text)
        for changes in ({"target_duration_ms":8000}, {"target_duration_ms":20000},
                        {"duration_preference":"shorten","target_duration_ms":20000}):
            response = self.client.patch(endpoint, json={"revision":1, **changes})
            self.assertEqual(response.status_code, 422, response.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(EcommerceVideoRefinementTask, task["id"]).current_revision, 1)
        self.assertEqual(self.db.query(EcommerceVideoRefinementRequest).count(), 2)
        cleared = self.client.patch(endpoint, json={"revision":1,"target_duration_ms":None})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertIsNone(cleared.json()["options"]["target_duration_ms"])

    def test_sources_return_library_tags_without_other_users_personal_tags(self):
        public = EcommerceVideoTag(tenant_id="t1", name="卖点", code="selling-point", scope="public", applies_to="both")
        own = EcommerceVideoTag(tenant_id="t1", name="我的成片", code="own", scope="personal", owner_id="owner", applies_to="both")
        other = EcommerceVideoTag(tenant_id="t1", name="他人的成片", code="other", scope="personal", owner_id="someone-else", applies_to="both")
        self.db.add_all([public,own,other]); self.db.flush()
        self.db.add_all([EcommerceVideoAssetTag(tenant_id="t1",asset_id=self.source.id,tag_id=tag.id,confirmed=True,created_by="owner") for tag in (public,own,other)])
        self.db.commit()
        row = self.client.get("/api/video-refinements/sources").json()[0]
        self.assertEqual({tag["id"] for tag in row["taxonomy_tags"]}, {public.id,own.id})
        self.assertEqual([tag["id"] for tag in row["personal_tags"]], [own.id])
        self.assertEqual([tag["id"] for tag in row["public_tags"]], [public.id])

    def test_source_reuse_status_does_not_invalidate_existing_refinement(self):
        task = self.create()
        record = self.db.get(EcommerceVideoRefinementTask, task["id"])
        for status in ("review_required", "reusable", "blocked"):
            with self.subTest(reusable_status=status):
                self.source.reusable_status = status
                self.db.commit()
                video_refinement._validate_source(self.db, record)

    def test_media_validation_is_required_but_library_ai_analysis_is_not(self):
        for status in ("validating", "validation_failed", "analysis_failed", "queued_for_analysis"):
            with self.subTest(status=status):
                self.source.status = status
                self.db.commit()
                source_ids = {row["id"] for row in self.client.get("/api/video-refinements/sources").json()}
                response = self.client.post("/api/video-refinements", json={
                    "source_asset_id": self.source.id, "user_brief": "精修商品成片",
                })
                if status in {"validating", "validation_failed"}:
                    self.assertNotIn(self.source.id, source_ids)
                    self.assertEqual(response.status_code, 422, response.text)
                else:
                    self.assertIn(self.source.id, source_ids)
                    self.assertEqual(response.status_code, 201, response.text)

    def test_blocked_replacement_material_is_still_excluded_from_snapshot(self):
        self.material.reusable_status = "blocked"
        self.db.commit()
        snapshot = self.create()["source_snapshot"]
        self.assertEqual(snapshot["material_catalog"], [])
        self.assertIn(self.material.id, {row["asset_id"] for row in snapshot["excluded_materials"]})

    def test_task_snapshot_and_foreign_keys_need_no_legacy_tables(self):
        other_final = self.asset("其他成片.mp4", asset_kind="final_output")
        other_product = EcommerceProduct(tenant_id="t1", name="另一个电商商品")
        self.db.add(other_product); self.db.commit()
        other_material = self.asset("其他商品素材.mp4", product_id=other_product.id)
        self.db.add_all([EcommerceVideoAssetAnalysis(tenant_id="t1", asset_id=asset.id,
            model="vision", status="completed", analysis_json={"source_hash": asset.content_hash,
                "summary": "非候选视频", "highlights": [{"start": 0, "end": 8}]})
            for asset in (other_final, other_material)])
        self.db.commit()
        task = self.create()
        self.assertEqual(task["source_snapshot"]["asset_domain"], "ecommerce")
        self.assertIsNone(task["source_snapshot"]["engineering_plan"])
        self.assertEqual([row["asset_id"] for row in task["source_snapshot"]["material_catalog"]], [self.material.id])
        inspector = inspect(self.engine)
        self.assertNotIn("video_assets", inspector.get_table_names())
        self.assertNotIn("products", inspector.get_table_names())
        for table in inspector.get_table_names():
            for foreign_key in inspector.get_foreign_keys(table):
                self.assertTrue(foreign_key["referred_table"].startswith("ecommerce_"), (table, foreign_key))

    def test_schema_initialization_is_additive_idempotent_and_supports_dry_run(self):
        engine = create_engine("sqlite://", poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        tables = [table for table in Base.metadata.sorted_tables
                  if table.name.startswith("ecommerce_") and not table.name.startswith("ecommerce_video_refinement_")
                  and table.name != "ecommerce_video_lineage_segments"]
        Base.metadata.create_all(engine, tables=tables)
        before = set(inspect(engine).get_table_names())
        pending = init_ecommerce_refinement(engine, dry_run=True)
        self.assertEqual(len(pending), 9)
        self.assertEqual(set(inspect(engine).get_table_names()), before)
        self.assertEqual(init_ecommerce_refinement(engine), pending)
        self.assertEqual(init_ecommerce_refinement(engine), [])
        self.assertEqual(set(inspect(engine).get_table_names()), before | set(pending))

    def test_overlapping_legacy_ids_never_import_old_sources_tasks_or_analysis(self):
        legacy = self.seed_legacy()
        self.db.add(VideoAssetAnalysis(tenant_id="t1", asset_id=legacy.id, model="vision", status="completed",
            analysis_json={"source_hash": self.source.content_hash, "highlights": [{"start": 0, "end": 8}], "summary": "旧库分析"}))
        self.db.commit()
        self.assertEqual(self.client.get("/api/video-refinements").json(), [])
        self.assertEqual(self.client.get("/api/video-refinements/1").status_code, 404)
        task = self.create()
        self.assertEqual(task["source_snapshot"]["source"]["filename"], self.source.filename)
        self.assertEqual(task["source_snapshot"]["source_analysis"], {})
        self.assertEqual(self.db.query(VideoRefinementTask).one().user_brief, "旧精修")
        self.assertEqual(self.client.get("/api/video-refinements/1", headers={"x-test-tenant": "t2"}).status_code, 404)
        legacy_only = VideoAsset(tenant_id="t1", product_id=self.product.id, id=999,
            asset_id="legacy-only", filename="旧成片.mp4", asset_kind="final_output", library_type="finished",
            object_url="/legacy-only.mp4", content_hash="c" * 64)
        self.db.add(legacy_only); self.db.commit()
        self.assertEqual(self.client.post("/api/video-refinements", json={
            "source_asset_id": 999, "user_brief": "修改旧素材",
        }).status_code, 404)

    def test_analysis_cache_reads_and_writes_only_ecommerce_records(self):
        self.seed_legacy()
        snapshot = self.create()["source_snapshot"]
        cached = cached_material_analyses(snapshot)
        self.assertEqual(cached[self.material.id]["data"]["summary"], "同商品细节")
        cache_analysis({**snapshot["source"], "tenant_id": "t1"}, {"summary": "商品源视频转写"}, "refinement-asr")
        self.assertEqual(self.db.query(VideoAssetAnalysis).count(), 0)
        self.assertEqual(self.db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=self.source.id).count(), 1)

    def test_library_analysis_is_version_bound_and_reusable_without_upload_session(self):
        data = ecommerce_video_library._normalize_analysis(self.material, {"highlights": [
            {"start": 0, "end": 8, "description": "商品细节"},
        ]})
        self.assertEqual(data["source_hash"], self.material.content_hash)
        row = EcommerceVideoAssetAnalysis(tenant_id="t1", asset_id=self.material.id, model="vision",
                                         prompt_version="video-library-v4-configured-asr", analysis_json=data)
        self.db.add(row); self.db.commit()
        cached = load_cached_analyses(self.db, "t1", [self.material])
        self.assertEqual(cached[self.material.id]["analysis_id"], row.id)
        self.material.content_hash = "c" * 64; self.db.commit()
        self.assertEqual(load_cached_analyses(self.db, "t1", [self.material]), {})

    def test_acceptance_saves_to_product_final_library_and_inherits_product_tags(self):
        task = self.create()
        item = RefinementItem(item_id="visual", type="VISUAL_REPLACE", range={"start_ms": 1000, "end_ms": 2000},
                              operation="替换商品画面", reason="展示细节", material_segments=[{
                                  "asset_id": self.material.id, "source_range": {"start_ms": 0, "end_ms": 1000},
                              }])
        plan_json = compile_plan(task["source_snapshot"], [item], task["user_brief"], "", manual=False)
        plan = EcommerceVideoRefinementPlan(task_id=task["id"], revision=0, origin="ai", plan_json=plan_json,
            plan_hash=content_hash(plan_json), source_snapshot_id=self.db.query(EcommerceVideoRefinementSnapshot).one().id)
        self.db.add(plan)
        tag = EcommerceVideoTag(tenant_id="t1", name="商品展示", code="demo", scope="public", applies_to="both")
        self.db.add(tag); self.db.flush()
        self.db.add(EcommerceVideoAssetTag(tenant_id="t1", asset_id=self.material.id, tag_id=tag.id,
                                          confirmed=True, created_by="owner"))
        self.db.commit()
        payload = {"revision": 0, "plan_hash": plan.plan_hash}
        confirmed = self.client.post(f"/api/video-refinements/{task['id']}/plans/confirm", json={**payload, "acknowledge_risks": True})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        preview = EcommerceVideoRefinementPreview(tenant_id="t1", task_id=task["id"], plan_id=plan.id,
            revision=0, plan_hash=plan.plan_hash, status="ready", output_url="/product-preview.mp4",
            storage_provider="aliyun_oss", output_hash="c" * 64, recipe_json=render_recipe(task["source_snapshot"], plan_json),
            validation_json={"passed": True, "duration_seconds": 8, "width": 720, "height": 1280, "fps": 30,
                             "has_audio": True, "burned_subtitles": False, "has_background_music": False})
        self.db.add(preview); self.db.commit()
        endpoint = f"/api/video-refinements/{task['id']}/previews/{preview.id}/accept"
        with patch("app.api.video_refinement.verified_path", return_value=Path("product-preview.mp4")):
            response = self.client.post(endpoint, json=payload)
            again = self.client.post(endpoint, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        output_id = response.json()["output_asset_id"]
        self.assertEqual(again.json()["output_asset_id"], output_id)
        self.assertEqual(self.db.query(EcommerceVideoRefinementVersion).count(), 1)
        output = self.db.get(EcommerceVideoAsset, output_id)
        self.assertEqual((output.product_id, output.asset_kind, output.library_type, output.source),
                         (self.product.id, "final_output", "finished", "video_refinement_output"))
        self.assertEqual(output.status, "uploaded")
        self.assertEqual(output.reusable_status, "blocked")
        sources = self.client.get("/api/video-refinements/sources").json()
        self.assertIn(output_id, {row["id"] for row in sources})
        finals = self.client.get("/api/ecommerce-video-assets", params={"asset_kind": "final_output", "product_id": self.product.id}).json()
        self.assertEqual({row["id"] for row in finals}, {self.source.id, output_id})
        saved = next(row for row in finals if row["id"] == output_id)
        self.assertEqual([row["id"] for row in saved["public_tags"]], [tag.id])
        lineage = self.db.query(EcommerceVideoLineageSegment).filter_by(target_asset_id=output_id).all()
        self.assertEqual({row.source_asset_id for row in lineage}, {self.source.id, self.material.id})
        self.db.refresh(self.source)
        self.assertEqual(self.source.asset_kind, "final_output")
        self.assertEqual(self.source.content_hash, "a" * 64)
        self.assertNotIn("video_assets", inspect(self.engine).get_table_names())
        followup = self.client.post("/api/video-refinements", json={
            "source_asset_id": output_id, "user_brief": "继续精修成片",
        })
        self.assertEqual(followup.status_code, 201, followup.text)
        self.assertEqual(followup.json()["source_snapshot"]["source"]["filename"], output.filename)

    def test_refinement_permission_depends_on_product_assets_not_legacy_finals(self):
        member = SimpleNamespace(role="employee", role_id=1)
        user = SimpleNamespace(id="user", email="test@example.com", phone="", is_platform_admin=False)
        tenant = SimpleNamespace(id="t1", name="Test")
        request = Request({"type": "http", "method": "GET", "path": "/api/video-refinements/sources", "headers": []})
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=create_token("user", "t1"))
        for permissions, allowed in ((["ecommerce_assets.access"], True), (["final_videos.access"], False)):
            db = MagicMock(spec=Session)
            db.query.return_value.filter_by.return_value.first.side_effect = [
                member, user, tenant, SimpleNamespace(permissions=permissions),
            ]
            if allowed:
                self.assertEqual(current_auth(request, credentials, db).permissions, tuple(permissions))
            else:
                with self.assertRaises(HTTPException) as caught:
                    current_auth(request, credentials, db)
                self.assertEqual(caught.exception.status_code, 403)
