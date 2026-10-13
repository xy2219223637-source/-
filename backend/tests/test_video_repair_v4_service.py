from __future__ import annotations

import unittest
import asyncio
import io
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.api import products, video_repair
from app.auth import AuthContext
from app.models import (
    EcommerceProduct, EcommerceVideoAsset, EcommerceVideoAssetAnalysis, EcommerceVideoHighlight,
    Product, Tenant, User, VideoAsset, VideoAssetAnalysis, VideoHighlight,
    VideoRepairInput, VideoRepairJob, VideoRepairPreview, VideoRepairTask,
)
from app.services.video_repair.migration import migrate_video_repair
from app.services.video_repair.migration_v2 import migrate_video_repair_v2
from app.services.video_repair.migration_v3 import migrate_video_repair_v3
from app.services.video_repair.protocol_v4 import plan_hash
from app.services.video_repair.service import serialize_task
from app.services.video_repair.ecommerce_materials import _create_or_reuse_mirror, sync_ecommerce_materials
from app.services.video_repair.legacy_analysis_import import import_finished_video_analysis
from app.services.video_repair.candidates import find_candidates
from app.services.video_repair.diagnosis_v4 import VideoRepairDiagnosisV4
from app.services.video_repair.v4_service import accept_preview, update_v4_draft
from app.services.video_repair import worker_v4
from app.schemas.video_repair import VideoRepairInputCreate, VideoRepairTaskCreate


