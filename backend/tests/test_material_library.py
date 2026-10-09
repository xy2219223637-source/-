from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from app.api import material_library
from app.auth import AuthContext, current_auth
from app.database import Base, get_db
from app.models import (MaterialLibraryRepairProductLink, Product, VideoAsset,
                        VideoAssetAnalysis, VideoHighlight)


class FakeStorage:
    name = "aliyun_oss"

    def __init__(self):
        self.uploads = []
        self.deleted = []

    async def upload_path(self, path, filename, subdir):
        self.uploads.append((filename, subdir, path.read_bytes()))
        return f"/api/storage/objects/{len(self.uploads)}"

    async def delete(self, url):
        self.deleted.append(url)


def test_standalone_products_and_tenant_scoped_assets():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[material_library.MaterialLibraryProduct.__table__,
                                             material_library.MaterialLibraryAsset.__table__,
                                             material_library.MaterialLibraryRepairAssetLink.__table__,
                                             MaterialLibraryRepairProductLink.__table__, Product.__table__,
                                             VideoAsset.__table__, VideoAssetAnalysis.__table__, VideoHighlight.__table__])
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(material_library.router, prefix="/api")
    tenant = {"id": "tenant-a"}

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_auth] = lambda: AuthContext(
        user_id="user", tenant_id=tenant["id"], role="owner", email="u@test.local", tenant_name="Test")
    storage = FakeStorage()
    with patch.object(material_library, "get_storage", return_value=storage), \
         patch.object(material_library, "get_storage_by_name", return_value=storage), \
         TestClient(app) as client:
        image = b"\x89PNG\r\n\x1a\n" + b"image"
        created = client.post("/api/material-library/products", data={"introduction": "  产品介绍  "},
                              files={"image": ("cover.png", image, "image/png")})
        assert created.status_code == 201, created.text
        product_id = created.json()["id"]
        assert created.json()["introduction"] == "产品介绍"
        assert len(storage.uploads) == 1

        video = b"\x1a\x45\xdf\xa3" + b"video"
        uploaded = client.post(f"/api/material-library/products/{product_id}/assets",
                               files={"file": ("shot.webm", video, "video/webm")})
        assert uploaded.status_code == 201, uploaded.text
        assert uploaded.json()["kind"] == "video"
        assert uploaded.json()["analysis_status"] == "validating"
        asset_id = uploaded.json()["id"]
        assert client.get(f"/api/material-library/products/{product_id}/assets").json()[0]["id"] == asset_id
        with sessions() as db:
            link = db.query(material_library.MaterialLibraryRepairAssetLink).filter_by(library_asset_id=asset_id).one()
            video_row = db.get(VideoAsset, link.video_asset_id)
            video_row.duration_seconds = 8
            video_row.status = "analyzed"
            analysis = VideoAssetAnalysis(tenant_id="tenant-a", asset_id=video_row.id,
                status="completed", summary="展示商品", analysis_json={"highlights": [{
                    "highlight_id": "segment-1", "start": 1.0, "end": 4.0,
                    "description": "展示商品", "product_visibility": "clear", "audio_mode": "mute_ok"}]})
            db.add(analysis);db.flush()
            db.add(VideoHighlight(tenant_id="tenant-a", asset_id=video_row.id,
                analysis_id=analysis.id, highlight_id="segment-1", start_seconds=1, end_seconds=4,
                description="展示商品"))
            db.commit()
        corrected = client.patch(f"/api/material-library/products/{product_id}/assets/{asset_id}/segments/segment-1",
            json={"start_ms": 1200, "end_ms": 4600, "description": "打开瓶盖"})
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["segments"][0]["description"] == "打开瓶盖"
        assert corrected.json()["segments"][0]["start"] == 1.2
        assert client.patch(f"/api/material-library/products/{product_id}/assets/{asset_id}/segments/segment-1",
            json={"start_ms": 7000, "end_ms": 9000, "description": "越界"}).status_code == 422
        retried = client.post(f"/api/material-library/products/{product_id}/assets",
                              files={"file": ("shot.webm", video, "video/webm")})
        assert retried.status_code == 201
        assert retried.json()["id"] == asset_id
        assert len(storage.uploads) == 2

        bad = client.post(f"/api/material-library/products/{product_id}/assets",
                          files={"file": ("fake.png", b"not-an-image", "image/png")})
        assert bad.status_code == 415
        assert len(storage.uploads) == 2

        tenant["id"] = "tenant-b"
        assert client.get("/api/material-library/products").json() == []
        assert client.get(f"/api/material-library/products/{product_id}").status_code == 404
        assert client.delete(f"/api/material-library/products/{product_id}/assets/{asset_id}").status_code == 404

        tenant["id"] = "tenant-a"
        assert client.delete(f"/api/material-library/products/{product_id}/assets/{asset_id}").status_code == 204
        assert client.get(f"/api/material-library/products/{product_id}/assets").json() == []
        # Linked media remains available to already-created repair previews.
        assert uploaded.json()["object_url"] not in storage.deleted
    engine.dispose()


def test_finished_videos_only_include_outputs_for_selected_product():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[material_library.MaterialLibraryProduct.__table__,
                                             MaterialLibraryRepairProductLink.__table__,
                                             Product.__table__, VideoAsset.__table__])
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([
            material_library.MaterialLibraryProduct(id=1, tenant_id="tenant-a", image_url="cover", introduction="A"),
            material_library.MaterialLibraryProduct(id=2, tenant_id="tenant-a", image_url="cover", introduction="B"),
            Product(id=10, tenant_id="tenant-a", name="A"),
            Product(id=20, tenant_id="tenant-a", name="B"),
        ])
        db.add_all([
            MaterialLibraryRepairProductLink(tenant_id="tenant-a", library_product_id=1, repair_product_id=10),
            MaterialLibraryRepairProductLink(tenant_id="tenant-a", library_product_id=2, repair_product_id=20),
        ])
        for asset_id, product_id, source in [(1, 10, "video_repair_upload"),
                                              (2, 10, "review_repair_v4_output"),
                                              (3, 20, "review_repair_v4_output")]:
            db.add(VideoAsset(tenant_id="tenant-a", product_id=product_id, asset_id=f"asset-{asset_id}",
                              filename=f"{asset_id}.mp4", source=source, library_type="finished",
                              asset_kind="final_output", object_url=f"/video/{asset_id}", content_hash=str(asset_id)))
        db.commit()
    app = FastAPI()
    app.include_router(material_library.router, prefix="/api")
    tenant = {"id": "tenant-a"}

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_auth] = lambda: AuthContext(
        user_id="user", tenant_id=tenant["id"], role="owner", email="u@test.local", tenant_name="Test")
    with TestClient(app) as client:
        response = client.get("/api/material-library/products/1/finished-videos")
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [2]
        assert [row["id"] for row in client.get("/api/material-library/products/2/finished-videos").json()] == [3]
        tenant["id"] = "tenant-b"
        assert client.get("/api/material-library/products/1/finished-videos").status_code == 404
    engine.dispose()


if __name__ == "__main__":
    test_standalone_products_and_tenant_scoped_assets()
    test_finished_videos_only_include_outputs_for_selected_product()
