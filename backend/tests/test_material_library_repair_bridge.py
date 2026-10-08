from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import AuthContext
from app.api.products import list_products
from app.api.video_repair import _validate_ai_draft, create_repair
from app.database import Base
from app.models import (MaterialLibraryAsset, MaterialLibraryProduct,
                        MaterialLibraryRepairAssetLink, MaterialLibraryRepairProductLink,
                        Product, VideoAsset)
from app.services.material_library_repair import ensure_repair_product, sync_repair_videos
from app.schemas.video_repair import VideoRepairInputCreate, VideoRepairTaskCreate
from pydantic import ValidationError


class MaterialLibraryRepairBridgeTests(unittest.TestCase):
    def test_legacy_routes_are_not_exposed_and_library_product_is_required(self):
        from app.main import app

        paths = set(app.openapi()["paths"])
        self.assertIn("/api/video-repair-tasks/{task_id}/draft", paths)
        for suffix in ("/candidates/select", "/draft-plan", "/approve", "/render"):
            self.assertNotIn(f"/api/video-repair-tasks/{{task_id}}{suffix}", paths)
        self.assertNotIn("/api/video-repair-assets", paths)
        with self.assertRaises(ValidationError):
            VideoRepairTaskCreate(source_asset_id=1,
                                  input=VideoRepairInputCreate(raw_reason="画面不准确"))

    def test_library_task_never_falls_back_to_legacy_worker(self):
        payload = VideoRepairTaskCreate(
            source_asset_id=1, material_library_product_id=1,
            input=VideoRepairInputCreate(raw_reason="画面不准确"))
        auth = AuthContext("user", "tenant", "owner", "u@test.local", "Tenant")
        with patch("app.api.video_repair.get_settings", return_value=SimpleNamespace(
                video_repair_v4_enabled=False, video_repair_async_worker_enabled=False)):
            with self.assertRaises(HTTPException) as raised:
                create_repair(payload, db=None, auth=auth)
        self.assertEqual(raised.exception.status_code, 503)

    def test_manual_material_choice_is_allowed_only_when_ai_requires_it(self):
        template = {"type": "VISUAL_REPLACE", "target_range": {"start_ms": 2000, "end_ms": 5000}}
        choice = {**template, "material": {
            "ref_type": "VIDEO_ASSET", "ref_id": 31,
            "source_range": {"start_ms": 1000, "end_ms": 4000},
        }}
        _validate_ai_draft({"ai_proposal": {
            "action_template": template, "requires_input": ["visual_material"]}}, choice, "")

    def test_library_product_and_video_are_available_only_through_repair_bridge(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[
            Product.__table__, MaterialLibraryProduct.__table__, MaterialLibraryAsset.__table__,
            MaterialLibraryRepairProductLink.__table__, MaterialLibraryRepairAssetLink.__table__,
            VideoAsset.__table__,
        ])
        sessions = sessionmaker(bind=engine)
        with tempfile.TemporaryDirectory() as directory, sessions() as db:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(b"video")
            library = MaterialLibraryProduct(tenant_id="tenant", image_url="/cover.png", introduction="湿巾")
            db.add(library)
            db.flush()
            asset = MaterialLibraryAsset(
                tenant_id="tenant", product_id=library.id, kind="video", filename="clip.mp4",
                object_url="/clip.mp4", storage_provider="aliyun_oss", size_bytes=5,
                content_hash="a" * 64, created_by="user")
            db.add(asset)
            db.commit()
            product = ensure_repair_product(db, tenant_id="tenant", library_product_id=library.id)
            self.assertEqual(product.description, "湿巾")
            auth = AuthContext("user", "tenant", "owner", "u@test.local", "Tenant")
            self.assertEqual(list_products(db=db, auth=auth), [])

            class Storage:
                def local_path(self, _url):
                    return path

            with patch("app.services.material_library_repair.get_storage_for_url", return_value=Storage()), \
                 patch("app.api.video_repair._probe_repair_video", return_value={
                     "duration": 8.0, "width": 1080, "height": 1920, "fps": 30,
                     "has_audio": True}):
                first = sync_repair_videos(db, tenant_id="tenant", repair_product_id=product.id)
                second = sync_repair_videos(db, tenant_id="tenant", repair_product_id=product.id)
            self.assertEqual(len(first), 1)
            self.assertEqual(second[0].id, first[0].id)
            self.assertEqual(first[0].library_type, "repair_only")
            self.assertEqual(first[0].content_hash, asset.content_hash)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
