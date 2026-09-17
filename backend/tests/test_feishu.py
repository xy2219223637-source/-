import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.database import migrate_feishu_notifications
from app.models import Tenant
from app.services.feishu import FeishuClient, FeishuError, _cache, configured, get_feishu_client, public_script_url


def settings():
    return SimpleNamespace(feishu_enabled=True, feishu_app_id="cli_test", feishu_app_secret="secret-never-log",
                           feishu_system_tenant_id="test-tenant", app_public_base_url="https://scripts.example.com")


class FeishuClientTests(unittest.TestCase):
    def setUp(self):
        _cache.clear()
        self.requests = []

    def client(self, handler):
        def respond(request):
            self.requests.append(request)
            if request.url.path.endswith("/tenant_access_token/internal"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "t_test", "expire": 7200})
            return handler(request)
        return FeishuClient(settings(), "test-tenant", httpx.MockTransport(respond))

    def test_scope_pagination_parent_children_and_single_users_are_merged(self):
        def respond(request):
            path = request.url.path
            if path.endswith("/scopes"):
                if not request.url.params.get("page_token"):
                    return httpx.Response(200, json={"code": 0, "data": {"user_ids": ["ou_single"], "department_ids": [], "has_more": True, "page_token": "second"}})
                return httpx.Response(200, json={"code": 0, "data": {"department_ids": ["od-parent"], "has_more": False}})
            if path.endswith("/children"):
                return httpx.Response(200, json={"code": 0, "data": {"items": [{"open_department_id": "od-child", "name": "内容组"}], "has_more": False}})
            if path.endswith("/find_by_department"):
                department = request.url.params["department_id"]
                users = [{"open_id": "ou_shared", "name": "共同员工"}]
                if department == "od-parent":
                    users += [{"open_id": "ou_parent", "name": "父部门员工"}, {"open_id": "ou_resigned", "name": "离职员工", "status": {"is_resigned": True}}]
                return httpx.Response(200, json={"code": 0, "data": {"items": users, "has_more": False}})
            if path.endswith("/ou_single"):
                return httpx.Response(200, json={"code": 0, "data": {"user": {"open_id": "ou_single", "name": "单独授权"}}})
            self.fail(path)
        client = self.client(respond)
        result = client.directory("user")
        self.assertEqual({x["id"] for x in result["items"]}, {"ou_parent", "ou_shared", "ou_single"})
        queries = [r.url.params["department_id"] for r in self.requests if r.url.path.endswith("/find_by_department")]
        self.assertEqual(set(queries), {"od-parent", "od-child"})
        count = len(self.requests)
        self.assertEqual(client.directory("user"), result)
        self.assertEqual(len(self.requests), count)
        client.directory("user", refresh=True)
        self.assertGreater(len(self.requests), count)

    def test_chat_directory_excludes_external_and_unclassified_chats(self):
        client = self.client(lambda request: httpx.Response(200, json={"code": 0, "data": {"items": [
            {"chat_id": "oc_internal", "name": "内部", "external": False},
            {"chat_id": "oc_external", "name": "外部", "external": True}, {"chat_id": "oc_unknown", "name": "未知"},
        ]}}))
        self.assertEqual([x["id"] for x in client.directory("chat")["items"]], ["oc_internal"])

    def test_user_groups_and_broken_pagination_fail_without_caching_incomplete_list(self):
        client = self.client(lambda request: httpx.Response(200, json={"code": 0, "data": {"group_ids": ["g1"]}}))
        with self.assertRaisesRegex(FeishuError, "用户组"):
            client.directory("user")
        client = self.client(lambda request: httpx.Response(200, json={"code": 0, "data": {"has_more": True, "page_token": "same"}}))
        with self.assertRaisesRegex(FeishuError, "分页"):
            client.directory("user")

    def test_text_is_serialized_uuid_reused_and_token_cached(self):
        client = self.client(lambda request: httpx.Response(200, json={"code": 0, "data": {"message_id": "om_message"}}))
        for kind, target in [("user", "ou_user"), ("chat", "oc_chat")]:
            self.assertEqual(client.send(kind, target, '台词："中文"\n下一行', "request-uuid"), "om_message")
        posts = [r for r in self.requests if r.url.path.endswith("/messages")]
        self.assertEqual([r.url.params["receive_id_type"] for r in posts], ["open_id", "chat_id"])
        body = json.loads(posts[0].content)
        self.assertIsInstance(body["content"], str)
        self.assertEqual(json.loads(body["content"])["text"], '台词："中文"\n下一行')
        self.assertEqual(body["uuid"], "request-uuid")
        self.assertEqual(sum(r.url.path.endswith("/tenant_access_token/internal") for r in self.requests), 1)

    def test_http_200_business_failure_does_not_succeed_or_leak_response(self):
        client = self.client(lambda request: httpx.Response(200, json={"code": 230002, "msg": "secret-never-log"}))
        with self.assertRaises(FeishuError) as caught:
            client.send("user", "ou_user", "x", "uuid")
        self.assertFalse(caught.exception.uncertain)
        self.assertIn("230002", str(caught.exception))
        self.assertNotIn("secret-never-log", str(caught.exception))

    def test_transport_and_missing_message_id_are_not_retried_as_known_failures(self):
        for error, uncertain in [(httpx.ReadTimeout("timeout"), True), (httpx.ConnectError("connect"), False)]:
            def respond(request):
                raise error
            client = self.client(respond)
            with self.assertRaises(FeishuError) as caught:
                client.send("user", "ou_user", "x", "uuid")
            self.assertEqual(caught.exception.uncertain, uncertain)
        client = self.client(lambda request: httpx.Response(200, json={"code": 0, "data": {}}))
        with self.assertRaises(FeishuError) as caught:
            client.send("user", "ou_user", "x", "uuid")
        self.assertTrue(caught.exception.uncertain)

    def test_expired_token_refreshes_once_without_changing_message(self):
        sent = []
        def respond(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 99991663} if len(sent) == 1 else {"code": 0, "data": {"message_id": "om_message"}})
        client = self.client(respond)
        client.send("user", "ou_user", "text", "stable-uuid")
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(sum(r.url.path.endswith("/tenant_access_token/internal") for r in self.requests), 2)

    def test_http_timeout_and_server_errors_leave_delivery_unknown(self):
        for status, uncertain in [(408, True), (502, True), (429, False), (403, False)]:
            with self.subTest(status=status):
                client = self.client(lambda request: httpx.Response(status))
                with self.assertRaises(FeishuError) as caught:
                    client.send("user", "ou_user", "text", "stable-uuid")
                self.assertEqual(caught.exception.uncertain, uncertain)

    def test_configuration_is_bound_to_system_tenant(self):
        with patch("app.services.feishu.get_settings", return_value=settings()):
            self.assertEqual(get_feishu_client("test-tenant").tenant_id, "test-tenant")
            with self.assertRaises(FeishuError) as caught:
                get_feishu_client("another-tenant")
            self.assertEqual(caught.exception.status, 403)
        value = settings()
        value.feishu_enabled = False
        with patch("app.services.feishu.get_settings", return_value=value):
            with self.assertRaises(FeishuError) as caught:
                get_feishu_client("test-tenant")
            self.assertEqual(caught.exception.status, 503)

    def test_credentials_enable_notifications_unless_explicitly_disabled(self):
        value = settings()
        value.feishu_enabled = None
        value.feishu_system_tenant_id = ""
        self.assertTrue(configured(value))
        value.feishu_enabled = False
        self.assertFalse(configured(value))
        value.feishu_enabled = True
        value.feishu_app_secret = "  "
        self.assertFalse(configured(value))

    def test_automatic_binding_requires_exactly_one_active_enterprise(self):
        value = settings()
        value.feishu_enabled = None
        value.feishu_system_tenant_id = ""
        engine = create_engine("sqlite://")
        Tenant.__table__.create(engine)
        try:
            with Session(engine) as db, patch("app.services.feishu.get_settings", return_value=value):
                with self.assertRaises(FeishuError):
                    get_feishu_client("tenant")
                with self.assertRaisesRegex(FeishuError, "创建系统企业"):
                    get_feishu_client("tenant", db=db)
                db.add(Tenant(id="tenant", name="Company", code="company"))
                db.add(Tenant(id="inactive", name="Archived", code="archived", status="inactive"))
                db.commit()
                self.assertEqual(get_feishu_client("tenant", db=db).tenant_id, "tenant")
                with self.assertRaises(FeishuError) as caught:
                    get_feishu_client("other", db=db)
                self.assertEqual(caught.exception.status, 403)
                db.add(Tenant(id="other", name="Other", code="other"))
                db.commit()
                with self.assertRaisesRegex(FeishuError, "多家企业"):
                    get_feishu_client("tenant", db=db)
                # An explicit administrator-configured binding remains valid with multiple enterprises.
                value.feishu_system_tenant_id = "tenant"
                self.assertEqual(get_feishu_client("tenant", db=db).tenant_id, "tenant")
                with self.assertRaises(FeishuError) as caught:
                    get_feishu_client("other", db=db)
                self.assertEqual(caught.exception.status, 403)
        finally:
            engine.dispose()

    def test_public_link_requires_https_and_does_not_accept_credentials_or_localhost(self):
        value = settings()
        self.assertEqual(public_script_url(value, 23), "https://scripts.example.com/director-inspiration/23/workspace")
        for base in ["", "http://example.com", "https://localhost", "https://a:b@example.com", "https://example.com?bad=1"]:
            value.app_public_base_url = base
            with self.assertRaises(FeishuError):
                public_script_url(value, 23)

    def test_legacy_migration_is_repeatable_and_never_marks_fake_rows_sent(self):
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE shooting_tasks (id INTEGER PRIMARY KEY, tenant_id TEXT, project_id INTEGER, status TEXT)"))
            connection.execute(text("INSERT INTO shooting_tasks VALUES (1,'t',1,'pending'), (2,'t',1,'pending')"))
        migrate_feishu_notifications(engine)
        migrate_feishu_notifications(engine)
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT notification_status, notification_uuid FROM shooting_tasks")).all()
        self.assertEqual(rows, [("not_sent", None), ("not_sent", None)])
        self.assertIn("notification_payload", {c["name"] for c in inspect(engine).get_columns("shooting_tasks")})
        engine.dispose()
