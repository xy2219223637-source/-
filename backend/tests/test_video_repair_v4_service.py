from __future__ import annotations

import unittest
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.api import video_repair
from app.auth import AuthContext
from app.models import (
    EcommerceProduct, EcommerceVideoAsset, Product, Tenant, User, VideoAsset,
    VideoRepairInput, VideoRepairPreview, VideoRepairTask,
)
from app.services.video_repair.migration import migrate_video_repair
from app.services.video_repair.migration_v2 import migrate_video_repair_v2
from app.services.video_repair.migration_v3 import migrate_video_repair_v3
from app.services.video_repair.protocol_v4 import plan_hash
from app.services.video_repair.service import serialize_task
from app.services.video_repair.v4_service import accept_preview, update_v4_draft
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
