from __future__ import annotations

from io import BytesIO
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import ecommerce_products, products
from app.auth import AuthContext, current_auth
from app.database import Base, get_db
from app.models import EcommerceProduct, EcommerceProductAsset, EcommerceProductFact, Product, ProductAsset, ProductFact


class EcommerceProductsTests(unittest.TestCase):
    def test_crud_and_physical_isolation(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[Product.__table__, ProductAsset.__table__, ProductFact.__table__, EcommerceProduct.__table__, EcommerceProductAsset.__table__, EcommerceProductFact.__table__])
        assert {"products", "product_assets", "ecommerce_products", "ecommerce_product_assets", "ecommerce_product_facts"} <= set(inspect(engine).get_table_names())
        assert {fk["referred_table"] for fk in inspect(engine).get_foreign_keys("ecommerce_product_assets")} == {"ecommerce_products"}
        assert {fk["referred_table"] for fk in inspect(engine).get_foreign_keys("ecommerce_product_facts")} == {"ecommerce_products"}

        tenant = {"id": "tenant-a"}
        app = FastAPI()
        app.include_router(products.router, prefix="/api")
        app.include_router(ecommerce_products.router, prefix="/api")

        def database():
            with Session(engine) as db:
                yield db

        def auth():
            return AuthContext("user", tenant["id"], "owner", "a@example.com", "Test")

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[current_auth] = auth

        class Storage:
            async def upload(self, content, filename, subdir):
                assert subdir.startswith("ecommerce/")
                return f"/{subdir}/{filename}"

            async def delete(self, url):
                assert url.startswith("/ecommerce/")

        storage_patch = patch.object(ecommerce_products, "get_storage", return_value=Storage())
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        inspection_patch = patch.object(ecommerce_products, "inspect_source", return_value={"status": "passed", "score": 100, "issues": []})
        inspection_patch.start()
        self.addCleanup(inspection_patch.stop)
        client = TestClient(app)
        old = client.post("/api/products", json={"name": "旧商品"})
        new = client.post("/api/ecommerce-products", json={"name": "电商商品", "brand_name": "品牌"})
        assert old.status_code == new.status_code == 200
        old_id, new_id = old.json()["id"], new.json()["id"]
        assert [p["name"] for p in client.get("/api/products").json()] == ["旧商品"]
        assert [p["name"] for p in client.get("/api/ecommerce-products").json()] == ["电商商品"]
        assert client.put(f"/api/ecommerce-products/{new_id}", json={"price": 99.5}).json()["price"] == 99.5
        facts = client.get(f"/api/ecommerce-products/{new_id}/facts")
        assert facts.status_code == 200 and len(facts.json()) == 8
        fact = client.put(f"/api/ecommerce-products/{new_id}/facts", json={"fact_key": "brand_name", "label": "品牌", "value": "新品牌"})
        assert fact.status_code == 200
        assert client.post(f"/api/ecommerce-products/{new_id}/facts/{fact.json()['id']}/confirm").json()["status"] == "confirmed"
        assert client.get(f"/api/ecommerce-products/{new_id}/readiness").json()["status"] == "blocked"

        image = BytesIO()
        Image.new("RGB", (2, 2), "red").save(image, "PNG")
        uploaded = client.post(f"/api/ecommerce-products/{new_id}/assets", files={"files": ("test.png", image.getvalue(), "image/png")})
        assert uploaded.status_code == 200, uploaded.text
        asset_id = uploaded.json()[0]["id"]
        assert client.put(f"/api/ecommerce-products/{new_id}/assets/{asset_id}", json={"description": "正面图", "material_role": "product", "priority": 1, "locked": True, "excluded": False, "benchmark_role": "product_front", "tags": [], "protection": {}}).status_code == 200
        assert len(client.get(f"/api/ecommerce-products/{new_id}/assets").json()) == 1
        assert client.get(f"/api/ecommerce-products/{new_id}/image-admission").status_code == 200
        assert client.get(f"/api/products/{old_id}/assets").json() == []
        with Session(engine) as db:
            assert db.query(Product).count() == db.query(EcommerceProduct).count() == 1
            assert db.query(ProductAsset).count() == 0
            assert db.query(ProductFact).count() == 0
            assert db.query(EcommerceProductAsset).count() == 1
            assert db.query(EcommerceProductFact).count() == 8

        tenant["id"] = "tenant-b"
        assert client.get("/api/ecommerce-products").json() == []
        assert client.get(f"/api/ecommerce-products/{new_id}").status_code == 404
        assert client.get(f"/api/ecommerce-products/{new_id}/assets").status_code == 404
        assert client.get(f"/api/ecommerce-products/{new_id}/facts").status_code == 404
        tenant["id"] = "tenant-a"
        assert client.delete(f"/api/ecommerce-products/{new_id}/assets/{asset_id}").status_code == 200
        assert client.delete(f"/api/ecommerce-products/{new_id}").status_code == 200
        assert client.get("/api/ecommerce-products").json() == []
        assert [p["name"] for p in client.get("/api/products").json()] == ["旧商品"]
        with Session(engine) as db:
            assert db.query(EcommerceProductFact).count() == 0
        engine.dispose()
