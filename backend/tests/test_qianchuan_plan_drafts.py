"""Local draft safety tests; all provider calls are read-only mocked GETs."""
from __future__ import annotations

import json
import unittest
import uuid

import test_qianchuan_auth as auth_fixture

from app.api.qianchuan_plans import router as plan_router
from app.models import (
    QianchuanAccountBinding as Binding,
    QianchuanConnection as Connection,
    QianchuanDeliveryPlan as Plan,
    QianchuanUpload as Upload,
)
from app.services.qianchuan_plan.client import AWEME_PATH, PRODUCT_PATH

PREFIX = auth_fixture.PREFIX


class QianchuanPlanDraftTests(unittest.TestCase):
    def setUp(self):
        self.fixture = auth_fixture.QianchuanAuthorizationTests(methodName="runTest")
        self.fixture.setUp()
        self.__dict__.update(self.fixture.__dict__)
        self.app.include_router(plan_router, prefix="/api")
        _, self.connection_id = self.fixture.completed()
        self.service.sync_connection(self.connection_id)
        with self.sessions() as db:
            binding = db.query(Binding).filter_by(connection_id=self.connection_id).one()
            self.binding_id = binding.id
            self.upload_id = str(uuid.uuid4())
            db.add(Upload(
                id=self.upload_id, tenant_id="team-a", created_by="owner", video_asset_id=1,
                binding_id=binding.id, advertiser_id=binding.advertiser_id,
                advertiser_name=binding.advertiser_name, source_content_hash="a" * 64,
                source_url="/api/storage/objects/test", filename="test.mp4", is_aigc=False,
                status="succeeded", material_id="7688613085071491098",
            ))
            db.commit()
        self.payload = {
            "video_asset_id": 1,
            "upload_id": self.upload_id,
            "binding_id": self.binding_id,
            "product_id": "9007199254740995",
            "aweme_id": "9007199254740994",
            "name": "只读校验后的本地草稿",
            "budget_minor": 10000,
            "roi_goal": "1.2500",
            "marketing_goal": "VIDEO_PROM_GOODS",
        }

    def tearDown(self):
        self.fixture.tearDown()

    def enable_write(self):
        self.settings.qianchuan_plan_enabled = True
        self.settings.qianchuan_plan_write_enabled = True

    def mock_valid_catalog(self, *, product_callback=None):
        self.custom[AWEME_PATH] = lambda request: self.fixture.envelope({
            "aweme_id_list": [{
                "aweme_id": 9007199254740994, "aweme_name": "测试抖音号",
                "aweme_show_id": "douyin-test", "aweme_status": "AWEME_STATUS_NORMAL",
                "aweme_has_video_permission": True, "aweme_has_live_permission": True,
                "aweme_has_publish_permission": False, "bind_type": ["OFFICIAL"],
            }],
            "page_info": {"page": 1, "page_size": 100, "total_number": 1, "total_page": 1},
        })

        def product(request):
            self.assertEqual(json.loads(request.url.params["filter"]), {
                "product_ids": [9007199254740995]})
            self.assertEqual(request.url.params["aweme_id"], "9007199254740994")
            if product_callback:
                product_callback()
            return self.fixture.envelope({
                "product_list": [{
                    "id": 9007199254740995, "name": "测试可投商品", "img": "",
                    "category_name": "护肤", "channel_type": "SHOP",
                }],
                "page_info": {"cursor": 1, "has_more": False},
            })
        self.custom[PRODUCT_PATH] = product

    def create(self, payload=None, headers=None):
        return self.browser.post(
            PREFIX + "/plans/drafts", json=payload or self.payload,
            headers=headers or self.headers)

    def test_create_and_explicit_update_store_only_verified_snapshots(self):
        self.enable_write()
        self.mock_valid_catalog()
        before = len(self.calls)
        created = self.create()
        self.assertEqual(created.status_code, 200, created.text)
        result = created.json()
        self.assertEqual(result["product_name"], "测试可投商品")
        self.assertEqual(result["aweme_name"], "测试抖音号")
        self.assertEqual(result["material_id"], "7688613085071491098")
        self.assertEqual(result["local_status"], "draft")
        provider_calls = self.calls[before:]
        self.assertEqual([call.method for call in provider_calls], ["GET", "GET"])
        self.assertEqual([call.url.path for call in provider_calls], [AWEME_PATH, PRODUCT_PATH])

        updated_payload = {**self.payload, "draft_id": result["id"],
                           "name": "更新后的草稿", "roi_goal": "1.5000"}
        updated = self.create(updated_payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["id"], result["id"])
        self.assertEqual(updated.json()["name"], "更新后的草稿")
        with self.sessions() as db:
            rows = db.query(Plan).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].credential_version,
                             db.get(Connection, self.connection_id).credential_version)
            self.assertEqual(rows[0].request_snapshot["roi_goal"], "1.5000")
            self.assertNotIn("access_token", rows[0].request_snapshot)

    def test_write_gate_and_failed_upload_stop_before_platform(self):
        self.settings.qianchuan_plan_enabled = True
        before = len(self.calls)
        response = self.create()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.calls), before)

    def test_invalid_values_and_missing_update_target_stop_before_platform(self):
        self.enable_write()
        before = len(self.calls)
        for changes in (
            {"name": "   "}, {"budget_minor": 0}, {"budget_minor": 100_000_001},
            {"roi_goal": "0"}, {"roi_goal": "1000.0001"}, {"unexpected": True},
        ):
            response = self.create({**self.payload, **changes})
            self.assertEqual(response.status_code, 422, (changes, response.text))
        response = self.create({**self.payload, "draft_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.calls), before)

        self.settings.qianchuan_plan_write_enabled = True
        with self.sessions() as db:
            db.get(Upload, self.upload_id).status = "failed"
            db.commit()
        response = self.create()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.calls), before)

    def test_cross_owner_chain_is_hidden_before_platform(self):
        self.enable_write()
        before = len(self.calls)
        response = self.create(headers=self.member_headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.calls), before)

    def test_unavailable_aweme_or_product_cannot_be_saved(self):
        self.enable_write()
        self.custom[AWEME_PATH] = lambda request: self.fixture.envelope({
            "aweme_id_list": [],
            "page_info": {"page": 1, "page_size": 100, "total_number": 0, "total_page": 0},
        })
        response = self.create()
        self.assertEqual(response.status_code, 422)
        self.assertFalse(any(call.url.path == PRODUCT_PATH for call in self.calls))
        with self.sessions() as db:
            self.assertEqual(db.query(Plan).count(), 0)

        self.mock_valid_catalog()
        self.custom[PRODUCT_PATH] = lambda request: self.fixture.envelope({
            "product_list": [], "page_info": {"cursor": 0, "has_more": False},
        })
        response = self.create()
        self.assertEqual(response.status_code, 422)
        with self.sessions() as db:
            self.assertEqual(db.query(Plan).count(), 0)

    def test_credential_change_during_catalog_validation_aborts_draft(self):
        self.enable_write()

        def rotate_credential():
            with self.sessions() as db:
                connection = db.get(Connection, self.connection_id)
                connection.credential_version += 1
                db.commit()

        self.mock_valid_catalog(product_callback=rotate_credential)
        response = self.create()
        self.assertEqual(response.status_code, 409, response.text)
        with self.sessions() as db:
            self.assertEqual(db.query(Plan).count(), 0)


if __name__ == "__main__":
    unittest.main()