class VideoRepairV4ServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        startup = [table for table in Base.metadata.sorted_tables
                   if not table.info.get("explicit_migration_required")]
        Base.metadata.create_all(self.engine, tables=startup)
        migrate_video_repair(self.engine)
        migrate_video_repair_v2(self.engine)
        migrate_video_repair_v3(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([
                Tenant(id="tenant", name="Tenant", code="tenant"),
                User(id="owner", email="owner@example.test", name="Owner", password_hash="unused"),
                Product(id=1, tenant_id="tenant", name="Product"),
                Product(id=2, tenant_id="tenant", name="Other Product"),
            ])
            db.add_all([
                VideoAsset(
                    id=1, tenant_id="tenant", product_id=1, asset_id="source-v1",
                    filename="source.mp4", source="manual_upload", library_type="finished",
                    asset_kind="final_output", object_url="/source.mp4", content_hash="a" * 64,
                    duration_seconds=12, width=1080, height=1920, fps=30, has_audio=True,
                    status="analyzed", reusable_status="review_required"),
                VideoAsset(
                    id=2, tenant_id="tenant", product_id=1, asset_id="material",
                    filename="material.mp4", source="manual_upload", library_type="semi_finished",
                    asset_kind="material", object_url="/material.mp4", content_hash="b" * 64,
                    duration_seconds=8, width=1080, height=1920, fps=30, has_audio=True,
                    status="analyzed", reusable_status="reusable"),
                VideoAsset(
                    id=3, tenant_id="tenant", product_id=2, asset_id="foreign-material",
                    filename="foreign.mp4", source="manual_upload", library_type="semi_finished",
                    asset_kind="material", object_url="/foreign.mp4", content_hash="c" * 64,
                    duration_seconds=8, width=1080, height=1920, fps=30, has_audio=True,
                    status="analyzed", reusable_status="reusable"),
            ])
            db.add(VideoRepairInput(
                id=1, tenant_id="tenant", created_by="owner", source_asset_id=1,
                source_kind="MANUAL", audit_platform="INTERNAL", raw_reason="画面错误",
                expected_result="替换画面", suggestions_json=[], time_ranges_json=[],
                raw_payload_json={}))
            db.add(VideoRepairTask(
                id=1, tenant_id="tenant", created_by="owner", input_id=1,
                source_asset_id=1, product_id=1, schema_version="video_repair_task.v2",
                draft_revision=1, status="CONFIGURING",
                diagnosis_json={"recommended_action": "VISUAL_REPLACE"}))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def action(ref_id: int = 2) -> dict:
        return {
            "type": "VISUAL_REPLACE",
            "target_range": {"start_ms": 2000, "end_ms": 5000},
            "material": {
                "ref_type": "VIDEO_ASSET", "ref_id": ref_id,
                "source_range": {"start_ms": 1000, "end_ms": 4000},
            },
        }

    def _queued_analysis_job(self, db):
        task = db.get(VideoRepairTask, 1)
        task.status = "ANALYZING"
        source = db.get(VideoAsset, 1)
        source.status = "queued_for_analysis"
        job = VideoRepairJob(
            tenant_id="tenant", task_id=task.id, job_type="ANALYZE", status="QUEUED",
            idempotency_key="test-source-analysis-dependency", payload_json={}, max_attempts=2)
        db.add(job)
        db.commit()
        return job.id

    def test_repair_waits_for_matching_source_analysis_without_using_attempts(self):
        with self.sessions() as db:
            job_id = self._queued_analysis_job(db)
        used_analysis_ids = []
        with patch.object(worker_v4, "SessionLocal", self.sessions), \
             patch.object(worker_v4, "_analyze",
                          side_effect=lambda db, task, analysis: used_analysis_ids.append(analysis.id)) as diagnose:
            worker_v4._execute(job_id)
            with self.sessions() as db:
                job = db.get(VideoRepairJob, job_id)
                self.assertEqual((job.status, job.attempts), ("QUEUED", 0))
                self.assertIn("dependency_next_check_at", job.payload_json)
                self.assertEqual(db.get(VideoRepairTask, 1).status, "ANALYZING")
                db.add(VideoAssetAnalysis(
                    tenant_id="tenant", asset_id=1, status="completed",
                    analysis_json={"source_hash": "stale", "highlights": [{"start": 0, "end": 2}]}))
                db.commit()
            with self.sessions() as db:
                self.assertEqual(worker_v4._source_analysis_dependency(
                    db, db.get(VideoRepairTask, 1))[0], "WAITING")
                db.add(VideoAssetAnalysis(
                    tenant_id="tenant", asset_id=1, status="completed",
                    analysis_json={"source_hash": "a" * 64,
                                   "highlights": [{"start": 0, "end": 2}]}))
                db.get(VideoAsset, 1).status = "analyzed"
                db.commit()
            worker_v4._execute(job_id)
            with self.sessions() as db:
                job = db.get(VideoRepairJob, job_id)
                self.assertEqual((job.status, job.attempts), ("SUCCEEDED", 1))
                expected_id = db.query(VideoAssetAnalysis).filter_by(
                    asset_id=1, status="completed").order_by(
                    VideoAssetAnalysis.id.desc()).first().id
            self.assertEqual(used_analysis_ids, [expected_id])

    def test_ai_action_without_range_requests_human_range_and_remains_configurable(self):
        for action, modalities, category, required in [
            ("VISUAL_REPLACE", ["visual"], "VISUAL_MISMATCH", "visual_material"),
            ("AUDIO_REPLACE", ["audio"], "COPY_AUDIO_CAPTION_RISK", "audio"),
        ]:
            with self.subTest(action=action), self.sessions() as db:
                task = db.get(VideoRepairTask, 1)
                task.status = "ANALYZING"
                analysis = VideoAssetAnalysis(
                    tenant_id="tenant", asset_id=1, status="completed",
                    analysis_json={"source_hash": "a" * 64,
                                   "highlights": [{"start": 0, "end": 2}]})
                db.add(analysis)
                db.commit()
                model = VideoRepairDiagnosisV4.model_validate({
                    "summary": "动作可执行，但区间待确认",
                    "issues": [{"issue_id": "issue-1", "category": category,
                                "description": "问题", "affected_modalities": modalities,
                                "ranges": [], "evidence": [{"source": "REVIEW_TEXT", "description": "审核意见"}],
                                "confidence": .8, "suggested_action": action}],
                    "primary_issue_id": "issue-1", "requires_human_range_confirmation": True,
                    "overall_confidence": .8, "meta": {"mode": "MULTIMODAL", "input_mode": (
                        "STRUCTURED_CONTEXT" if action == "VISUAL_REPLACE" else "VIDEO")},
                })
                context = {"review": {"explicit_time_ranges": []},
                           "observations": {"asr_segments": [{"text": "hello"}]}}
                settings = SimpleNamespace(
                    video_repair_ai_max_keyframes=12,
                    video_repair_audio_replace_enabled=True,
                    video_repair_content_insert_enabled=True)
                with patch.object(worker_v4, "sync_ecommerce_materials"), \
                     patch.object(worker_v4, "build_ai_context", return_value=context), \
                     patch.object(worker_v4, "diagnose_video_v4", return_value=model), \
                     patch.object(worker_v4, "get_settings", return_value=settings), \
                     patch.object(worker_v4, "_provider", return_value=None):
                    settings.video_repair_multimodal_enabled = True
                    worker_v4._analyze(db, task, analysis)
                self.assertEqual(task.status, "CONFIGURING")
                proposal = task.diagnosis_json["ai_proposal"]
                self.assertEqual(proposal["action_template"]["type"], action)
                self.assertEqual(set(proposal["requires_input"]), {"target_range", required})
                if action == "VISUAL_REPLACE":
                    self.assertIn("没有根据原视频画面判断", task.diagnosis_json["evidence_warnings"][0])
                    self.assertEqual(task.diagnosis_json["evidence_sufficiency"], "INSUFFICIENT")

    def test_content_insert_uses_one_placement_for_summary_and_render_action(self):
        with self.sessions() as db:
            task = db.get(VideoRepairTask, 1)
            task.status = "ANALYZING"
            analysis = VideoAssetAnalysis(
                tenant_id="tenant", asset_id=1, status="completed",
                analysis_json={"source_hash": "a" * 64,
                               "highlights": [{"start": 0, "end": 2}]})
            db.add(analysis)
            db.commit()
            model = VideoRepairDiagnosisV4.model_validate({
                "summary": "缺少功能介绍",
                "issues": [{"issue_id": "issue-1", "category": "MISSING_PROCESS_EVIDENCE",
                            "description": "缺少功能介绍", "affected_modalities": ["visual", "audio"],
                            "ranges": [{"start_ms": 0, "end_ms": 3000,
                                        "precision": "APPROXIMATE", "source": "MULTIMODAL", "confidence": .6}],
                            "evidence": [{"source": "REVIEW_TEXT", "description": "审核意见"}],
                            "confidence": .8, "suggested_action": "CONTENT_INSERT"}],
                "primary_issue_id": "issue-1", "requires_human_range_confirmation": False,
                "overall_confidence": .8, "meta": {"mode": "MULTIMODAL", "input_mode": "VIDEO"},
            })
            context = {"review": {"explicit_time_ranges": []},
                       "timeline": {}, "observations": {"asr_segments": []}}
            candidate = {"id": "candidate-1", "source_asset_id": 2,
                         "source_range": {"start": 1.0, "end": 5.0},
                         "duration_seconds": 8.0, "content_hash": "b" * 64,
                         "recommendation_level": "RECOMMENDED", "has_audio": True,
                         "description": "功能演示"}
            settings = SimpleNamespace(
                video_repair_ai_max_keyframes=12,
                video_repair_audio_replace_enabled=True,
                video_repair_content_insert_enabled=True,
                video_repair_multimodal_enabled=True,
                video_repair_recommend_score=.8, video_repair_selectable_score=.5)
            with patch.object(worker_v4, "sync_ecommerce_materials"), \
                 patch.object(worker_v4, "build_ai_context", return_value=context), \
                 patch.object(worker_v4, "diagnose_video_v4", return_value=model), \
                 patch.object(worker_v4, "find_candidates", return_value=[candidate]), \
                 patch.object(worker_v4, "get_settings", return_value=settings), \
                 patch.object(worker_v4, "_provider", return_value=None):
                worker_v4._analyze(db, task, analysis)
            proposal = task.diagnosis_json["ai_proposal"]
            actual = proposal["action"]["insert_at_ms"]
            self.assertEqual(actual, proposal["placement"]["recommended_ms"])
            self.assertGreater(actual, 0)
            self.assertIn(f"{actual / 1000:g} 秒插入", proposal["summary"])

    def test_source_analysis_failure_is_distinct_from_ai_failure_and_can_retry(self):
        with self.sessions() as db:
            job_id = self._queued_analysis_job(db)
            db.get(VideoAsset, 1).status = "analysis_failed"
            db.commit()
        with patch.object(worker_v4, "SessionLocal", self.sessions), \
             patch.object(worker_v4, "_analyze") as diagnose:
            worker_v4._execute(job_id)
        diagnose.assert_not_called()
        with self.sessions() as db:
            task = db.get(VideoRepairTask, 1)
            self.assertEqual((task.status, task.error_code),
                             ("FAILED", "SOURCE_ANALYSIS_FAILED"))
            self.assertEqual(db.get(VideoRepairJob, job_id).attempts, 0)
            auth = SimpleNamespace(user_id="owner", tenant_id="tenant")
            with patch.object(video_repair, "enqueue_video_analysis") as enqueue, \
                 patch.object(video_repair, "wake_video_repair_worker"):
                video_repair.retry_v4_analysis(1, db, auth)
                enqueue.assert_called_once()
            self.assertEqual(db.get(VideoAsset, 1).status, "queued_for_analysis")
            self.assertEqual(db.get(VideoRepairTask, 1).status, "ANALYZING")

    def test_reanalyze_ready_preview_supersedes_it_and_invalidates_draft(self):
        with self.sessions() as db:
            task = update_v4_draft(db, db.get(VideoRepairTask, 1), revision=1, action=self.action())
            task.status = "PREVIEW_READY"
            preview = VideoRepairPreview(
                tenant_id="tenant", task_id=task.id, draft_revision=task.draft_revision,
                plan_hash=plan_hash(task.repair_plan_json), status="READY",
                object_url="/preview-v2.mp4", content_hash="d" * 64,
                duration_seconds=12, width=1080, height=1920, created_by="owner",
                source_snapshot_json={}, plan_snapshot_json=task.repair_plan_json)
            db.add(preview); db.commit(); db.refresh(preview)
            prior_revision = task.draft_revision
            auth = SimpleNamespace(user_id="owner", tenant_id="tenant")
            with patch.object(video_repair, "wake_video_repair_worker"):
                video_repair.retry_v4_analysis(task.id, db, auth)
            self.assertEqual(task.status, "ANALYZING")
            self.assertEqual(task.draft_revision, prior_revision + 1)
            self.assertEqual(task.repair_plan_json, {})
            self.assertEqual(preview.status, "SUPERSEDED")

    def test_waiting_dependency_does_not_block_other_queued_jobs(self):
        with self.sessions() as db:
            waiting_id = self._queued_analysis_job(db)
            other = VideoRepairJob(
                tenant_id="tenant", task_id=1, job_type="RENDER_PREVIEW", status="QUEUED",
                idempotency_key="other-ready-job", payload_json={}, max_attempts=1)
            db.add(other)
            db.commit()
            other_id = other.id
        with patch.object(worker_v4, "SessionLocal", self.sessions):
            worker_v4._execute(waiting_id)
            self.assertEqual(worker_v4._next_job_id(), other_id)

    def test_source_analysis_timeout_never_invokes_ai(self):
        with self.sessions() as db:
            job_id = self._queued_analysis_job(db)
            db.get(VideoRepairJob, job_id).created_at = datetime.utcnow() - timedelta(minutes=21)
            db.commit()
        with patch.object(worker_v4, "SessionLocal", self.sessions), \
             patch.object(worker_v4, "_analyze") as diagnose:
            worker_v4._execute(job_id)
        diagnose.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.get(VideoRepairTask, 1).error_code, "SOURCE_ANALYSIS_TIMEOUT")
            self.assertEqual(db.get(VideoRepairJob, job_id).attempts, 0)

    def test_draft_revision_is_optimistic_and_rejects_other_product(self):
        with self.sessions() as db:
            task = db.get(VideoRepairTask, 1)
            updated = update_v4_draft(db, task, revision=1, action=self.action())
            self.assertEqual(updated.draft_revision, 2)
            self.assertEqual(updated.repair_plan_json["action"]["material"]["content_hash"], "b" * 64)
            with self.assertRaises(HTTPException) as stale:
                update_v4_draft(db, updated, revision=1, action=self.action())
            self.assertEqual(stale.exception.status_code, 409)
        with self.sessions() as db:
            task = db.get(VideoRepairTask, 1)
            with self.assertRaises(HTTPException) as mismatch:
                update_v4_draft(db, task, revision=2, action=self.action(3))
            self.assertEqual(mismatch.exception.status_code, 422)

    def test_accept_ready_preview_reuses_rendered_object_and_keeps_v1(self):
        with self.sessions() as db:
            task = update_v4_draft(db, db.get(VideoRepairTask, 1), revision=1, action=self.action())
            task.status = "PREVIEW_READY"
            preview = VideoRepairPreview(
                tenant_id="tenant", task_id=task.id, draft_revision=task.draft_revision,
                plan_hash=plan_hash(task.repair_plan_json), status="READY",
                object_url="/preview-v2.mp4", content_hash="d" * 64,
                duration_seconds=12, width=1080, height=1920, created_by="owner",
                source_snapshot_json={}, plan_snapshot_json=task.repair_plan_json)
            db.add(preview); db.commit(); db.refresh(preview)
            result = accept_preview(db, task, preview.id, task.draft_revision)
            output = db.get(VideoAsset, result.output_asset_id)
            self.assertEqual(result.status, "COMPLETED")
            self.assertEqual(output.object_url, preview.object_url)
            self.assertEqual(db.get(VideoAsset, 1).object_url, "/source.mp4")
            self.assertNotEqual(output.id, 1)

    def test_manual_repair_result_enters_ecommerce_final_library_once(self):
        with self.sessions() as db:
            db.add(EcommerceProduct(id=7, tenant_id="tenant", name="电商商品"))
            repair_input = db.get(VideoRepairInput, 1)
            repair_input.raw_payload_json = {"ecommerce_product_id": 7}
            db.commit()
            task = update_v4_draft(db, db.get(VideoRepairTask, 1), revision=1, action=self.action())
            task.status = "PREVIEW_READY"
            preview = VideoRepairPreview(
                tenant_id="tenant", task_id=task.id, draft_revision=task.draft_revision,
                plan_hash=plan_hash(task.repair_plan_json), status="READY",
                object_url="/preview-v2.mp4", content_hash="d" * 64,
                duration_seconds=12, width=1080, height=1920, created_by="owner",
                source_snapshot_json={}, plan_snapshot_json=task.repair_plan_json)
            db.add(preview); db.commit(); db.refresh(preview)
            with patch("app.services.storage.get_storage") as storage:
                storage.return_value.name = "test"
                result = accept_preview(db, task, preview.id, task.draft_revision)
                accept_preview(db, result, preview.id, task.draft_revision)
            rows = db.query(EcommerceVideoAsset).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].product_id, 7)
            self.assertEqual(rows[0].asset_kind, "final_output")
            self.assertEqual(rows[0].object_url, preview.object_url)
            self.assertEqual(rows[0].source, f"video_repair_task:{task.id}")
            self.assertEqual(db.get(VideoAsset, result.output_asset_id).library_type, "repair_only")
            self.assertEqual(serialize_task(db, result)["ecommerce_output_asset_id"], rows[0].id)

    def test_manual_upload_creates_repair_without_ecommerce_source_video(self):
        class Storage:
            name = "test"

            async def upload_path(self, path, filename, subdir):
                self_data = path.read_bytes()
                assert self_data == b"video-bytes"
                assert subdir.endswith("/final_output")
                return f"/{subdir}/{filename}"

        with self.sessions() as db:
            db.add(EcommerceProduct(id=7, tenant_id="tenant", name="电商商品"))
            db.commit()
            auth = AuthContext("owner", "tenant", "owner", "owner@example.test", "Owner")
            uploaded = UploadFile(filename="original.mp4", file=io.BytesIO(b"video-bytes"))
            settings = SimpleNamespace(video_repair_v4_enabled=True,
                video_repair_async_worker_enabled=True, director_video_max_upload_mb=1,
                video_library_max_duration_seconds=600)
            with patch.object(video_repair, "get_settings", return_value=settings), \
                 patch.object(video_repair, "get_video_storage", return_value=Storage()), \
                 patch.object(video_repair, "_probe", return_value={
                     "duration": 12.0, "width": 1080, "height": 1920,
                     "fps": 30.0, "has_audio": True}), \
                 patch.object(video_repair, "enqueue_video_analysis"), \
                 patch("app.services.video_repair.worker_v4.wake_video_repair_worker"):
                source = asyncio.run(video_repair.upload_repair_source(
                    ecommerce_product_id=7, file=uploaded, db=db, auth=auth))
                result = video_repair.create_repair(VideoRepairTaskCreate(
                    source_asset_id=source["id"], ecommerce_product_id=7,
                    input=VideoRepairInputCreate(raw_reason="画面错误")), db=db, auth=auth)
            self.assertEqual(result["source_asset_id"], source["id"])
            self.assertEqual(result["status"], "ANALYZING")
            self.assertEqual(db.get(VideoAsset, source["id"]).library_type, "repair_only")
            self.assertEqual(db.query(EcommerceVideoAsset).count(), 0)
            self.assertEqual(db.get(VideoRepairInput, result["input_id"]).raw_payload_json,
                             {"ecommerce_product_id": 7})

    def test_ecommerce_product_video_becomes_repair_candidate(self):
        with self.sessions() as db:
            db.add(EcommerceProduct(id=7, tenant_id="tenant", name="电商商品"))
            repair_input = db.get(VideoRepairInput, 1)
            repair_input.raw_payload_json = {"ecommerce_product_id": 7}
            asset = EcommerceVideoAsset(
                tenant_id="tenant", product_id=7, asset_id="ecom-material",
                filename="商品展示.mp4", source="manual_upload", library_type="finished",
                asset_kind="material", reusable_status="reusable", object_url="/ecom.mp4",
                storage_provider="test", content_hash="e" * 64, duration_seconds=8,
                width=1080, height=1920, fps=30, has_audio=True, status="analyzed")
            db.add(asset); db.flush()
            analysis = EcommerceVideoAssetAnalysis(
                tenant_id="tenant", asset_id=asset.id, model="test", prompt_version="test",
                status="completed", summary="商品展示", analysis_json={}, input_video_seconds=8)
            db.add(analysis); db.flush()
            db.add(EcommerceVideoHighlight(
                tenant_id="tenant", asset_id=asset.id, analysis_id=analysis.id,
                highlight_id="ecom-h1", start_seconds=0, end_seconds=5,
                description="商品近景", reason="同商品", content_role="product_demo", score=.9))
            db.commit()
            task = db.get(VideoRepairTask, 1)
            first = sync_ecommerce_materials(db, task)
            second = sync_ecommerce_materials(db, task)
            raced = _create_or_reuse_mirror(db, task, asset)
            self.assertEqual(first[0].id, second[0].id)
            self.assertEqual(raced.id, first[0].id)
            self.assertEqual(db.get(VideoRepairTask, 1).id, 1)  # Savepoint failure did not poison the session.
            with self.assertRaises(IntegrityError):
                _create_or_reuse_mirror(
                    db, SimpleNamespace(tenant_id="tenant", product_id=2), asset)
            self.assertEqual(db.query(VideoAsset).filter_by(source=f"ecommerce_video_asset:{asset.id}").count(), 1)
            self.assertEqual(db.query(VideoAsset).filter_by(source=f"ecommerce_video_asset:{asset.id}").count(), 1)
            self.assertEqual(db.query(VideoAssetAnalysis).filter_by(asset_id=first[0].id).count(), 1)
            self.assertEqual(db.query(VideoHighlight).filter_by(asset_id=first[0].id).count(), 1)
            candidates = find_candidates(db, tenant_id="tenant", source_asset=db.get(VideoAsset, 1),
                action="VISUAL_REPLACE", target_duration=3)
            self.assertIn(first[0].id, [row["source_asset_id"] for row in candidates])
            auth = AuthContext("owner", "tenant", "owner", "owner@example.test", "Tenant")
            listed = video_repair.list_v4_materials(1, db, auth)
            self.assertTrue(next(row for row in listed if row["id"] == first[0].id)["ecommerce_has_analyzed_segments"])

    def test_repair_reuses_matching_existing_product_and_video(self):
        with self.sessions() as db:
            db.add(EcommerceProduct(id=7, tenant_id="tenant", name="Product"))
            db.flush()
            self.assertEqual(video_repair._repair_product(db, "tenant", 7).id, 1)
            self.assertEqual(db.query(Product).count(), 2)
            repair_input = db.get(VideoRepairInput, 1)
            repair_input.raw_payload_json = {"ecommerce_product_id": 7}
            db.add(EcommerceVideoAsset(
                tenant_id="tenant", product_id=7, asset_id="existing-material",
                filename="material.mp4", source="manual_upload", library_type="finished",
                asset_kind="material", reusable_status="reusable", object_url="/material.mp4",
                storage_provider="test", content_hash="b" * 64, duration_seconds=8,
                width=1080, height=1920, fps=30, has_audio=True, status="uploaded"))
            db.flush()
            rows = sync_ecommerce_materials(db, db.get(VideoRepairTask, 1))
            self.assertEqual([row.id for row in rows], [2])
            self.assertEqual(db.query(VideoAsset).count(), 3)
            auth = AuthContext("owner", "tenant", "owner", "owner@example.test", "Tenant")
            listed = video_repair.list_v4_materials(1, db, auth)
            self.assertFalse(next(row for row in listed if row["id"] == 2)["ecommerce_has_analyzed_segments"])

    def test_imports_existing_finished_analysis_into_ecommerce_video_once(self):
        with self.sessions() as db:
            db.add(EcommerceProduct(id=7, tenant_id="tenant", name="Product"))
            source = db.get(VideoAsset, 2)
            source.library_type = "finished"
            source.ai_tags = ["使用画面"]
            db.add(EcommerceVideoAsset(
                tenant_id="tenant", product_id=7, asset_id="same-video",
                filename="material.mp4", source="manual_upload", library_type="finished",
                asset_kind="material", reusable_status="review_required", object_url="/material.mp4",
                storage_provider="test", content_hash="b" * 64, duration_seconds=8,
                width=1080, height=1920, fps=30, has_audio=True, status="uploaded"))
            analysis = VideoAssetAnalysis(
                tenant_id="tenant", asset_id=source.id, model="vision", prompt_version="old",
                status="completed", summary="使用展示", analysis_json={"scene": "usage"},
                input_video_seconds=8)
            db.add(analysis)
            db.flush()
            db.add(VideoHighlight(
                tenant_id="tenant", asset_id=source.id, analysis_id=analysis.id,
                highlight_id="old-h1", start_seconds=1, end_seconds=6,
                description="使用画面", reason="同商品", content_role="product_demo", score=.8))
            db.flush()
            first = import_finished_video_analysis(
                db, tenant_id="tenant", legacy_product_id=1, ecommerce_product_id=7)
            second = import_finished_video_analysis(
                db, tenant_id="tenant", legacy_product_id=1, ecommerce_product_id=7)
            self.assertEqual((first["imported_assets"], first["imported_highlights"]), (1, 1))
            self.assertEqual((second["imported_assets"], second["already_analyzed"]), (0, 1))
            target = db.query(EcommerceVideoAsset).filter_by(asset_id="same-video").one()
            self.assertEqual(target.status, "analyzed")
            self.assertEqual(target.ai_tags, ["使用画面"])
            self.assertEqual(db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=target.id).count(), 1)
            self.assertEqual(db.query(EcommerceVideoHighlight).filter_by(asset_id=target.id).count(), 1)

    def test_existing_visual_proposal_accepts_manual_product_material(self):
        diagnosis = {
            "recommended_action": "VISUAL_REPLACE",
            "suggested_ranges": [{"start_ms": 6000, "end_ms": 10000}],
            "ai_proposal": {"requires_input": ["visual_material"]},
        }
        action = {
            "type": "VISUAL_REPLACE",
            "target_range": {"start_ms": 6000, "end_ms": 10000},
            "fit_mode": "COVER", "transition": {"type": "CUT", "duration_ms": 0},
            "material": {"ref_type": "VIDEO_ASSET", "ref_id": 2,
                         "source_range": {"start_ms": 0, "end_ms": 4000},
                         "content_hash": "b" * 64},
        }
        video_repair._validate_ai_draft(diagnosis, action, "")
        with self.sessions() as db:
            task = db.get(VideoRepairTask, 1)
            task.diagnosis_json = diagnosis
            updated = update_v4_draft(db, task, revision=1, action=action)
            self.assertEqual(updated.repair_plan_json["action"]["material"]["ref_id"], 2)

    def test_repair_shadow_product_is_hidden_from_product_management(self):
        with self.sessions() as db:
            db.add(Product(id=17, tenant_id="tenant", name="电商商品副本",
                           category="__ecommerce_repair_7__"))
            db.commit()
            auth = AuthContext(user_id="owner", tenant_id="tenant", role="owner",
                               email="owner@example.test", tenant_name="Tenant")
            visible_ids = {row.id for row in products.list_products(db, auth)}
            self.assertEqual(visible_ids, {1, 2})
            self.assertIsNotNone(db.get(Product, 17))

    def test_content_insert_can_reuse_selected_material_audio(self):
        with self.sessions() as db:
            task = db.get(VideoRepairTask, 1)
            task.diagnosis_json = {"recommended_action": "CONTENT_INSERT"}
            db.commit()
            action = {
                "type": "CONTENT_INSERT", "insert_at_ms": 6200, "duration_ms": 3000,
                "visual": {"ref_type": "VIDEO_ASSET", "ref_id": 2,
                           "source_range": {"start_ms": 0, "end_ms": 3000}},
                "audio": {"source_type": "VIDEO_ASSET", "ref_type": "VIDEO_ASSET", "ref_id": 2,
                          "source_range": {"start_ms": 0, "end_ms": 3000}},
            }
            updated = update_v4_draft(db, task, revision=1, action=action)
            self.assertEqual(updated.repair_plan_json["action"]["audio"]["content_hash"], "b" * 64)


if __name__ == "__main__":
    unittest.main()
