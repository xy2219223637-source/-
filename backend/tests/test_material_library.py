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
                                             material_library.MaterialLibraryRepairAssetLink.__table__])
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
        asset_id = uploaded.json()["id"]
        assert client.get(f"/api/material-library/products/{product_id}/assets").json()[0]["id"] == asset_id
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
        assert uploaded.json()["object_url"] in storage.deleted
    engine.dispose()


if __name__ == "__main__":
    test_standalone_products_and_tenant_scoped_assets()
