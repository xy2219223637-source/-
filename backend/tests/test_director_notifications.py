import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import director
from app.auth import AuthContext, current_auth
from app.database import Base, get_db
from app.models import DirectorProject, DirectorSection, ShootingTask
from app.services.feishu import FeishuClient, FeishuError, _cache
from app.services.feishu_logging import RequestTraceMiddleware


class DirectorNotificationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.auth = AuthContext("user", "tenant", "owner", "director@example.com", "Company")
        app = FastAPI()
        app.add_middleware(RequestTraceMiddleware)
        app.include_router(director.router, prefix="/api")

        def session():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = session
        app.dependency_overrides[current_auth] = lambda: self.auth
        self.client = TestClient(app)
        with self.sessions() as db:
            project = DirectorProject(name="真实推送测试", tenant_id="tenant", mode="original", status="ready")
            db.add(project)
            db.flush()
            self.project_id = project.id
            db.add(DirectorSection(tenant_id="tenant", project_id=project.id, title="开头", script_lines=[{"text": "台词"}]))
            db.commit()
        self.feishu = Mock()
        self.feishu.settings = SimpleNamespace(feishu_app_id="cli_test", app_public_base_url="https://scripts.example.com")
        self.feishu.recipient.side_effect = lambda kind, id: {"id": id, "name": "接收群" if kind == "chat" else "接收员工", "department_id": "od_ops", "department_name": "运营部"}
        self.feishu.send.return_value = "om_delivered"
        self.patch = patch("app.services.director_notifications.get_feishu_client", return_value=self.feishu)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.client.close()
        self.engine.dispose()

    def payload(self, kind="user", target="ou_employee"):
        return {"recipient_type": kind, "recipient_id": target, "request_id": str(uuid.uuid4()), "note": "请按脚本拍摄"}

    def create(self, payload=None):
        return self.client.post(f"/api/director-projects/{self.project_id}/shooting-tasks", json=payload or self.payload())

    def tasks(self):
        return self.client.get("/api/director-projects/shooting-tasks").json()

    def test_personal_message_persists_snapshot_and_same_request_never_resends(self):
        payload = self.payload()
        first = self.create(payload)
        self.assertEqual(first.status_code, 200, first.text)
        row = first.json()
        self.assertEqual(row["notification_status"], "sent")
        self.assertEqual(row["feishu_message_id"], "om_delivered")
        self.assertEqual(row["assignee_id"], "ou_employee")
        self.assertEqual(row["status"], "pending")
        second = self.create(payload)
        self.assertEqual(second.json()["id"], row["id"])
        self.assertEqual(self.feishu.send.call_count, 1)
        self.assertEqual(len(self.tasks()), 1)
        message = self.feishu.send.call_args.args[2]
        self.assertIn(f"/director-inspiration/{self.project_id}/workspace", message)
        self.assertIn("director@example.com", message)
        self.assertEqual(self.create({**payload, "note": "changed"}).status_code, 409)
        self.assertEqual(self.create().status_code, 409)

    def test_group_is_a_recipient_and_not_a_personal_assignee(self):
        response = self.create(self.payload("chat", "oc_group"))
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        self.assertEqual(row["recipient_type"], "chat")
        self.assertEqual(row["assignee_id"], "")
        self.assertEqual(row["department_id"], "")
        self.assertEqual(self.feishu.send.call_args.args[:2], ("chat", "oc_group"))

    def test_http_success_and_notification_failure_are_correlated_in_logs(self):
        _cache.clear()
        def respond(request):
            if "/auth/" in request.url.path:
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "trace-test-token", "expire": 3600})
            return httpx.Response(200, headers={"x-tt-logid": "trace-test-upstream"}, json={"code": 230002, "msg": "permission denied"})
        settings = SimpleNamespace(feishu_app_id="cli_trace_test", feishu_app_secret="trace-test-secret")
        client = FeishuClient(settings, "tenant", transport=httpx.MockTransport(respond))
        self.feishu.send.side_effect = client.send
        payload = self.payload()
        with self.assertLogs("app.feishu", level="INFO") as captured:
            response = self.client.post(f"/api/director-projects/{self.project_id}/shooting-tasks", json=payload,
                                        headers={"x-request-id": "notification-trace"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["notification_status"], "failed")
        events = [record.feishu_event for record in captured.records]
        self.assertTrue(all(event["request_id"] == "notification-trace" for event in events))
        upstream = next(event for event in events if event.get("business_code") == 230002)
        self.assertEqual(upstream["notification_uuid"], payload["request_id"])
        self.assertEqual(upstream["task_id"], response.json()["id"])
        self.assertEqual(upstream["upstream_request_id"], "trace-test-upstream")
        self.assertEqual(len(events), 4)  # Only token and message HTTP request/response pairs.
        self.assertEqual({event["event"] for event in events}, {"feishu.http.started", "feishu.http.completed"})

    def test_local_task_creation_read_replay_and_validation_do_not_write_feishu_logs(self):
        # Mocked client does no upstream HTTP: internal business work must be silent.
        with self.assertNoLogs("app.feishu", level="INFO"):
            payload = self.payload()
            first = self.create(payload)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(self.create(payload).json()["id"], first.json()["id"])
            self.assertEqual(len(self.tasks()), 1)
            self.feishu.settings.app_public_base_url = "http://127.0.0.1"
            self.assertEqual(self.create().status_code, 503)
        self.assertEqual(self.feishu.send.call_count, 1)

    def test_directory_preserves_recipients_and_reports_script_link_readiness(self):
        directory = {"type": "user", "items": [{"id": "ou_employee", "name": "Employee"}]}
        self.feishu.directory.return_value = directory
        with patch("app.api.director.get_feishu_client", return_value=self.feishu):
            for base_url, expected in [("http://127.0.0.1", False), ("https://scripts.example.com", True)]:
                with self.subTest(base_url=base_url):
                    self.feishu.settings.app_public_base_url = base_url
                    response = self.client.get("/api/director-projects/shooting-directory?type=user")
                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()
                    self.assertEqual(result["items"], directory["items"])
                    self.assertEqual(result["can_send"], expected)
                    self.assertEqual(bool(result["send_blocked_reason"]), not expected)
        self.assertNotIn("can_send", directory)  # Cached directory data must stay unchanged.
        self.feishu.send.assert_not_called()

    def test_invalid_script_link_is_rejected_before_any_task_or_send(self):
        self.feishu.settings.app_public_base_url = "http://127.0.0.1"
        response = self.create()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.headers.get("x-notification-outcome"), "not_started")
        self.assertEqual(self.tasks(), [])
        self.feishu.send.assert_not_called()

    def test_pre_send_directory_failure_is_not_an_uncertain_notification(self):
        self.feishu.recipient.side_effect = FeishuError("Directory unavailable", status=503)
        response = self.create()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.headers.get("x-notification-outcome"), "not_started")
        self.assertEqual(self.tasks(), [])
        self.feishu.send.assert_not_called()

    def test_known_failure_retries_original_record_uuid_and_message(self):
        self.feishu.send.side_effect = [FeishuError("没有发送权限"), "om_retry"]
        row = self.create().json()
        self.assertEqual(row["notification_status"], "failed")
        self.assertTrue(row["can_retry_notification"])
        original = self.feishu.send.call_args.args
        with self.sessions() as db:
            db.get(DirectorProject, self.project_id).name = "后来改名"
            db.commit()
        retried = self.client.post(f"/api/director-projects/shooting-tasks/{row['id']}/retry-notification")
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["notification_status"], "sent")
        self.assertEqual(self.feishu.send.call_args.args, original)
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.client.post(f"/api/director-projects/shooting-tasks/{row['id']}/retry-notification").status_code, 409)

    def test_read_timeout_is_unknown_and_cannot_be_blindly_retried(self):
        self.feishu.send.side_effect = FeishuError("超时", uncertain=True)
        payload = self.payload()
        row = self.create(payload).json()
        self.assertEqual(row["notification_status"], "unknown")
        self.assertFalse(row["can_retry_notification"])
        self.assertEqual(self.client.post(f"/api/director-projects/shooting-tasks/{row['id']}/retry-notification").status_code, 409)
        self.create(payload)
        self.assertEqual(self.feishu.send.call_count, 1)

    def test_other_tenant_cannot_see_create_retry_or_delete_this_project(self):
        row = self.create().json()
        self.auth = AuthContext("other", "other-tenant", "owner", "other@example.com", "Other")
        self.assertEqual(self.tasks(), [])
        self.assertEqual(self.create().status_code, 404)
        self.assertEqual(self.client.post(f"/api/director-projects/shooting-tasks/{row['id']}/retry-notification").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/director-projects/{self.project_id}").status_code, 404)
        self.assertEqual(self.feishu.send.call_count, 1)

    def test_invalid_target_empty_script_and_running_project_do_not_send(self):
        self.feishu.recipient.side_effect = FeishuError("对象未授权", status=422)
        self.assertEqual(self.create().status_code, 422)
        self.feishu.recipient.side_effect = None
        self.feishu.recipient.return_value = {"name": "员工"}
        with self.sessions() as db:
            db.get(DirectorProject, self.project_id).status = "generating"
            db.commit()
        self.assertEqual(self.create().status_code, 409)
        with self.sessions() as db:
            db.get(DirectorProject, self.project_id).status = "ready"
            db.query(DirectorSection).delete()
            db.commit()
        self.assertEqual(self.create().status_code, 409)
        self.assertEqual(len(self.tasks()), 0)
        self.feishu.send.assert_not_called()

    def test_delete_is_blocked_while_sending_and_stale_send_is_shown_unknown(self):
        row = self.create().json()
        with self.sessions() as db:
            task = db.get(ShootingTask, row["id"])
            task.notification_status = "sending"
            task.notification_started_at = datetime.utcnow()
            db.commit()
        self.assertEqual(self.client.delete(f"/api/director-projects/{self.project_id}").status_code, 409)
        with self.sessions() as db:
            task = db.get(ShootingTask, row["id"])
            task.notification_started_at = datetime.utcnow() - timedelta(minutes=10)
            db.commit()
        self.assertEqual(self.tasks()[0]["notification_status"], "unknown")
        self.assertEqual(self.client.delete(f"/api/director-projects/{self.project_id}").status_code, 200)
        self.assertEqual(self.tasks(), [])

    def test_database_prevents_duplicate_active_recipient_even_with_different_uuid(self):
        row = self.create().json()
        with self.sessions() as db:
            existing = db.get(ShootingTask, row["id"])
            values = {column.name: getattr(existing, column.name) for column in ShootingTask.__table__.columns if column.name != "id"}
            values["notification_uuid"] = str(uuid.uuid4())
            db.add(ShootingTask(**values))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_old_app_cannot_retry_with_new_credentials(self):
        self.feishu.send.side_effect = FeishuError("failed")
        row = self.create().json()
        self.feishu.settings.feishu_app_id = "cli_different"
        response = self.client.post(f"/api/director-projects/shooting-tasks/{row['id']}/retry-notification")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.feishu.send.call_count, 1)
