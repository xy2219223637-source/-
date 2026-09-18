"""Readable .log files and correlated, redacted JSON-line .raw.log records."""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from starlette.datastructures import Headers, MutableHeaders

from app.config import get_settings

_context: ContextVar[dict] = ContextVar("feishu_log_context", default={})
_lock = threading.Lock()
_logger = logging.getLogger("app.feishu")
BODY_LIMIT = 65536
LOG_TIMEZONE = timezone(timedelta(hours=8))
_secret_keys = {"authorization", "cookie", "set_cookie", "app_secret", "client_secret", "secret",
                "password", "access_token", "tenant_access_token", "user_access_token", "refresh_token",
                "api_key", "x_api_key"}
_personal_keys = {"email", "enterprise_email", "mobile", "phone", "phone_number"}
_credential_text = re.compile(
    r'''(?i)(authorization|app_secret|client_secret|password|tenant_access_token|user_access_token|access_token|refresh_token|api_key)["']?\s*[:=]\s*["']?(?:Bearer\s+)?[^\s,"'<>&}]+''')


def trace_fields():
    return dict(_context.get())


@contextmanager
def trace_context(**fields):
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def _credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower().replace("-", "_") in _secret_keys and isinstance(item, str) and item:
                yield item
            yield from _credentials(item)
    elif isinstance(value, list):
        for item in value:
            yield from _credentials(item)


def redact(value, secrets=(), depth=0):
    if depth > 25:
        return "[嵌套过深，已省略]"
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if str(key).lower().replace("-", "_") in _secret_keys | _personal_keys
                else redact(item, secrets, depth + 1) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(item, secrets, depth + 1) for item in value]
    if isinstance(value, str):
        for secret in sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        # Feishu message content is JSON encoded inside a JSON string.
        if value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(redact(json.loads(value), secrets, depth + 1), ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
        value = _credential_text.sub(lambda match: match.group(1) + "=[REDACTED]", value)
        value = re.sub(r"(?i)\bBearer\s+[^\s,\"'<>]+", "Bearer [REDACTED]", value)
        if "@" in value:
            value = re.sub(r"(?<![\w.+-])[\w.+-]{1,64}@[\w.-]{1,253}\.[A-Za-z]{2,63}", "[EMAIL_REDACTED]", value)
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"[{type(value).__name__}]"


def _limited(value):
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized) <= BODY_LIMIT:
        return value
    return {"truncated": True, "original_chars": len(serialized), "limit_chars": BODY_LIMIT,
            "preview": serialized[:BODY_LIMIT], "note": "脱敏后正文超过上限，以下仅为前段内容"}


class SummaryFormatter(logging.Formatter):
    def format(self, record):
        event = record.feishu_event
        labels = {"request_id": "请求", "call_id": "调用", "task_id": "任务", "notification_uuid": "通知",
                  "http_status": "HTTP", "business_code": "飞书业务码", "duration_ms": "耗时毫秒",
                  "upstream_request_id": "飞书日志ID", "notification_status": "通知状态", "error_type": "异常类型",
                  "method": "方法", "path": "接口", "business_message": "飞书说明", "reason": "原因"}
        details = " ".join(f"{label}={json.dumps(event[key], ensure_ascii=False)}"
                           for key, label in labels.items() if key in event and event[key] is not None)
        # Quote untrusted newlines so they cannot forge extra console log entries.
        return f"{event['time']} {record.levelname} [{event['event']}] {json.dumps(event['remark'], ensure_ascii=False)} {details}"


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(record.feishu_event, ensure_ascii=False)


class SafeFileHandler(RotatingFileHandler):
    def handleError(self, record):
        logging.getLogger("uvicorn.error").error("飞书诊断日志写入失败，请检查磁盘空间和目录权限")


class DailyFileHandler(SafeFileHandler):
    """Select a Beijing-date directory for each event, including across midnight."""
    def __init__(self, directory, filename, **kwargs):
        self.directory = Path(directory).resolve()
        self.filename = filename
        super().__init__(self.directory / filename, delay=True, **kwargs)

    def emit(self, record):
        try:
            event_time = datetime.fromisoformat(record.feishu_event["time"])
            day = event_time.astimezone(LOG_TIMEZONE).strftime("%Y-%m-%d")
            target = self.directory / day / self.filename
            if self.baseFilename != str(target):
                target.parent.mkdir(parents=True, exist_ok=True)
                if self.stream:
                    self.stream.close()
                    self.stream = None
                self.baseFilename = str(target)
            super().emit(record)
        except Exception:
            self.handleError(record)


def _configured_logger():
    if not _logger.handlers:
        with _lock:
            if not _logger.handlers:
                settings = get_settings()
                directory = Path(settings.feishu_log_dir or Path(__file__).resolve().parents[2] / "logs")
                directory.mkdir(parents=True, exist_ok=True)
                filename = f"feishu-{socket.gethostname()}-{os.getpid()}"
                for suffix, formatter in ((".log", SummaryFormatter()), (".raw.log", JsonFormatter())):
                    handler = DailyFileHandler(directory, f"{filename}{suffix}", maxBytes=10 * 1024 * 1024,
                                               backupCount=3, encoding="utf-8")
                    handler.setFormatter(formatter)
                    _logger.addHandler(handler)
                console = logging.StreamHandler()
                console.setFormatter(SummaryFormatter())
                _logger.addHandler(console)
                _logger.setLevel(logging.INFO)
                _logger.propagate = False
    return _logger


def log_event(event, remark, *, level=logging.INFO, secrets=(), **fields):
    """Log failures must never change whether a message is sent or retried."""
    try:
        record = {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "level": logging.getLevelName(level),
                  "event": event, "remark": remark, **trace_fields(), **fields}
        record = redact(record, (*secrets, *_credentials(record)))
        for key in ("request", "response"):
            if key in record:
                if get_settings().feishu_log_raw:
                    record[key] = _limited(record[key])
                else:
                    record.pop(key)
        _configured_logger().log(level, record["remark"], extra={"feishu_event": record})
    except Exception:
        logging.getLogger("uvicorn.error").error("飞书诊断日志不可用；业务请求继续处理，请检查日志配置")


class RequestTraceMiddleware:
    """Silently propagate one id through endpoints, audit records and Feishu calls."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        candidate = Headers(scope=scope).get("x-request-id", "")
        request_id = candidate if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", candidate) else uuid.uuid4().hex
        async def traced_send(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["x-request-id"] = request_id
            await send(message)

        with trace_context(request_id=request_id):
            await self.app(scope, receive, traced_send)
