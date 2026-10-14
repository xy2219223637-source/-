from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import ecommerce_products, ecommerce_video_library, ecommerce_video_taxonomy
from app.auth import AuthContext, current_auth
from app.database import Base, get_db
from app.models import EcommerceProduct, EcommerceVideoAsset, EcommerceVideoAssetAnalysis, EcommerceVideoHighlight
from app.services import video_analysis_queue


class EcommerceVideoLibraryTests(unittest.TestCase):
    def test_material_and_final_library_are_isolated(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        tables = [table for name, table in Base.metadata.tables.items() if name.startswith("ecommerce_")]
        Base.metadata.create_all(engine, tables=tables)
        assert {fk["referred_table"] for fk in inspect(engine).get_foreign_keys("ecommerce_video_assets")} == {
            "ecommerce_products", "ecommerce_video_categories"
        }
        assert "video_assets" not in inspect(engine).get_table_names()

        tenant = {"id": "tenant-a"}
        app = FastAPI()
        for router in (ecommerce_products.router, ecommerce_video_library.router, ecommerce_video_taxonomy.router):
            app.include_router(router, prefix="/api")

        def database():
            with Session(engine) as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[current_auth] = lambda: AuthContext("user", tenant["id"], "owner", "a@example.com", "Test")

        class Storage:
            name = "test"
            uploaded_paths: list[Path] = []

            async def upload_path(self, path, filename, subdir):
                assert subdir.startswith("ecommerce-video-library/")
                assert path.read_bytes() == b"video-" + filename.encode()
                self.uploaded_paths.append(path)
                return f"/{subdir}/{filename}"

            async def begin_multipart(self, filename, subdir, content_type):
                assert subdir.startswith("ecommerce-video-library/")
                return "ecommerce/multipart/final.mp4", "upload-id"

            def signed_part_url(self, key, upload_id, number):
                return f"https://upload.example/{number}"

            async def complete_multipart(self, key, upload_id):
                return "/ecommerce/multipart/final.mp4", 5, "etag"

        patches = [
            patch.object(ecommerce_video_library, "_tenant_video_storage", return_value=Storage()),
            patch.object(ecommerce_video_library, "get_storage_by_name", return_value=Storage()),
            patch.object(ecommerce_video_library, "_probe", return_value={"duration": 8.0, "width": 1920, "height": 1080, "fps": 25.0, "has_audio": True}),
            patch.object(ecommerce_video_library, "enqueue_video_analysis"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        client = TestClient(app)
        product = client.post("/api/ecommerce-products", json={"name": "电商商品"})
        assert product.status_code == 200
        product_id = product.json()["id"]

        def upload(name: str, kind: str):
            return client.post("/api/ecommerce-video-assets", data={"product_id": str(product_id), "library_type": "finished", "asset_kind": kind}, files={"files": (name, b"video-" + name.encode(), "video/mp4")})

        material = upload("material.mp4", "material")
        final = upload("final.mp4", "final_output")
        assert material.status_code == final.status_code == 200, (material.text, final.text)
        assert material.json()[0]["status"] == "queued_for_analysis"
        assert final.json()[0]["status"] == "uploaded"
        assert len(Storage.uploaded_paths) == 2
        assert all(not path.exists() for path in Storage.uploaded_paths)
        with patch.object(ecommerce_video_library, "get_settings", return_value=SimpleNamespace(ecommerce_video_max_upload_mb=1)):
            oversized = client.post("/api/ecommerce-video-assets", data={"product_id": str(product_id), "asset_kind": "material"},
                files={"files": ("large.mp4", b"x" * (1024 * 1024 + 1), "video/mp4")})
        assert oversized.status_code == 413
        assert len(Storage.uploaded_paths) == 2
        material_id, final_id = material.json()[0]["id"], final.json()[0]["id"]
        assert client.post(f"/api/ecommerce-video-assets/{final_id}/reanalyze").status_code == 422
        assert client.post(f"/api/ecommerce-video-assets/{material_id}/reanalyze").json()["status"] == "queued_for_analysis"
        with Session(engine) as db:
            db.get(EcommerceVideoAsset, material_id).status = "analyzing"
            db.commit()
        running = client.post(f"/api/ecommerce-video-assets/{material_id}/reanalyze")
        assert running.status_code == 200 and running.json()["status"] == "analyzing"
        with Session(engine) as db:
            db.get(EcommerceVideoAsset, material_id).status = "queued_for_analysis"
            db.commit()
        assert [row["id"] for row in client.get("/api/ecommerce-video-assets", params={"product_id": product_id, "asset_kind": "material"}).json()] == [material_id]
        assert [row["id"] for row in client.get("/api/ecommerce-video-assets", params={"product_id": product_id, "asset_kind": "final_output"}).json()] == [final_id]
        assert client.get(f"/api/ecommerce-video-assets/{final_id}").json()["asset_kind"] == "final_output"
        assert client.put(f"/api/ecommerce-video-assets/{material_id}/reusable-status", json={"reusable_status": "reusable"}).status_code == 200
        assert client.put(f"/api/ecommerce-video-assets/{final_id}/reusable-status", json={"reusable_status": "reusable"}).status_code == 422
        assert client.put(f"/api/ecommerce-video-assets/{material_id}/tags", json={"manual_tags": ["卖点"], "removed_ai_tags": []}).json()["effective_tags"] == ["卖点"]
        assert client.post(f"/api/ecommerce-video-assets/{material_id}/reanalyze").status_code == 200
        with patch.object(video_analysis_queue, "SessionLocal", side_effect=lambda: Session(engine)):
            assert video_analysis_queue._next_ecommerce_asset_id() == material_id
        catalog = client.get("/api/ecommerce-video-taxonomy/catalog", params={"object_type": "material"})
        assert catalog.status_code == 200 and catalog.json()["categories"]
        final_catalog = client.get("/api/ecommerce-video-taxonomy/catalog", params={"object_type": "final_output"})
        assert final_catalog.status_code == 200 and final_catalog.json()["categories"]
        material_category = next(row["id"] for row in catalog.json()["categories"] if row["parent_id"] is not None)
        assigned = client.put("/api/ecommerce-video-taxonomy/assets/batch", json={"asset_ids": [material_id], "set_category": True, "category_id": material_category})
        assert assigned.status_code == 200, assigned.text
        assert client.get(f"/api/ecommerce-video-assets/{material_id}").json()["category_id"] == material_category
        wrong_library = client.put("/api/ecommerce-video-taxonomy/assets/batch", json={"asset_ids": [final_id], "set_category": True, "category_id": material_category})
        assert wrong_library.status_code == 422
        app.dependency_overrides[current_auth] = lambda: AuthContext(
            "user", tenant["id"], "employee", "a@example.com", "Test",
            permissions=("ecommerce_assets.access",),
        )
        assert client.put("/api/ecommerce-video-taxonomy/assets/batch", json={
            "asset_ids": [material_id], "set_category": True, "category_id": material_category,
        }).status_code == 200
        app.dependency_overrides[current_auth] = lambda: AuthContext(
            "user", tenant["id"], "employee", "a@example.com", "Test",
            permissions=("finished_assets.access", "final_videos.access"),
        )
        assert client.put("/api/ecommerce-video-taxonomy/assets/batch", json={
            "asset_ids": [material_id], "set_category": True, "category_id": material_category,
        }).status_code == 403
        app.dependency_overrides[current_auth] = lambda: AuthContext("user", tenant["id"], "owner", "a@example.com", "Test")
        with Session(engine) as db:
            assert db.query(EcommerceProduct).count() == 1
            assert db.query(EcommerceVideoAsset).count() == 2
            assert db.query(EcommerceVideoAssetAnalysis).count() == 0

        tenant["id"] = "tenant-b"
        assert client.get("/api/ecommerce-video-assets").json() == []
        assert client.get(f"/api/ecommerce-video-assets/{material_id}").status_code == 404
        tenant["id"] = "tenant-a"
        assert client.post("/api/ecommerce-video-assets", data={"product_id": product_id, "library_type": "semi_finished", "asset_kind": "final_output"}, files={"files": ("bad.mp4", b"bad", "video/mp4")}).status_code == 422
        started = client.post("/api/ecommerce-video-assets/multipart/start", json={"product_id": product_id, "filename": "final2.mp4", "content_type": "video/mp4", "size_bytes": 5, "library_type": "finished", "asset_kind": "final_output"})
        assert started.status_code == 200, started.text
        completed = client.post("/api/ecommerce-video-assets/multipart/complete", json={"session_id": started.json()["session_id"]})
        assert completed.status_code == 200, completed.text
        assert completed.json()["asset_kind"] == "final_output"
        assert len(client.get("/api/ecommerce-video-assets", params={"product_id": product_id, "asset_kind": "final_output"}).json()) == 2

        class AnalysisStorage:
            def signed_url(self, url):
                return "https://video.example/test.mp4"

        class AnalysisClient:
            async def chat_json(self, *args, **kwargs):
                return {"summary": "商品展示与讲解", "video_tags": ["商品展示"], "highlights": [
                    {"start": 0, "end": 4, "description": "商品特写", "track_role": "b_roll", "content_role": "product_demo"}
                ]}

        with Session(engine) as db:
            row = db.get(EcommerceVideoAsset, material_id)
            row.has_audio = False
            db.commit()
        with patch.object(ecommerce_video_library, "SessionLocal", side_effect=lambda: Session(engine)), \
             patch.object(ecommerce_video_library, "_asset_storage", return_value=AnalysisStorage()), \
             patch.object(ecommerce_video_library, "LLMClient", return_value=AnalysisClient()):
            asyncio.run(ecommerce_video_library.analyze_video_asset(material_id))
        with Session(engine) as db:
            assert db.get(EcommerceVideoAsset, material_id).status == "analyzed"
            assert db.query(EcommerceVideoAssetAnalysis).filter_by(asset_id=material_id, status="completed").count() == 1
            assert db.query(EcommerceVideoHighlight).filter_by(asset_id=material_id).count() >= 1
        engine.dispose()
