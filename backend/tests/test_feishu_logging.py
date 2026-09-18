import asyncio
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

from app.services import feishu_logging as logs
from app.services.feishu import FeishuClient, FeishuError, _cache


class CaptureHandler(logging.Handler):
    def __init__(self, records):
        super().__init__()
        self.records = records

    def emit(self, record):
        self.records.append(record.feishu_event)


class FeishuLoggingTests(unittest.TestCase):
    def setUp(self):
        _cache.clear()
        self.events = []
        self.logger = logging.Logger("isolated-feishu-test", level=logging.INFO)
        self.logger.addHandler(CaptureHandler(self.events))
        self.settings = SimpleNamespace(feishu_app_id="cli_test", feishu_app_secret="app-secret-fixture",
                                        feishu_log_raw=True, feishu_log_dir="")
        self.logger_patch = patch.object(logs, "_configured_logger", return_value=self.logger)
        self.logger_patch.start()
        self.addCleanup(self.logger_patch.stop)
        settings_patch = patch.object(logs, "get_settings", return_value=self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def client(self, responder):
        def handle(request):
            if request.url.path.startswith("/open-apis/auth/"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "token-secret-fixture", "expire": 7200})
            return responder(request)
        return FeishuClient(self.settings, "tenant-test", transport=httpx.MockTransport(handle))

    def test_business_error_preserves_upstream_diagnostics_but_scrubs_credentials(self):
        client = self.client(lambda request: httpx.Response(200, headers={"x-tt-logid": "upstream-123", "set-cookie": "session=hidden"}, json={
            "code": 230002, "msg": "denied app-secret-fixture token-secret-fixture", "error": {"details": ["missing permission"]},
            "data": {"mobile": "13800138000", "email": "private@example.com", "access_token": "new-secret-fixture"}}))
        with logs.trace_context(request_id="browser-request-1", task_id=42, notification_uuid="notice-uuid"):
            with self.assertRaises(FeishuError):
                client.send("user", "ou_test", "test script", "notice-uuid")
        serialized = json.dumps(self.events, ensure_ascii=False)
        for secret in ["app-secret-fixture", "token-secret-fixture", "new-secret-fixture", "13800138000", "private@example.com", "session=hidden"]:
            self.assertNotIn(secret, serialized)
        final = self.events[-1]
        self.assertEqual(final["http_status"], 200)
        self.assertEqual(final["business_code"], 230002)
        self.assertEqual(final["level"], "WARNING")
        self.assertEqual(final["upstream_request_id"], "upstream-123")
        self.assertEqual(final["response"]["body"]["error"]["details"], ["missing permission"])
        self.assertEqual(final["task_id"], 42)
        self.assertTrue(all(event["request_id"] == "browser-request-1" for event in self.events))
        self.assertEqual(logs.trace_fields(), {})

    def test_token_refresh_has_distinct_calls_and_retains_notification_uuid(self):
        attempts = []
        def respond(request):
            attempts.append(json.loads(request.content))
            return httpx.Response(200, json={"code": 99991663} if len(attempts) == 1 else {"code": 0, "data": {"message_id": "om_test"}})
        with logs.trace_context(request_id="refresh-test", notification_uuid="stable-uuid"):
            self.client(respond).send("user", "ou_test", "hello", "stable-uuid")
        started = [event for event in self.events if event["event"] == "feishu.http.started"]
        completed = [event for event in self.events if event["event"] == "feishu.http.completed"]
        self.assertEqual(len(started), 4)
        self.assertEqual(len(self.events), 8)
        self.assertEqual(len({event["call_id"] for event in started}), 4)
        self.assertEqual({event["call_id"] for event in started}, {event["call_id"] for event in completed})
        self.assertTrue(all(event["notification_uuid"] == "stable-uuid" for event in self.events))
        self.assertEqual(attempts[0], attempts[1])

    def test_non_json_error_and_http_rejection_have_accurate_uncertainty(self):
        for status, uncertain in [(502, True), (408, True), (429, False), (403, False)]:
            with self.subTest(status=status):
                with self.assertRaises(FeishuError):
                    self.client(lambda request: httpx.Response(status, text="gateway app-secret-fixture token-secret-fixture")).send("user", "ou_test", "x", "uuid")
                final = self.events[-1]
                self.assertEqual(final["http_status"], status)
                self.assertEqual(final["level"], "ERROR" if status >= 500 else "WARNING")
                self.assertEqual(final["uncertain"], uncertain)
                self.assertIn("gateway", final["response"]["body"])
                self.assertNotIn("secret-fixture", json.dumps(final))

    def test_transport_failure_has_no_http_status_and_preserves_error_class(self):
        for error, uncertain in [(httpx.ReadTimeout("sensitive exception"), True), (httpx.ConnectError("sensitive exception"), False)]:
            def respond(request):
                raise error
            with self.assertRaises(FeishuError):
                self.client(respond).send("user", "ou_test", "x", "uuid")
            final = self.events[-1]
            self.assertIsNone(final["http_status"])
            self.assertEqual(final["level"], "ERROR")
            self.assertEqual(self.events[-2]["event"], "feishu.http.started")
            self.assertEqual(self.events[-2]["call_id"], final["call_id"])
            self.assertEqual(final["error_type"], type(error).__name__)
            self.assertEqual(final["uncertain"], uncertain)
            self.assertNotIn("sensitive exception", json.dumps(final))

    def test_cached_directory_and_token_are_silent_and_each_page_has_one_event_pair(self):
        requests = []

        def respond(request):
            requests.append(request)
            more = "page_token" not in request.url.params
            return httpx.Response(200, json={"code": 0, "data": {
                "items": [], "has_more": more, "page_token": "next" if more else ""}})

        client = self.client(respond)
        first = client.directory("chat")
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(self.events), 6)  # Token plus two actual page requests.
        for start, end in zip(self.events[::2], self.events[1::2]):
            self.assertEqual(start["event"], "feishu.http.started")
            self.assertEqual(end["event"], "feishu.http.completed")
            self.assertEqual(start["call_id"], end["call_id"])
        self.events.clear()
        self.assertEqual(client.directory("chat"), first)
        self.assertEqual(client.token(), "token-secret-fixture")
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.events, [])

    def test_malformed_success_is_error_with_raw_response_and_uncertain_delivery(self):
        for body in ["not-json", {"code": "0"}, {"code": 0}, {"code": 0, "data": {}}]:
            with self.subTest(body=body):
                self.events.clear()
                response = httpx.Response(200, text=body) if isinstance(body, str) else httpx.Response(200, json=body)
                with self.assertRaises(FeishuError) as caught:
                    self.client(lambda request: response).send("user", "ou_test", "hello", "uuid")
                self.assertTrue(caught.exception.uncertain)
                final = self.events[-1]
                self.assertEqual(final["level"], "ERROR")
                self.assertTrue(final["uncertain"])
                self.assertEqual(final["response"]["body"], body)
                self.assertIn("响应格式异常", final["remark"])

    def test_nested_content_is_redacted_without_modifying_original_request(self):
        payload = {"content": json.dumps({"app_secret": "secret-in-json", "email": "me@example.com", "text": "hello"})}
        original = json.dumps(payload)
        logs.log_event("test.raw", "test", request=payload)
        self.assertEqual(json.dumps(payload), original)
        encoded = json.dumps(self.events)
        self.assertNotIn("secret-in-json", encoded)
        self.assertNotIn("me@example.com", encoded)
        self.assertIn("hello", encoded)

    def test_large_body_is_marked_truncated_and_raw_can_be_disabled(self):
        logs.log_event("test.raw", "test", response={"body": "x" * (logs.BODY_LIMIT * 2)})
        self.assertTrue(self.events[-1]["response"]["truncated"])
        self.assertGreater(self.events[-1]["response"]["original_chars"], logs.BODY_LIMIT)
        self.settings.feishu_log_raw = False
        logs.log_event("test.raw", "test", response={"body": "raw omitted"}, http_status=200)
        self.assertNotIn("response", self.events[-1])
        self.assertEqual(self.events[-1]["http_status"], 200)

    def test_logging_failure_does_not_prevent_sending(self):
        with patch.object(logs, "_configured_logger", side_effect=OSError("disk full")), self.assertLogs("uvicorn.error", level="ERROR"):
            result = self.client(lambda request: httpx.Response(200, json={"code": 0, "data": {"message_id": "om_ok"}})).send("user", "ou_test", "hello", "uuid")
        self.assertEqual(result, "om_ok")

    def test_raw_log_is_utf8_json_lines_and_summary_is_readable_single_line(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "test.raw.log"
            handler = logs.SafeFileHandler(target, maxBytes=100000, backupCount=1, encoding="utf-8")
            handler.setFormatter(logs.JsonFormatter())
            self.logger.addHandler(handler)
            stream = io.StringIO()
            console = logging.StreamHandler(stream)
            console.setFormatter(logs.SummaryFormatter())
            self.logger.addHandler(console)
            try:
                logs.log_event("test.summary", "发送失败\n换行内容", http_status=200, business_code=230002,
                               business_message="Denied\nforged log", response={"body": {"code": 230002}})
                handler.flush()
                self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["http_status"], 200)
                self.assertEqual(len(stream.getvalue().splitlines()), 1)
                self.assertIn("飞书业务码=230002", stream.getvalue())
                self.assertIn("发送失败", stream.getvalue())
            finally:
                self.logger.removeHandler(handler)
                self.logger.removeHandler(console)
                handler.close()

    def test_concurrent_requests_and_threadpool_share_only_their_own_request_id(self):
        app = FastAPI()
        app.add_middleware(logs.RequestTraceMiddleware)

        @app.get("/api/director-projects/logging-test")
        async def endpoint():
            await asyncio.sleep(0)
            def sync_call():
                self.client(lambda request: httpx.Response(200, json={"code": 0, "data": {}}))._request("GET", "/im/v1/chats")
                return logs.trace_fields()
            return await run_in_threadpool(sync_call)

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await asyncio.gather(*[client.get("/api/director-projects/logging-test", headers={"x-request-id": key}) for key in ("request-a", "request-b")])
        responses = asyncio.run(run())
        for response, expected in zip(responses, ("request-a", "request-b")):
            self.assertEqual(response.headers["x-request-id"], expected)
            self.assertEqual(response.json()["request_id"], expected)
            self.assertEqual(len([event for event in self.events if event["request_id"] == expected]), 2)
        self.assertEqual(logs.trace_fields(), {})
        self.assertEqual({event["event"] for event in self.events}, {"feishu.http.started", "feishu.http.completed"})

    def test_middleware_propagates_trace_but_does_not_log_local_success_or_exception(self):
        app = FastAPI()
        app.add_middleware(logs.RequestTraceMiddleware)

        @app.get("/api/director-projects/test")
        def endpoint():
            return logs.trace_fields()

        @app.get("/api/director-projects/error")
        def error():
            raise RuntimeError("secret exception message")

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/director-projects/test", headers={"x-request-id": "x" * 100})
            self.assertEqual(len(response.headers["x-request-id"]), 32)
            self.assertEqual(response.headers["x-request-id"], response.json()["request_id"])
            self.assertEqual(client.get("/api/director-projects/error").status_code, 500)
        self.assertEqual(self.events, [])
        self.assertEqual(logs.trace_fields(), {})

    def test_main_audit_and_response_use_same_trace_without_starting_workers(self):
        from app.main import app
        from app.models import AuditLog

        fake_db = Mock()
        with patch("app.main.SessionLocal", return_value=fake_db), patch("app.main.get_redis", return_value=None):
            # No context manager: do not run the real database/worker lifespan.
            client = TestClient(app)
            try:
                response = client.get("/api/health", headers={"x-request-id": "audit-trace-test"})
            finally:
                client.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-request-id"], "audit-trace-test")
        audit = fake_db.add.call_args.args[0]
        self.assertIsInstance(audit, AuditLog)
        self.assertEqual(audit.request_id, "audit-trace-test")
        self.assertEqual(self.events, [])

    def test_reader_filters_across_rotated_files_and_expands_original_data(self):
        from scripts.read_feishu_logs import read_events, render_event

        with tempfile.TemporaryDirectory() as directory:
            for name, events in [("2026-09-15/feishu-worker.raw.log.1", [{"time": "2026-09-16T01:00:00", "request_id": "wanted", "task_id": 4}]),
                                 ("2026-09-16/feishu-worker.raw.log", [{"time": "2026-09-16T02:00:00", "request_id": "other"},
                                                          {"time": "2026-09-16T03:00:00", "request_id": "wanted", "task_id": 4,
                                                           "response": {"body": {"code": 230002}}}])]:
                Path(directory, name).parent.mkdir(parents=True, exist_ok=True)
                Path(directory, name).write_text("\n".join(json.dumps(event) for event in events) + "\n{incomplete", encoding="utf-8")
            Path(directory, "feishu-legacy.jsonl").write_text(json.dumps({"time": "2026-09-16T00:00:00", "request_id": "wanted", "task_id": 4}), encoding="utf-8")
            # The human-readable summary must not duplicate raw events in search results.
            Path(directory, "feishu-worker.log").write_text(json.dumps({"time": "2026-09-16T04:00:00", "request_id": "wanted", "task_id": 4}), encoding="utf-8")
            self.assertEqual(len(read_events(directory, request_id="wanted", task_id=4)), 3)
            selected = read_events(directory, request_id="wanted", task_id=4, limit=1)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["time"], "2026-09-16T03:00:00")
        self.assertNotIn('"body"', render_event(selected[0]))
        self.assertIn('"body"', render_event(selected[0], raw=True))

    def test_configured_logger_writes_both_readable_and_raw_log_files(self):
        self.logger_patch.stop()
        isolated_logger = logging.Logger("file-output-test")
        with tempfile.TemporaryDirectory() as directory, patch.object(logs, "_logger", isolated_logger):
            self.settings.feishu_log_dir = directory
            try:
                logs.log_event("test.files", "中文摘要", request_id="two-files-test", http_status=200,
                               response={"body": {"code": 0, "app_secret": "must-be-redacted"}})
                raw_files = list(Path(directory).rglob("*.raw.log"))
                summary_files = [path for path in Path(directory).rglob("*.log") if path not in raw_files]
                self.assertEqual(len(raw_files), 1)
                self.assertEqual(len(summary_files), 1)
                raw = raw_files[0].read_text(encoding="utf-8")
                summary = summary_files[0].read_text(encoding="utf-8")
                self.assertEqual(json.loads(raw)["response"]["body"]["code"], 0)
                self.assertIn("中文摘要", summary)
                self.assertIn("HTTP=200", summary)
                self.assertNotIn("must-be-redacted", raw + summary)
            finally:
                for handler in isolated_logger.handlers[:]:
                    handler.close()
                    isolated_logger.removeHandler(handler)

    def test_daily_files_switch_at_beijing_midnight_and_append_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            def write_events(times):
                handler = logs.DailyFileHandler(directory, "feishu-test.raw.log", maxBytes=100000, backupCount=3, encoding="utf-8")
                handler.setFormatter(logs.JsonFormatter())
                try:
                    for timestamp in times:
                        record = logging.LogRecord("test", logging.INFO, "", 0, "test", (), None)
                        record.feishu_event = {"time": timestamp, "remark": "跨天测试"}
                        handler.handle(record)
                finally:
                    handler.close()

            write_events(["2026-09-16T15:59:59+00:00", "2026-09-16T16:00:00+00:00"])
            write_events(["2026-09-16T16:01:00+00:00"])
            before = Path(directory, "2026-09-16", "feishu-test.raw.log").read_text(encoding="utf-8").splitlines()
            after = Path(directory, "2026-09-17", "feishu-test.raw.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(before), 1)
            self.assertEqual(len(after), 2)
            self.assertEqual(json.loads(after[0])["time"], "2026-09-16T16:00:00+00:00")
            self.assertEqual(list(Path(directory).glob("*.log")), [])
