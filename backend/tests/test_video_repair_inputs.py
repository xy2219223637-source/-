from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.auth import AuthContext
from app.api.video_repair import _probe_repair_video, create_repair, upload_repair_source
from app.models import Product, Tenant, User, VideoAsset, VideoRepairInput
from app.schemas.video_repair import VideoRepairInputCreate, VideoRepairTaskCreate
from app.services.video_repair.ai_context import build_ai_context
from app.services.video_repair.inputs import create_input
from app.services.video_repair.migration import migrate_video_repair


class VideoRepairInputTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        startup = [table for table in Base.metadata.sorted_tables
                   if not table.info.get("explicit_migration_required")]
        Base.metadata.create_all(self.engine, tables=startup)
        migrate_video_repair(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(Tenant(id="tenant", name="Tenant", code="tenant"))
            db.add(User(id="owner", email="owner@example.test", name="Owner", password_hash="unused"))
            db.add(Product(id=1, tenant_id="tenant", name="Product"))
            db.add(VideoAsset(
                id=1, tenant_id="tenant", product_id=1, asset_id="asset-v1", filename="v1.mp4",
                source="smart_compose_output", library_type="finished", asset_kind="final_output",
                object_url="/v1.mp4", content_hash="a" * 64, duration_seconds=12,
                width=1080, height=1920, fps=30, has_audio=True, status="analyzed",
                reusable_status="review_required"))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_manual_input_is_created_without_qianchuan(self):
        with self.sessions() as db:
            row = create_input(
                db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                source_kind="MANUAL", audit_platform="INTERNAL", raw_reason="6 到 10 秒画面错误",
                suggestions=[], time_ranges=[{"start": 6, "end": 10}], raw_payload={})
            db.commit()
            self.assertEqual(row.source_kind, "MANUAL")
            self.assertEqual(row.time_ranges_json, [{"start": 6, "end": 10}])
            duplicate = create_input(
                db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                source_kind="MANUAL", audit_platform="INTERNAL", raw_reason="6 到 10 秒画面错误",
                suggestions=[], time_ranges=[{"start": 6, "end": 10}], raw_payload={})
            self.assertEqual(duplicate.id, row.id)

    def test_public_client_cannot_spoof_platform_input(self):
        with self.sessions() as db:
            with self.assertRaises(HTTPException) as raised:
                create_input(
                    db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                    source_kind="QIANCHUAN_PLATFORM", audit_platform="AD", raw_reason="拒审",
                    suggestions=[], time_ranges=[], raw_payload={})
            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(raised.exception.detail["code"], "INPUT_SOURCE_FORBIDDEN")

    def test_time_range_must_fit_source_asset(self):
        with self.sessions() as db:
            with self.assertRaises(HTTPException) as raised:
                create_input(
                    db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                    source_kind="MANUAL", audit_platform="INTERNAL", raw_reason="问题",
                    suggestions=[], time_ranges=[{"start": 10, "end": 13}], raw_payload={})
            self.assertEqual(raised.exception.detail["code"], "INVALID_TIME_RANGE")

    def test_new_input_uses_description_and_suggestions_only(self):
        payload = VideoRepairInputCreate.model_validate({
            "raw_reason": "6 到 10 秒画面错误", "suggestions": ["替换真实商品画面"],
            "expected_result": "旧字段", "time_ranges": [{"start": 1, "end": 2}],
            "category_hint": "COPY",
        })
        self.assertEqual(set(payload.model_dump()), {"raw_reason", "suggestions"})
        with self.sessions() as db:
            row = create_input(
                db, tenant_id="tenant", user_id="owner", source_asset_id=1,
                source_kind="MANUAL", audit_platform="INTERNAL",
                raw_reason=payload.raw_reason, suggestions=payload.suggestions,
                time_ranges=[], raw_payload={})
            context = build_ai_context(
                db, repair_input=row, source_asset=db.get(VideoAsset, 1), base_plan={})
            self.assertEqual(context["review"]["suggestions"], ["替换真实商品画面"])
            self.assertEqual(context["review"]["explicit_time_ranges"][0]["start_ms"], 6000)
            self.assertNotIn("expected_result", context["review"])
            self.assertEqual(context["review"]["category_hint"], "UNKNOWN")

    def test_local_upload_links_video_to_selected_product(self):
        import imageio_ffmpeg

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.mp4"
            subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=160x90:r=25",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", str(path),
            ], check=True, capture_output=True, timeout=30)
            video_bytes = path.read_bytes()
        storage = type("Storage", (), {"name": "aliyun_oss", "upload": AsyncMock(return_value="/uploaded.mp4")})()
        auth = AuthContext("owner", "tenant", "owner", "owner@example.test", "Tenant")
        with self.sessions() as db, patch("app.api.video_repair.get_video_storage", return_value=storage), \
                patch("app.services.director_workflow._ffprobe_executable", return_value=""), \
                patch("app.api.video_repair.get_settings", return_value=SimpleNamespace(
                    video_repair_v4_enabled=True, video_repair_async_worker_enabled=True,
                    director_video_max_upload_mb=100, video_library_max_duration_seconds=300)), \
                patch("app.services.material_library_repair.ensure_repair_product", return_value=db.get(Product, 1)), \
                patch("app.api.video_repair.enqueue_video_analysis") as enqueue:
            def verify_saved_before_analysis():
                self.assertEqual(storage.upload.await_count, 1)
                self.assertEqual(db.query(VideoAsset).filter_by(source="video_repair_upload").count(), 1)
            enqueue.side_effect = verify_saved_before_analysis
            result = asyncio.run(upload_repair_source(
                material_library_product_id=1,
                file=UploadFile(filename="v1.mp4", file=BytesIO(video_bytes)),
                db=db, auth=auth))
            asset = db.get(VideoAsset, result["id"])
            self.assertEqual(asset.product_id, 1)
            self.assertEqual(asset.asset_kind, "final_output")
            self.assertEqual(asset.source, "video_repair_upload")
            self.assertGreater(asset.duration_seconds, 0)
            self.assertTrue(asset.has_audio)
            self.assertEqual(storage.upload.await_count, 1)
            enqueue.assert_called_once()

    def test_create_task_persists_no_manual_hint_or_range(self):
        auth = AuthContext("owner", "tenant", "owner", "owner@example.test", "Tenant")
        payload = VideoRepairTaskCreate(source_asset_id=1, material_library_product_id=1,
                                        input=VideoRepairInputCreate(
            raw_reason="6 到 10 秒画面错误", suggestions=["替换真实画面"]))
        with self.sessions() as db, \
                patch("app.api.video_repair.get_settings", return_value=SimpleNamespace(
                    video_repair_v4_enabled=True, video_repair_async_worker_enabled=True)), \
                patch("app.services.material_library_repair.ensure_repair_product", return_value=db.get(Product, 1)), \
                patch("app.api.video_repair.create_v4_task") as create_v4_task, \
                patch("app.api.video_repair.serialize_task", return_value={"id": 42}):
            self.assertEqual(create_repair(payload, db=db, auth=auth), {"id": 42})
            row = db.query(VideoRepairInput).one()
            self.assertEqual(row.source_kind, "MANUAL")
            self.assertEqual(row.audit_platform, "INTERNAL")
            self.assertEqual(row.expected_result, "")
            self.assertEqual(row.time_ranges_json, [])
            self.assertEqual(row.raw_payload_json, {"material_library_product_id": 1})
            self.assertEqual(row.suggestions_json, ["替换真实画面"])
            create_v4_task.assert_called_once()

    def test_video_probe_without_system_ffprobe_preserves_audio(self):
        import imageio_ffmpeg

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.mp4"
            subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=160x90:r=25",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", str(path),
            ], check=True, capture_output=True, timeout=30)
            with patch("app.services.director_workflow._ffprobe_executable", return_value=""):
                media = _probe_repair_video(path)
            self.assertGreater(media["duration"], 0)
            self.assertEqual((media["width"], media["height"]), (160, 90))
            self.assertTrue(media["has_audio"])


if __name__ == "__main__":
    unittest.main()
