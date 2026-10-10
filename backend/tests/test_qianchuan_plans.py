"""Sidecar plan gates and ownership tests; provider calls remain mocked/offline."""
from __future__ import annotations

from datetime import datetime, timedelta
import uuid
import unittest

import test_qianchuan_auth as auth_fixture

from app.api.qianchuan_plans import router as plan_router
from app.models import (QianchuanAccountBinding as Binding, QianchuanCapabilityProbe as Probe,
                        QianchuanConnection as Connection)
from app.services.qianchuan_plan.client import AWEME_PATH, PRODUCT_PATH

PREFIX = auth_fixture.PREFIX


class QianchuanPlanGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = auth_fixture.QianchuanAuthorizationTests(methodName="runTest")
        self.fixture.setUp()
        self.__dict__.update(self.fixture.__dict__)
        self.app.include_router(plan_router, prefix="/api")
        _, self.connection_id = self.fixture.completed()
        self.service.sync_connection(self.connection_id)
        with self.sessions() as db:
            self.binding_id = db.query(Binding).filter_by(connection_id=self.connection_id).one().id

    def tearDown(self):
        self.fixture.tearDown()

    def capability_url(self, binding_id=None):
        return PREFIX + "/plan-capabilities" + (f"?binding_id={binding_id}" if binding_id else "")

    def aweme_url(self, binding_id=None):
        return PREFIX + f"/accounts/{binding_id or self.binding_id}/awemes"

    def product_url(self, binding_id=None):
        return PREFIX + f"/accounts/{binding_id or self.binding_id}/products"

    def add_probe(self, name, conclusion="verified", *, credential_version=None, expires_delta=30):
        with self.sessions() as db:
            connection = db.get(Connection, self.connection_id)
            binding = db.get(Binding, self.binding_id)
            db.add(Probe(
                id=str(uuid.uuid4()), tenant_id="team-a", created_by="owner",
                connection_id=connection.id, binding_id=binding.id,
                advertiser_id=binding.advertiser_id,
                credential_version=(connection.credential_version if credential_version is None
                                    else credential_version),
                capability=name, conclusion=conclusion, http_status=200,
                probed_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(minutes=expires_delta),
            ))
            db.commit()

    def test_all_plan_gates_default_off_without_changing_account_access(self):
        response = self.browser.get(self.capability_url(self.binding_id), headers=self.headers)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual({key: result[key] for key in (
            "enabled", "write_enabled", "worker_enabled", "can_create")}, {
            "enabled": False, "write_enabled": False, "worker_enabled": False, "can_create": False})
        self.assertEqual(result["binding_id"], self.binding_id)
        self.assertGreaterEqual(result["credential_version"], 1)
        self.assertEqual(result["capabilities"], [])
        self.assertEqual(self.browser.get(PREFIX + "/accounts", headers=self.headers).status_code, 200)

    def test_gate_hierarchy_and_verified_capabilities_are_server_controlled(self):
        for name in ("authorized_awemes", "available_products", "plan_list:VIDEO_PROM_GOODS"):
            self.add_probe(name)
        self.settings.qianchuan_plan_enabled = True
        result = self.browser.get(self.capability_url(self.binding_id), headers=self.headers).json()
        self.assertTrue(result["enabled"])
        self.assertFalse(result["write_enabled"])
        self.assertFalse(result["can_create"])
        self.assertEqual({item["name"] for item in result["capabilities"]}, {
            "authorized_awemes", "available_products", "plan_list:VIDEO_PROM_GOODS"})

        self.settings.qianchuan_plan_write_enabled = True
        result = self.browser.get(self.capability_url(self.binding_id), headers=self.headers).json()
        self.assertTrue(result["write_enabled"])
        self.assertTrue(result["can_create"])
        self.assertFalse(result["worker_enabled"])

        self.settings.qianchuan_plan_worker_enabled = True
        result = self.browser.get(self.capability_url(self.binding_id), headers=self.headers).json()
        self.assertTrue(result["worker_enabled"])

    def test_stale_wrong_version_and_non_verified_probes_do_not_open_create_gate(self):
        self.settings.qianchuan_plan_enabled = True
        self.settings.qianchuan_plan_write_enabled = True
        self.add_probe("authorized_awemes", credential_version=0)
        self.add_probe("available_products", expires_delta=-1)
        self.add_probe("plan_list:VIDEO_PROM_GOODS", conclusion="no_sample_data")
        result = self.browser.get(self.capability_url(self.binding_id), headers=self.headers).json()
        self.assertFalse(result["can_create"])
        self.assertEqual([item["name"] for item in result["capabilities"]],
                         ["plan_list:VIDEO_PROM_GOODS"])

    def test_other_users_binding_is_hidden(self):
        with self.sessions() as db:
            db.add(Connection(id="other-connection", tenant_id="team-a", created_by="member",
                              status="active", credential_version=1))
            db.add(Binding(id="00000000-0000-4000-8000-000000000009", tenant_id="team-a",
                           connection_id="other-connection", source_account_id="900",
                           source_account_type="SHOP", advertiser_id="901", is_valid=True))
            db.commit()
        response = self.browser.get(
            self.capability_url("00000000-0000-4000-8000-000000000009"), headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.settings.qianchuan_plan_enabled = True
        before = len(self.calls)
        response = self.browser.get(
            self.aweme_url("00000000-0000-4000-8000-000000000009"), headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.calls), before)

    def test_catalog_endpoints_are_hidden_and_make_no_call_when_gate_is_off(self):
        before = len(self.calls)
        awemes = self.browser.get(self.aweme_url(), headers=self.headers)
        products = self.browser.get(self.product_url(), headers=self.headers)
        self.assertEqual((awemes.status_code, products.status_code), (404, 404))
        self.assertEqual(len(self.calls), before)

    def test_awemes_are_normalized_and_keyword_searches_all_provider_pages(self):
        self.settings.qianchuan_plan_enabled = True

        def aweme_page(request):
            page = int(request.url.params["page"])
            self.assertEqual(request.url.params["advertiser_id"], "9007199254740993")
            rows = [{
                "aweme_id": 9007199254740994 + page,
                "aweme_name": "其他账号" if page == 1 else "目标账号",
                "aweme_show_id": "show-other" if page == 1 else "show-target",
                "aweme_avatar": "https://example.test/avatar.png",
                "aweme_status": "AWEME_STATUS_NORMAL",
                "aweme_has_live_permission": True,
                "aweme_has_video_permission": page == 2,
                "aweme_has_publish_permission": False,
                "bind_type": ["OFFICIAL"],
            }]
            return self.fixture.envelope({"aweme_id_list": rows, "page_info": {
                "page": page, "page_size": int(request.url.params["page_size"]),
                "total_number": 2, "total_page": 2,
            }})

        self.custom[AWEME_PATH] = aweme_page
        response = self.browser.get(
            self.aweme_url(), params={"keyword": "目标", "page": 1, "page_size": 10},
            headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["total_number"], 1)
        self.assertEqual(result["items"][0]["id"], "9007199254740996")
        self.assertTrue(result["items"][0]["has_video_permission"])
        self.assertEqual(result["items"][0]["bind_types"], ["OFFICIAL"])
        calls = [call for call in self.calls if call.url.path == AWEME_PATH]
        self.assertEqual([call.url.params["page"] for call in calls], ["1", "2"])
        self.assertTrue(all(call.url.params["page_size"] == "100" for call in calls))
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_products_use_provider_filter_and_cursor_without_leaking_numeric_ids(self):
        self.settings.qianchuan_plan_enabled = True

        def products(request):
            self.assertEqual(request.url.params["advertiser_id"], "9007199254740993")
            self.assertEqual(request.url.params["aweme_id"], "9007199254740998")
            self.assertEqual(request.url.params["cursor"], "27")
            self.assertNotIn("page", request.url.params)
            self.assertEqual(request.url.params["filter"], '{"product_name":"面膜"}')
            self.assertEqual(request.url.params["campaign_scene"], "VIDEO_PROM_GOODS")
            return self.fixture.envelope({
                "product_list": [{
                    "id": 9007199254740997, "name": "补水面膜", "img": "https://example.test/p.png",
                    "category_name": "护肤", "channel_type": "DOU_PLUS",
                }],
                "page_info": {"cursor": 31, "has_more": True},
            })

        self.custom[PRODUCT_PATH] = products
        response = self.browser.get(self.product_url(), params={
            "aweme_id": "9007199254740998", "keyword": "面膜", "cursor": 27, "page_size": 10,
        }, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "items": [{"id": "9007199254740997", "name": "补水面膜",
                       "image_url": "https://example.test/p.png", "category_name": "护肤",
                       "channel_type": "DOU_PLUS"}],
            "cursor": 31, "has_more": True, "page_size": 10,
        })

    def test_catalog_rejects_expiring_token_before_platform_call(self):
        self.settings.qianchuan_plan_enabled = True
        with self.sessions() as db:
            connection = db.get(Connection, self.connection_id)
            connection.access_token_expires_at = datetime.utcnow() + timedelta(minutes=4)
            db.commit()
        before = len(self.calls)
        response = self.browser.get(self.aweme_url(), headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.calls), before)

    def test_malformed_platform_catalog_response_fails_closed(self):
        self.settings.qianchuan_plan_enabled = True
        self.custom[PRODUCT_PATH] = lambda request: self.fixture.envelope({
            "product_list": {}, "page_info": {"cursor": 0, "has_more": False},
        })
        response = self.browser.get(self.product_url(), headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("product_list", response.text)
