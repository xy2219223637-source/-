"""Synthetic responses only: verify useful diagnostics without credential leakage."""
import io
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import tempfile
import unittest

import httpx

from app.config import Settings
from app.services.qianchuan.client import PlatformError, QianchuanClient
from app.services.qianchuan.logging import configure_logging, response_preview, RESPONSE_LIMIT, ChineseLogFormatter, log_context, QUERY_ID, event_fields
import json


class QianchuanLoggingTests(unittest.TestCase):
    def test_detailed_logs_are_enabled_by_default(self):
        self.assertTrue(Settings(_env_file=None).qianchuan_log_material_response)

    def test_chinese_blocks_keep_payload_readable_and_share_query_id(self):
        with self.assertLogs("app.services.qianchuan.client", level="INFO") as capture:
            self.run_request(lambda request: httpx.Response(200, json={"code": 0, "message": "成功",
                "request_id": "req-1", "data": {"count": 0, "missing": None}}),
                response_logging=True, path="/open_api/v1.0/qianchuan/video/get/")
        formatted = [ChineseLogFormatter().format(record) for record in capture.records]
        self.assertEqual(len(capture.records), 1)
        self.assertIn("[信息 INFO] 千川接口｜调用成功", formatted[0])
        self.assertIn("接口名称：查询视频素材", formatted[0])
        self.assertIn('"平台原始回参（已脱敏）": {', formatted[0])
        self.assertIn('"count": 0', formatted[0])
        self.assertIn('"missing": null', formatted[0])
        self.assertTrue(capture.records[0].qc_fields['查询编号'].startswith('qc-'))

    def test_log_context_resets_and_field_newlines_cannot_forge_headers(self):
        with log_context():
            first = QUERY_ID.get()
            with log_context():
                self.assertEqual(first, QUERY_ID.get())
            record = logging.LogRecord('test', logging.INFO, '', 0, 'internal', (), None)
            record.__dict__.update(event_fields('中文事件', {'账户': 'name\nFAKE LOG'}))
            self.assertNotIn('\nFAKE LOG', ChineseLogFormatter().format(record))
        self.assertEqual(QUERY_ID.get(), '')
        with log_context():
            self.assertNotEqual(QUERY_ID.get(), first)

    def test_legacy_authorization_event_has_chinese_labels(self):
        record = logging.LogRecord('test', logging.INFO, '', 0,
            'qianchuan action=accounts_synced tenant=%s connection=%s count=%s', ('tenant-1', 'conn-1', 2), None)
        text = ChineseLogFormatter().format(record)
        self.assertIn('千川授权｜账户同步完成', text)
        self.assertIn('账户数量：2', text)

    def run_request(self, provider, *, response_logging=False, path="/open_api/test/?auth_code=query-fixture", **kwargs):
        client = QianchuanClient(Settings(_env_file=None, qianchuan_app_secret="secret-fixture",
                                         qianchuan_log_material_response=response_logging),
                                 httpx.MockTransport(provider))
        self.addCleanup(client.close)
        return client.request("POST", path, **kwargs)

    def test_material_response_requires_opt_in_and_excludes_auth_paths(self):
        for enabled, path, expected in (
            (False, "/open_api/v1.0/qianchuan/video/get/", False),
            (True, "/open_api/v1.0/qianchuan/video/get/", True),
            (True, "/open_api/oauth2/access_token/", False),
            (True, "/open_api/oauth2/refresh_token/", False),
        ):
            with self.subTest(enabled=enabled, path=path):
                with self.assertLogs("app.services.qianchuan.client", level="INFO") as capture:
                    result = self.run_request(lambda request: httpx.Response(200, json={"code": 0,
                        "message": "OK", "request_id": "req-1", "data": {"material_id": "123"}}),
                        response_logging=enabled, path=path)
                self.assertEqual(result, {"material_id": "123"})
                self.assertEqual(len(capture.records), 1)
                self.assertEqual(capture.records[0].qc_details is not None, expected)
                if expected:
                    preview = json.loads(capture.output[0].split("response=", 1)[1])
                    self.assertEqual(preview["data"]["material_id"], "123")

    def test_platform_error_message_is_visible_but_secrets_are_redacted(self):
        with self.assertLogs("app.services.qianchuan.client", level="INFO") as capture:
            with self.assertRaises(PlatformError):
                self.run_request(lambda request: httpx.Response(200, json={"code": 40000,
                    "message": "缺少投放能力 token-fixture", "data": {"Access-Token": "returned-secret",
                    "nested": [{"refresh_token": "refresh-secret"}],
                    "url": "https://example.com/video?signature=signed-secret",
                    "detail": "returned-secret secret-fixture refresh-secret"}}),
                    token="token-fixture", response_logging=True,
                    path="/open_api/v1.0/qianchuan/report/uni_promotion/data/get/")
        output = "\n".join(capture.output)
        self.assertIn("缺少投放能力", output)
        for value in ("token-fixture", "returned-secret", "refresh-secret", "secret-fixture", "signed-secret"):
            self.assertNotIn(value, output)

    def test_response_preview_is_bounded_and_does_not_mutate_payload(self):
        payload = {"data": {str(i): "中文\n" * 2000 for i in range(100)}, "access_token": "sensitive-value"}
        preview = response_preview(payload, [])
        self.assertLessEqual(len(preview), RESPONSE_LIMIT)
        self.assertTrue(json.loads(preview)["truncated"])
        self.assertEqual(payload["access_token"], "sensitive-value")
        self.assertNotIn("sensitive-value", preview)

    def test_raw_response_retains_long_lists_strings_and_nested_credentials(self):
        nested = {'access_token': 'deep-credential'}
        for _ in range(12):
            nested = {'nested': nested}
        payload = {'code': 0, 'data': {'rows': [{'index': i, 'value': '文' * 5000}
            for i in range(101)], 'deep': nested, 'message': 'deep-credential',
            'zero': 0, 'missing': None, 'enabled': False}}
        with self.assertLogs('app.services.qianchuan.client', level='INFO') as capture:
            result = self.run_request(lambda r: httpx.Response(200, json=payload),
                response_logging=True, path='/open_api/v1.0/qianchuan/report/uni_promotion/data/get/')
        self.assertEqual(len(capture.records), 1)
        raw = capture.records[0].qc_details['平台原始回参（已脱敏）']['data']
        self.assertEqual(raw['rows'], payload['data']['rows'])
        self.assertEqual((raw['zero'], raw['missing'], raw['enabled']), (0, None, False))
        self.assertNotIn('deep-credential', json.dumps(raw))
        self.assertEqual(result['message'], 'deep-credential')

    def test_non_json_material_response_is_not_dumped(self):
        with self.assertLogs("app.services.qianchuan.client", level="INFO") as capture:
            with self.assertRaises(PlatformError):
                self.run_request(lambda request: httpx.Response(502, text="unsafe-html-response"),
                    response_logging=True, path="/open_api/v1.0/qianchuan/video/get/")
        self.assertIn("response is not valid JSON", capture.output[0])
        self.assertNotIn("unsafe-html-response", "\n".join(capture.output))

    def test_success_logs_diagnostics_without_request_or_response_secrets(self):
        with self.assertLogs("app.services.qianchuan.client", level="INFO") as capture:
            self.run_request(lambda request: httpx.Response(200, json={"code": 0,
                "request_id": "request-123", "data": {"access_token": "returned-fixture"}}),
                token="token-fixture", body={"secret": "secret-fixture", "auth_code": "code-fixture"})
        output = "\n".join(capture.output)
        for expected in ("outcome=success", "http_status=200", "provider_code=0",
                         "request_id=request-123", "duration_ms=", "path=/open_api/test/"):
            self.assertIn(expected, output)
        for secret in ("query-fixture", "returned-fixture", "token-fixture", "secret-fixture", "code-fixture"):
            self.assertNotIn(secret, output)

    def test_rejection_logs_code_but_not_platform_message_or_tainted_request_id(self):
        with self.assertLogs("app.services.qianchuan.client", level="WARNING") as capture:
            with self.assertRaises(PlatformError):
                self.run_request(lambda request: httpx.Response(200, json={"code": 40000,
                    "message": "secret-fixture", "request_id": "prefix-token-fixture"}), token="token-fixture")
        output = "\n".join(capture.output)
        self.assertIn("provider_code=40000", output)
        self.assertIn("error=provider_rejected", output)
        self.assertIn("request_id=-", output)
        self.assertNotIn("secret-fixture", output)
        self.assertNotIn("token-fixture", output)

    def test_http_and_invalid_response_failures_are_logged(self):
        for response, expected in ((httpx.Response(503, text="response-secret"), "http_error"),
                                   (httpx.Response(200, text="response-secret"), "invalid_response")):
            with self.subTest(error=expected):
                with self.assertLogs("app.services.qianchuan.client", level="WARNING") as capture:
                    with self.assertRaises(PlatformError):
                        self.run_request(lambda request: response)
                self.assertIn("error=" + expected, capture.output[0])
                self.assertNotIn("response-secret", capture.output[0])

    def test_transport_failure_does_not_log_exception_text(self):
        def fail(request):
            raise httpx.ReadTimeout("transport-secret", request=request)
        with self.assertLogs("app.services.qianchuan.client", level="WARNING") as capture:
            with self.assertRaises(PlatformError):
                self.run_request(fail)
        self.assertIn("error=transport_uncertain", capture.output[0])
        self.assertNotIn("transport-secret", capture.output[0])

    def test_console_and_file_emit_info_once_without_changing_root(self):
        logger = logging.getLogger("app.services.qianchuan")
        old = (logger.handlers[:], logger.level, logger.propagate)
        root_level = logging.getLogger().level
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        log_dir = Path(directory.name)
        try:
            logger.handlers = []
            configure_logging(log_dir)
            configure_logging(log_dir)
            self.assertEqual(len(logger.handlers), 2)
            stream = io.StringIO()
            logger.handlers[0].setStream(stream)
            logging.getLogger("app.services.qianchuan.service").info("authorize_started")
            self.assertEqual(stream.getvalue().count("authorize_started"), 1)
            self.assertIn("INFO", stream.getvalue())
            self.assertEqual((log_dir / "qianchuan.log").read_text(encoding="utf-8").count("authorize_started"), 1)
            self.assertEqual(logging.getLogger().level, root_level)
        finally:
            for handler in logger.handlers:
                handler.close()
            logger.handlers, logger.level, logger.propagate = old

    def test_rotation_keeps_five_backups_and_discards_oldest(self):
        logger = logging.getLogger("app.services.qianchuan")
        old = (logger.handlers[:], logger.level, logger.propagate)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        log_dir = Path(directory.name)
        try:
            logger.handlers = []
            configure_logging(log_dir)
            handler = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
            self.assertEqual(handler.maxBytes, 10 * 1024 * 1024)
            self.assertEqual(handler.backupCount, 5)
            # Shrink only this test handler to exercise actual rotation cheaply.
            handler.maxBytes = 180
            logger.handlers[0].setStream(io.StringIO())
            for index in range(12):
                logger.info("event-%02d 中文日志 %s", index, "x" * 80)
            files = list(log_dir.iterdir())
            self.assertEqual({p.name for p in files}, {"qianchuan.log", *(
                "qianchuan.log." + str(i) for i in range(1, 6))})
            contents = "".join(p.read_text(encoding="utf-8") for p in files)
            self.assertNotIn("event-00", contents)
            self.assertIn("event-11", (log_dir / "qianchuan.log").read_text(encoding="utf-8"))
            self.assertIn("中文日志", contents)
        finally:
            for handler in logger.handlers:
                handler.close()
            logger.handlers, logger.level, logger.propagate = old


if __name__ == "__main__":
    unittest.main()
