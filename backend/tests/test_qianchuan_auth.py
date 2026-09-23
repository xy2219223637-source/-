"""Synthetic OAuth/SDK contract fixtures; no real credentials or outbound requests."""
from __future__ import annotations

import json
import logging
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.qianchuan import router
from app.auth import create_token
from app.config import Settings
from app.database import Base, get_db
from app.models import (Product, VideoAsset, Tenant, User, TenantMember, TenantRole,
                        QianchuanConnection as Connection, QianchuanOAuthSession as OAuthSession,
                        QianchuanAccountBinding as Binding)
from app.services.qianchuan.client import QianchuanClient, PlatformError, AGENT_ROLE, SHOP_ROLE
from app.services.qianchuan.service import QianchuanService, get_qianchuan_service, SensitiveLogFilter, digest

PREFIX = "/api/qianchuan"
A01 = "/open_api/oauth2/access_token/"
A02 = "/open_api/oauth2/advertiser/get/"
A03 = "/open_api/oauth2/refresh_token/"
A04 = "/open_api/v1.0/qianchuan/shop/advertiser/list/"
A05 = "/open_api/2/agent/advertiser/select/"
A08 = "/open_api/v1.0/qianchuan/advertiser/type/get/"
A09 = "/open_api/v3.0/customer_center/account/list/"


class QianchuanAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.directory.name) / "test.db"),
                                    connect_args={"check_same_thread": False, "timeout": 30})
        self.sessions = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.settings = Settings(_env_file=None, qianchuan_enabled=True, debug=True,
            qianchuan_app_id="12345", qianchuan_app_secret="mock-secret",
            qianchuan_authorization_url="https://ad.oceanengine.com/openapi/audit/oauth.html",
            qianchuan_redirect_uri="http://127.0.0.1:5173/api/qianchuan/oauth/callback",
            qianchuan_scope="mock_scope", qianchuan_token_encryption_key=Fernet.generate_key().decode())
        self.calls = []
        self.custom = {}
        self.client = QianchuanClient(self.settings, httpx.MockTransport(self.respond))
        self.service = QianchuanService(self.settings, self.client, self.sessions)
        with self.sessions() as db:
            for tenant_id in ("team-a", "team-b"):
                db.add(Tenant(id=tenant_id, name=tenant_id, code=tenant_id))
            db.add(TenantRole(id=1, tenant_id="team-a", name="成片成员", code="custom",
                              permissions=["final_videos.access"]))
            for user_id, tenant_id, role in (("owner", "team-a", "owner"), ("member", "team-a", "member"),
                                             ("other", "team-b", "owner")):
                db.add(User(id=user_id, email=user_id + "@example.test", name=user_id, password_hash="unused"))
                db.add(TenantMember(tenant_id=tenant_id, user_id=user_id, role=role,
                                    role_id=1 if user_id == "member" else None))
            db.add(Product(id=1, tenant_id="team-a", name="测试商品"))
            db.add(Product(id=2, tenant_id="team-b", name="其他企业商品"))
            for asset_id, tenant_id, kind in ((1, "team-a", "final_output"), (2, "team-b", "final_output"),
                                              (3, "team-a", "material")):
                db.add(VideoAsset(id=asset_id, tenant_id=tenant_id, product_id=1 if tenant_id == "team-a" else 2,
                    asset_id=str(asset_id), filename="test.mp4", object_url="/api/storage/objects/test",
                    content_hash="a" * 64, asset_kind=kind))
            db.commit()
        self.headers = {"Authorization": "Bearer " + create_token("owner", "team-a")}
        self.member_headers = {"Authorization": "Bearer " + create_token("member", "team-a")}
        self.other_headers = {"Authorization": "Bearer " + create_token("other", "team-b")}
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        def db_override():
            with self.sessions() as db:
                yield db
        self.app.dependency_overrides[get_db] = db_override
        self.app.dependency_overrides[get_qianchuan_service] = lambda: self.service
        self.browser = TestClient(self.app, base_url="http://127.0.0.1:5173")

    def tearDown(self):
        self.browser.close()
        self.client.close()
        self.engine.dispose()
        self.directory.cleanup()

    @staticmethod
    def tokens(suffix=""):
        return {"access_token": "mock-access" + suffix, "refresh_token": "mock-refresh" + suffix,
                "expires_in": 7200, "refresh_token_expires_in": 86400}

    @staticmethod
    def envelope(data):
        return httpx.Response(200, json={"code": 0, "data": data, "request_id": "request-test-1"})

    def respond(self, request):
        self.calls.append(request)
        path = request.url.path
        if path in self.custom:
            return self.custom[path](request)
        if path in (A01, A03):
            return self.envelope(self.tokens())
        if path == A02:
            return self.envelope({"list": [{"account_id": 100, "account_name": "测试店铺",
                "account_type": SHOP_ROLE, "is_valid": True}]})
        if path == A04:
            return self.envelope({"adv_id_list": [{"adv_id": 9007199254740993, "adv_name": "测试千川"}],
                                   "page_info": {"page": int(request.url.params["page"]), "total_page": 1}})
        if path == A05:
            return self.envelope({"advertiser_ids": [200], "page_info": {"page": 1, "total_page": 1}})
        if path == A08:
            advertiser_ids = json.loads(request.url.params["advertiser_ids"])
            if not isinstance(advertiser_ids, list) or not advertiser_ids or any(
                    type(value) is not int for value in advertiser_ids):
                return httpx.Response(200, json={"code": 40000,
                    "message": "advertiser_ids: Field must be set to array"})
            return self.envelope({"list": [{"advertiser_id": value, "ecp_type": "SHOP"}
                                           for value in advertiser_ids]})
        raise AssertionError("Unexpected platform endpoint")

    def start(self, **payload):
        response = self.browser.post(PREFIX + "/oauth/sessions", json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), response

    def callback(self, session, browser=None, **extra):
        state = parse_qs(urlsplit(session["authorization_url"]).query)["state"][0]
        params = {"state": state, "auth_code": "mock-code", **extra}
        return (browser or self.browser).get(PREFIX + "/oauth/callback", params=params, follow_redirects=False)

    def completed(self):
        session, _ = self.start(video_asset_id=1)
        response = self.callback(session)
        self.assertEqual(response.status_code, 303, response.text)
        with self.sessions() as db:
            conn_id = db.get(OAuthSession, session["session_id"]).connection_id
        return session, conn_id

    def call_count(self, path):
        return sum(r.url.path == path for r in self.calls)

    def test_shop_authorization_cookie_encryption_result_and_accounts(self):
        session, response = self.start(video_asset_id=1)
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertNotIn("Secure", cookie)
        query = parse_qs(urlsplit(session["authorization_url"]).query)
        self.assertEqual(query["redirect_uri"], [self.settings.qianchuan_redirect_uri])
        self.assertEqual(query["scope"], ["mock_scope"])
        self.assertNotIn("mock-secret", response.text)
        callback = self.callback(session)
        self.assertEqual(callback.status_code, 303)
        self.assertTrue(callback.headers["location"].startswith("http://127.0.0.1:5173/qianchuan/oauth/result?"))
        self.assertEqual(callback.headers["cache-control"], "no-store")
        self.assertEqual(callback.headers["referrer-policy"], "no-referrer")
        with self.sessions() as db:
            row = db.get(OAuthSession, session["session_id"])
            conn = db.get(Connection, row.connection_id)
            self.assertEqual(row.state_hash, digest(query["state"][0]))
            self.assertNotIn("mock-access", conn.access_token_ciphertext)
            self.assertEqual(self.service.decrypt(conn.access_token_ciphertext), "mock-access")
            self.assertEqual(conn.status, "sync_pending")
            conn_id = conn.id
        request = next(r for r in self.calls if r.url.path == A01)
        self.assertEqual(json.loads(request.content), {"app_id": 12345, "secret": "mock-secret", "auth_code": "mock-code"})
        self.service.sync_connection(conn_id)
        accounts = self.browser.get(PREFIX + "/accounts", headers=self.headers).json()
        self.assertEqual(accounts["connections"][0]["accounts"][0]["advertiser_id"], "9007199254740993")
        self.assertNotIn("mock-access", json.dumps(accounts))
        request = next(r for r in self.calls if r.url.path == A02)
        self.assertEqual(request.url.params["access_token"], "mock-access")
        result = self.browser.get(PREFIX + "/oauth/sessions/" + session["session_id"], headers=self.headers).json()
        self.assertEqual(result["connection_status"], "active")
        self.assertEqual(result["return_path"], "/final-videos/1")

    def test_public_callback_needs_cookie_even_with_valid_state(self):
        session, _ = self.start()
        with TestClient(self.app, base_url="http://127.0.0.1:5173") as other_browser:
            self.assertEqual(self.callback(session, other_browser).status_code, 400)
        self.assertEqual(self.call_count(A01), 0)
        # Original browser uses no Authorization header on callback.
        self.assertEqual(self.callback(session).status_code, 303)

    def test_callback_replay_and_duplicate_parameters_rejected(self):
        session, _ = self.start()
        state = parse_qs(urlsplit(session["authorization_url"]).query)["state"][0]
        duplicate = self.browser.get(PREFIX + "/oauth/callback",
            params=[("state", state), ("state", state), ("auth_code", "mock-code")])
        self.assertEqual(duplicate.status_code, 400)
        cookie = dict(self.browser.cookies)
        self.assertEqual(self.callback(session).status_code, 303)
        self.browser.cookies.update(cookie)
        self.assertEqual(self.callback(session).status_code, 400)
        self.assertEqual(self.call_count(A01), 1)

    def test_missing_expired_and_invalid_state_do_not_exchange(self):
        self.assertEqual(self.browser.get(PREFIX + "/oauth/callback").status_code, 400)
        session, _ = self.start()
        self.assertEqual(self.callback(session, state="x" * 43).status_code, 400)
        with self.sessions() as db:
            db.get(OAuthSession, session["session_id"]).expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.callback(session).status_code, 400)
        self.assertEqual(self.call_count(A01), 0)

    def test_cancel_and_exchange_uncertainty_are_safe(self):
        session, _ = self.start()
        response = self.callback(session, auth_code="", error="access_denied")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.call_count(A01), 0)
        session, _ = self.start()
        def timeout(request):
            raise httpx.ReadTimeout("secret=mock-secret auth_code=mock-code", request=request)
        self.custom[A01] = timeout
        self.assertEqual(self.callback(session).status_code, 303)
        result = self.browser.get(PREFIX + "/oauth/sessions/" + session["session_id"], headers=self.headers)
        self.assertEqual(result.json()["error_code"], "exchange_uncertain")
        self.assertNotIn("mock-code", result.text)
        self.assertEqual(self.callback(session).status_code, 400)
        self.assertEqual(self.call_count(A01), 1)

    def test_membership_checked_again_on_callback(self):
        session, _ = self.start()
        with self.sessions() as db:
            db.query(TenantMember).filter_by(user_id="owner").update({"status": "disabled"})
            db.commit()
        self.assertEqual(self.callback(session).status_code, 400)
        self.assertEqual(self.call_count(A01), 0)

    def test_member_losing_final_video_permission_cannot_finish_authorization(self):
        response = self.browser.post(PREFIX + "/oauth/sessions", json={"video_asset_id": 1},
                                     headers=self.member_headers)
        self.assertEqual(response.status_code, 201)
        with self.sessions() as db:
            db.get(TenantRole, 1).permissions = []
            db.commit()
        self.assertEqual(self.callback(response.json()).status_code, 400)
        self.assertEqual(self.call_count(A01), 0)
        self.assertEqual(self.browser.get(PREFIX + "/accounts", headers=self.member_headers).status_code, 403)

    def test_authentication_roles_and_cross_tenant_resources(self):
        self.assertEqual(self.browser.get(PREFIX + "/accounts").status_code, 401)
        self.assertEqual(self.browser.post(PREFIX + "/oauth/sessions", json={}, headers=self.member_headers).status_code, 201)
        for asset_id in (2, 3):
            self.assertEqual(self.browser.post(PREFIX + "/oauth/sessions", json={"video_asset_id": asset_id},
                                               headers=self.headers).status_code, 404)
        session, conn_id = self.completed()
        self.assertEqual(self.browser.get(PREFIX + "/oauth/sessions/" + session["session_id"],
                                          headers=self.other_headers).status_code, 404)
        self.assertEqual(self.browser.get(PREFIX + "/oauth/sessions/" + session["session_id"],
                                          headers=self.member_headers).status_code, 404)
        for suffix, method in (("/sync", self.browser.post), ("", self.browser.delete)):
            self.assertEqual(method(PREFIX + "/connections/" + conn_id + suffix, headers=self.other_headers).status_code, 404)
            self.assertEqual(method(PREFIX + "/connections/" + conn_id + suffix, headers=self.member_headers).status_code, 404)
        self.assertEqual(self.browser.get(PREFIX + "/accounts", headers=self.other_headers).json()["connections"], [])
        self.assertEqual(self.browser.post(PREFIX + "/oauth/sessions", json={"tenant_id": "team-b"}, headers=self.headers).status_code, 422)

    def test_configuration_local_https_and_disabled(self):
        self.assertEqual(self.service.configuration()["status"], "ready")
        self.settings.debug = False
        self.assertEqual(self.service.configuration()["status"], "invalid_configuration")
        self.settings.qianchuan_redirect_uri = "https://example.test/api/qianchuan/oauth/callback"
        self.assertEqual(self.service.configuration()["status"], "ready")
        self.assertTrue(self.service.cookie_name("id").startswith("__Host-"))
        _, response = self.start()
        self.assertIn("Secure", response.headers["set-cookie"])
        for uri in ("http://example.test/api/qianchuan/oauth/callback", "https://example.test/api/qianchuan/oauth/callback?next=x",
                    "https://user:pass@example.test/api/qianchuan/oauth/callback", "http://127.0.0.1:8000/api/qianchuan/oauth/callback"):
            self.settings.qianchuan_redirect_uri = uri
            self.assertEqual(self.service.configuration()["status"], "invalid_configuration")
        self.settings.qianchuan_enabled = False
        self.assertEqual(self.browser.get(PREFIX + "/accounts", headers=self.headers).json()["configuration"]["status"], "disabled")
        self.assertEqual(self.browser.post(PREFIX + "/oauth/sessions", json={}, headers=self.headers).status_code, 503)

    def test_official_link_preserves_parameters_and_replaces_state(self):
        self.settings.qianchuan_authorization_url += "?app_id=12345&state=old&material_auth=1&rid=mock-callback-id"
        self.assertEqual(self.service.configuration()["status"], "ready")
        first, _ = self.start()
        second, _ = self.start()
        one = parse_qs(urlsplit(first["authorization_url"]).query)
        two = parse_qs(urlsplit(second["authorization_url"]).query)
        self.assertEqual(one["material_auth"], ["1"])
        self.assertEqual(one["rid"], ["mock-callback-id"])
        self.assertEqual(one["redirect_uri"], [self.settings.qianchuan_redirect_uri])
        self.assertNotEqual(one["state"], ["old"])
        self.assertNotEqual(one["state"], two["state"])
        self.assertEqual(len(one["state"]), 1)

    def test_repeated_new_authorization_reuses_personal_connection(self):
        first, _ = self.start(video_asset_id=1)
        second, _ = self.start(video_asset_id=1)
        with self.sessions() as db:
            self.assertEqual(db.query(Connection).filter_by(
                tenant_id="team-a", created_by="owner").count(), 1)
            first_row = db.get(OAuthSession, first["session_id"])
            second_row = db.get(OAuthSession, second["session_id"])
            self.assertEqual(first_row.connection_id, second_row.connection_id)
            self.assertEqual(first_row.status, "failed")
            self.assertEqual(first_row.error_code, "superseded")
            self.assertEqual(second_row.status, "pending")

    def test_same_advertiser_from_shop_and_workspace_is_returned_once(self):
        self.custom[A02] = lambda r: self.envelope({"list": [
            {"account_id": 100, "account_name": "PAMIS", "account_type": "CUSTOMER_ADMIN", "is_valid": True},
            {"account_id": 101, "account_name": "直营店铺", "account_type": SHOP_ROLE, "is_valid": True},
        ]})
        self.custom[A09] = lambda r: self.envelope({"accounts": [
            {"account_id": 9007199254740993, "account_name": "工作台千川", "account_type": "QIANCHUAN"}],
            "page_info": {"page": 1, "total_page": 1}})
        _, connection_id = self.completed()
        self.service.sync_connection(connection_id)
        accounts = self.browser.get(PREFIX + "/accounts", headers=self.headers).json()
        rows = accounts["connections"][0]["accounts"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["advertiser_id"], "9007199254740993")
        self.assertEqual(rows[0]["source_account_name"], "直营店铺")

    def test_legacy_connections_are_presented_as_one_deduplicated_list(self):
        _, first_id = self.completed()
        self.service.sync_connection(first_id)
        with self.sessions() as db:
            first = db.get(Connection, first_id)
            second = Connection(id="legacy-second", tenant_id="team-a", created_by="owner",
                                status="active", created_at=first.created_at + timedelta(seconds=1))
            db.add(second)
            db.add(Binding(id="legacy-binding", tenant_id="team-a", connection_id=second.id,
                           source_account_id="200", source_account_name="duplicate",
                           source_account_type=SHOP_ROLE, advertiser_id="9007199254740993",
                           advertiser_name="测试千川", advertiser_type="SHOP_LINKED"))
            db.commit()
        result = self.browser.get(PREFIX + "/accounts", headers=self.headers).json()
        self.assertEqual(len(result["connections"]), 1)
        self.assertEqual(result["connections"][0]["id"], "legacy-second")
        self.assertEqual(len(result["connections"][0]["accounts"]), 1)

    def test_authorization_template_rejects_conflicts_and_unknown_parameters(self):
        base = self.settings.qianchuan_authorization_url
        for query in ("app_id=999", "redirect_uri=https%3A%2F%2Fevil.test", "scope=other",
                      "state=a&state=b", "secret=never-send", "material_auth=2"):
            with self.subTest(query=query):
                self.settings.qianchuan_authorization_url = base + "?" + query
                self.assertIn("QIANCHUAN_AUTHORIZATION_URL", self.service.configuration()["missing"])
                self.assertEqual(self.browser.post(PREFIX + "/oauth/sessions", json={}, headers=self.headers).status_code, 503)
        self.assertEqual(self.calls, [])

    def test_authorization_template_keeps_explicit_scope_when_env_scope_empty(self):
        self.settings.qianchuan_scope = ""
        self.settings.qianchuan_authorization_url += "?scope=explicit-test-scope"
        result, _ = self.start()
        self.assertEqual(parse_qs(urlsplit(result["authorization_url"]).query)["scope"], ["explicit-test-scope"])

    def test_workspace_roles_resolve_only_qianchuan_accounts_with_full_pagination(self):
        for role in ("CUSTOMER_ADMIN", "CUSTOMER_OPERATOR"):
            with self.subTest(role=role):
                self.custom[A02] = lambda r: self.envelope({"list": [{"account_id": 100,
                    "account_type": role, "is_valid": True}]})
                def workspace_page(request):
                    self.assertEqual(request.url.params["account_id"], "100")
                    self.assertEqual(json.loads(request.url.params["filter"]), {"account_type": "QIANCHUAN"})
                    page = int(request.url.params["page"])
                    return self.envelope({"accounts": [{"account_id": 9007199254740992 + page,
                        "account_name": "workspace-account", "account_type": "QIANCHUAN"}],
                        "page_info": {"page": page, "total_page": 2}})
                self.custom[A09] = workspace_page
                _, connection_id = self.completed()
                self.service.sync_connection(connection_id)
                with self.sessions() as db:
                    self.assertEqual(db.get(Connection, connection_id).status, "active")
                    rows = db.query(Binding).filter_by(connection_id=connection_id, is_valid=True).all()
                    self.assertEqual({x.advertiser_id for x in rows}, {"9007199254740993", "9007199254740994"})
                    self.assertTrue(all(x.source_account_type == role and x.advertiser_type == "SHOP" for x in rows))

    def test_workspace_empty_result_preserves_authorization_with_actionable_warning(self):
        self.custom[A02] = lambda r: self.envelope({"list": [{"account_id": 100,
            "account_type": "CUSTOMER_ADMIN", "is_valid": True}]})
        self.custom[A09] = lambda r: self.envelope({"accounts": [], "page_info": {"page": 1, "total_page": 0}})
        _, connection_id = self.completed()
        self.service.sync_connection(connection_id)
        with self.sessions() as db:
            conn = db.get(Connection, connection_id)
            self.assertEqual(conn.status, "active")
            self.assertTrue(conn.access_token_ciphertext)
            self.assertEqual(conn.account_snapshot[0]["account_type"], "CUSTOMER_ADMIN")
            self.assertTrue(any("工作台" in warning for warning in conn.warnings))
            self.assertEqual(db.query(Binding).filter_by(connection_id=connection_id).count(), 0)
        self.assertFalse(any(r.url.path == A08 for r in self.calls))

    def test_workspace_rejects_wrong_business_type_or_missing_type(self):
        self.custom[A02] = lambda r: self.envelope({"list": [{"account_id": 100,
            "account_type": "CUSTOMER_ADMIN", "is_valid": True}]})
        for kind in ("AD", None):
            with self.subTest(kind=kind):
                self.custom[A09] = lambda r: self.envelope({"accounts": [{"account_id": 200, "account_type": kind}],
                    "page_info": {"page": 1, "total_page": 1}})
                _, connection_id = self.completed()
                self.service.sync_connection(connection_id)
                with self.sessions() as db:
                    self.assertEqual(db.get(Connection, connection_id).status, "sync_failed")
                    self.assertEqual(db.query(Binding).filter_by(connection_id=connection_id).count(), 0)

    def test_origin_mismatch_cannot_start(self):
        response = self.browser.post(PREFIX + "/oauth/sessions", json={}, headers={**self.headers, "Origin": "http://localhost:5173"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_multiple_shop_pages_and_unknown_invalid_roles(self):
        self.custom[A02] = lambda r: self.envelope({"list": [
            {"account_id": 100, "account_type": SHOP_ROLE, "is_valid": True},
            {"account_id": 500, "account_type": "AGENT", "is_valid": True},
            {"account_id": 501, "account_type": SHOP_ROLE, "is_valid": False},
            {"account_id": 502, "account_type": SHOP_ROLE, "is_valid": "true"}]})
        self.custom[A04] = lambda r: self.envelope({
            "list": [200 + int(r.url.params["page"])],
            "page_info": {"page": int(r.url.params["page"]), "total_page": 2}})
        _, conn_id = self.completed()
        self.service.sync_connection(conn_id)
        accounts = self.browser.get(PREFIX + "/accounts", headers=self.headers).json()["connections"][0]
        self.assertEqual(len(accounts["accounts"]), 2)
        self.assertEqual(len(accounts["warnings"]), 2)
        self.assertEqual(self.call_count(A04), 2)
        self.assertEqual(self.call_count(A05), 0)

    def agent_source(self, companies):
        return {"account_id": 100, "account_type": AGENT_ROLE, "is_valid": True, "company_list": companies}

    def test_agent_company_scope_repeated_query_and_type(self):
        self.custom[A02] = lambda r: self.envelope({"list": [self.agent_source([
            {"customer_company_id": 123}, {"customer_company_id": 456}])]})
        _, conn_id = self.completed()
        self.service.sync_connection(conn_id)
        agent_call = next(r for r in self.calls if r.url.path == A05)
        self.assertEqual(agent_call.url.params.get_list("company_ids"), ["123", "456"])
        self.assertEqual(agent_call.url.params["advertiser_id"], "100")
        self.assertEqual(self.call_count(A08), 1)
        with self.sessions() as db:
            self.assertEqual(db.get(Connection, conn_id).status, "active")
            self.assertEqual(db.query(Binding).one().source_company_ids, ["123", "456"])

    def test_empty_company_list_versus_missing_scope(self):
        self.custom[A02] = lambda r: self.envelope({"list": [self.agent_source([])]})
        _, conn_id = self.completed()
        self.service.sync_connection(conn_id)
        self.assertNotIn("company_ids", next(r for r in self.calls if r.url.path == A05).url.params)
        self.custom[A02] = lambda r: self.envelope({"list": [{"account_id": 100, "account_type": AGENT_ROLE, "is_valid": True}]})
        self.browser.post(PREFIX + "/connections/" + conn_id + "/sync", headers=self.headers)
        before = self.call_count(A05)
        self.service.sync_connection(conn_id)
        self.assertEqual(self.call_count(A05), before)
        with self.sessions() as db:
            self.assertEqual(db.get(Connection, conn_id).status, "sync_failed")
        self.assertEqual(self.browser.get(PREFIX + "/accounts", headers=self.headers).json()["connections"][0]["accounts"], [])

    def test_direct_advertiser_must_have_supported_ecp_type(self):
        self.custom[A02] = lambda r: self.envelope({"list": [{"account_id": 200, "account_name": "直接账户",
                                                            "account_type": "ADVERTISER", "is_valid": True}]})
        _, conn_id = self.completed()
        self.custom[A08] = lambda r: self.envelope({"list": [{"advertiser_id": 200, "ecp_type": "AGENT"}]})
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            self.assertEqual(db.get(Connection, conn_id).status, "sync_failed")
            self.assertEqual(db.query(Binding).count(), 0)

    def test_partial_pagination_failure_preserves_snapshot_and_binding_identity(self):
        _, conn_id = self.completed()
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            binding_id = db.query(Binding).one().id
        self.browser.post(PREFIX + "/connections/" + conn_id + "/sync", headers=self.headers)
        def broken_page(request):
            if request.url.params["page"] == "2":
                return httpx.Response(500, text="access_token=mock-access")
            return self.envelope({"list": [999], "page_info": {"page": 1, "total_page": 2}})
        self.custom[A04] = broken_page
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            self.assertEqual(db.query(Binding).one().id, binding_id)
            self.assertEqual(db.get(Connection, conn_id).status, "sync_failed")
        del self.custom[A04]
        self.browser.post(PREFIX + "/connections/" + conn_id + "/sync", headers=self.headers)
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            self.assertEqual(db.query(Binding).one().id, binding_id)

    def test_missing_pagination_and_repeated_pages_fail_closed(self):
        _, conn_id = self.completed()
        self.custom[A04] = lambda r: self.envelope({"list": [200]})
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            self.assertEqual(db.get(Connection, conn_id).error_code, "invalid_pagination")
        self.browser.post(PREFIX + "/connections/" + conn_id + "/sync", headers=self.headers)
        self.custom[A04] = lambda r: self.envelope({"list": [200],
            "page_info": {"page": int(r.url.params["page"]), "total_page": 3}})
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            self.assertEqual(db.query(Binding).count(), 0)

    def test_reauthorization_invalidates_old_session_and_scope(self):
        session, conn_id = self.completed()
        self.service.sync_connection(conn_id)
        first, _ = self.start(connection_id=conn_id)
        second, _ = self.start(connection_id=conn_id)
        self.assertEqual(self.callback(first).status_code, 400)
        self.assertEqual(self.callback(second).status_code, 303)
        self.assertEqual(self.browser.get(PREFIX + "/accounts", headers=self.headers).json()["connections"][0]["accounts"], [])
        self.custom[A02] = lambda r: self.envelope({"list": []})
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            self.assertEqual(db.query(Binding).filter_by(is_valid=True).count(), 0)
            self.assertEqual(db.query(Binding).count(), 1)

    def test_token_refresh_and_worker_claim_only_once(self):
        _, conn_id = self.completed()
        with self.sessions() as db:
            db.get(Connection, conn_id).access_token_expires_at = datetime.utcnow()
            db.commit()
        self.custom[A03] = lambda r: self.envelope(self.tokens("-fresh"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.service.sync_connection, [conn_id, conn_id]))
        self.assertEqual(self.call_count(A03), 1)
        self.assertEqual(self.call_count(A02), 1)
        with self.sessions() as db:
            conn = db.get(Connection, conn_id)
            self.assertEqual(self.service.decrypt(conn.refresh_token_ciphertext), "mock-refresh-fresh")
            self.assertEqual(conn.status, "active")

    def test_callback_race_consumes_code_only_once(self):
        session, _ = self.start()
        cookie = dict(self.browser.cookies)
        barrier = threading.Barrier(2)
        def callback_thread(_):
            with TestClient(self.app, base_url="http://127.0.0.1:5173") as browser:
                browser.cookies.update(cookie)
                barrier.wait(timeout=5)
                return self.callback(session, browser).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(callback_thread, range(2)))
        self.assertEqual(sorted(statuses), [303, 400])
        self.assertEqual(self.call_count(A01), 1)

    def test_refresh_cannot_overwrite_new_authorization(self):
        _, conn_id = self.completed()
        with self.sessions() as db:
            db.get(Connection, conn_id).access_token_expires_at = datetime.utcnow()
            db.commit()
        def concurrent_reauthorization(request):
            newer, _ = self.start(connection_id=conn_id)
            self.custom[A01] = lambda r: self.envelope(self.tokens("-new-auth"))
            self.assertEqual(self.callback(newer).status_code, 303)
            return self.envelope(self.tokens("-stale-refresh"))
        self.custom[A03] = concurrent_reauthorization
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            conn = db.get(Connection, conn_id)
            self.assertEqual(self.service.decrypt(conn.access_token_ciphertext), "mock-access-new-auth")
            self.assertEqual(conn.status, "sync_pending")

    def test_old_exchange_cannot_overwrite_new_session(self):
        _, conn_id = self.completed()
        older, _ = self.start(connection_id=conn_id)
        def concurrent_start(request):
            self.start(connection_id=conn_id)
            return self.envelope(self.tokens("-stale"))
        self.custom[A01] = concurrent_start
        self.assertEqual(self.callback(older).status_code, 303)
        with self.sessions() as db:
            self.assertEqual(db.get(OAuthSession, older["session_id"]).error_code, "superseded")
            self.assertEqual(self.service.decrypt(db.get(Connection, conn_id).access_token_ciphertext), "mock-access")

    def test_disable_during_sync_blocks_followup_calls_and_keeps_history(self):
        _, conn_id = self.completed()
        def disable_before_shop(request):
            self.assertEqual(self.browser.delete(PREFIX + "/connections/" + conn_id, headers=self.headers).status_code, 204)
            return self.envelope({"list": [{"account_id": 100, "account_type": SHOP_ROLE, "is_valid": True}]})
        self.custom[A02] = disable_before_shop
        self.service.sync_connection(conn_id)
        self.assertEqual(self.call_count(A04), 0)
        with self.sessions() as db:
            conn = db.get(Connection, conn_id)
            self.assertEqual(conn.status, "disabled")
            self.assertEqual(conn.access_token_ciphertext, "")

    def test_restart_does_not_replay_exchange(self):
        session, _ = self.start()
        with self.sessions() as db:
            row = db.get(OAuthSession, session["session_id"])
            row.status = "processing"
            conn_id = row.connection_id
            db.commit()
        self.service.recover()
        self.assertEqual(self.calls, [])
        with self.sessions() as db:
            self.assertEqual(db.get(OAuthSession, session["session_id"]).error_code, "interrupted")
            self.assertEqual(db.get(Connection, conn_id).status, "reauthorization_required")

    def test_invalid_tokens_and_provider_messages_never_leak(self):
        for data in ({"access_token": "mock-access"}, {**self.tokens(), "expires_in": "7200"}):
            self.custom[A01] = lambda r, data=data: self.envelope(data)
            session, _ = self.start()
            self.assertEqual(self.callback(session).status_code, 303)
            result = self.browser.get(PREFIX + "/oauth/sessions/" + session["session_id"], headers=self.headers)
            self.assertNotIn("mock-access", result.text)
            self.assertEqual(result.json()["status"], "failed")
        self.custom[A01] = lambda r: httpx.Response(200, json={"code": 40100, "message": "mock-secret mock-access",
                                                              "request_id": "mock-secret"})
        session, _ = self.start()
        self.callback(session)
        result = self.browser.get(PREFIX + "/oauth/sessions/" + session["session_id"], headers=self.headers)
        self.assertNotIn("mock-secret", result.text)

    def test_sensitive_log_filter(self):
        filter_ = SensitiveLogFilter()
        for text in ("GET /api/qianchuan/oauth/callback?auth_code=secret&state=xyz",
                     "HTTP Request: GET https://api.oceanengine.com/?access_token=secret",
                     "headers: Access-Token: secret", "{'secret': 'secret'}"):
            record = logging.LogRecord("httpx", logging.INFO, "", 0, "%s", (text,), None)
            self.assertFalse(filter_.filter(record))
        self.assertTrue(filter_.filter(logging.LogRecord("httpx", logging.INFO, "", 0, "GET /api/health", (), None)))

    def test_disabled_connection_can_be_authorized_again(self):
        _, conn_id = self.completed()
        self.browser.delete(PREFIX + "/connections/" + conn_id, headers=self.headers)
        session, _ = self.start(connection_id=conn_id)
        self.assertEqual(self.callback(session).status_code, 303)
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            self.assertEqual(db.get(Connection, conn_id).status, "active")

    def test_remove_connection_hides_accounts_and_cannot_be_reactivated(self):
        _, conn_id = self.completed()
        self.service.sync_connection(conn_id)
        url = PREFIX + "/connections/" + conn_id + "/remove"
        self.assertEqual(self.browser.post(url, headers=self.member_headers).status_code, 404)
        self.assertEqual(self.browser.post(url, headers=self.other_headers).status_code, 404)
        pending, _ = self.start(connection_id=conn_id)
        calls_before = self.call_count(A01)
        self.assertEqual(self.browser.post(url, headers=self.headers).status_code, 204)
        self.assertEqual(self.browser.post(url, headers=self.headers).status_code, 204)
        self.assertEqual(self.browser.get(PREFIX + "/accounts", headers=self.headers).json()["connections"], [])
        self.assertEqual(self.callback(pending).status_code, 400)
        self.assertEqual(self.call_count(A01), calls_before)
        self.assertEqual(self.browser.post(PREFIX + "/oauth/sessions", json={"connection_id": conn_id},
                                          headers=self.headers).status_code, 404)
        self.browser.delete(PREFIX + "/connections/" + conn_id, headers=self.headers)
        with self.sessions() as db:
            conn = db.get(Connection, conn_id)
            self.assertEqual(conn.status, "deleted")
            self.assertEqual(conn.access_token_ciphertext, "")
            self.assertEqual(conn.refresh_token_ciphertext, "")
            self.assertFalse(db.query(Binding).one().is_valid)

    def test_remove_connection_during_exchange_keeps_credentials_cleared(self):
        session, _ = self.start()
        with self.sessions() as db:
            conn_id = db.get(OAuthSession, session["session_id"]).connection_id
        def remove_during_exchange(request):
            self.assertEqual(self.browser.post(PREFIX + "/connections/" + conn_id + "/remove",
                                              headers=self.headers).status_code, 204)
            return self.envelope(self.tokens())
        self.custom[A01] = remove_during_exchange
        self.callback(session)
        with self.sessions() as db:
            conn = db.get(Connection, conn_id)
            self.assertEqual(conn.status, "deleted")
            self.assertEqual(conn.access_token_ciphertext, "")

    def test_direct_advertiser_positive_and_refresh_failure(self):
        self.custom[A02] = lambda r: self.envelope({"list": [{"account_id": 200, "account_name": "直接账户",
            "account_type": "ADVERTISER", "is_valid": True}]})
        _, conn_id = self.completed()
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            self.assertEqual(db.query(Binding).one().advertiser_id, "200")
            db.get(Connection, conn_id).access_token_expires_at = datetime.utcnow()
            db.commit()
        self.custom[A03] = lambda r: httpx.Response(200, json={"code": 40100, "message": "mock-refresh"})
        self.browser.post(PREFIX + "/connections/" + conn_id + "/sync", headers=self.headers)
        count_before = self.call_count(A02)
        self.service.sync_connection(conn_id)
        self.assertEqual(self.call_count(A02), count_before)
        with self.sessions() as db:
            self.assertEqual(db.get(Connection, conn_id).status, "reauthorization_required")

    def test_tampered_ciphertext_never_reaches_provider(self):
        _, conn_id = self.completed()
        with self.sessions() as db:
            db.get(Connection, conn_id).access_token_ciphertext = "v1:tampered"
            db.commit()
        self.service.sync_connection(conn_id)
        self.assertEqual(self.call_count(A02), 0)
        with self.sessions() as db:
            self.assertEqual(db.get(Connection, conn_id).status, "reauthorization_required")

    def test_empty_optional_shop_details_use_id_list(self):
        self.custom[A04] = lambda r: self.envelope({"adv_id_list": [], "list": [200],
                                                   "page_info": {"page": 1, "total_page": 1}})
        _, conn_id = self.completed()
        self.service.sync_connection(conn_id)
        with self.sessions() as db:
            self.assertEqual(db.query(Binding).one().advertiser_id, "200")


if __name__ == "__main__":
    unittest.main()
