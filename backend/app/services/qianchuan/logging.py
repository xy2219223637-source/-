"""Chinese event logs with sanitized provider responses and file rotation."""
import logging
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
RESPONSE_LIMIT = 16000
QUERY_ID = ContextVar("qianchuan_query_id", default="")
API_CALLS = ContextVar("qianchuan_api_calls", default=None)


@contextmanager
def collect_api_calls():
    """Keep provider calls local to one report query, including failure paths."""
    calls = []
    token = API_CALLS.set(calls)
    try:
        yield calls
    finally:
        API_CALLS.reset(token)


@contextmanager
def log_context():
    token = QUERY_ID.set(QUERY_ID.get() or "qc-" + uuid4().hex[:12])
    try:
        yield
    finally:
        QUERY_ID.reset(token)


def event_fields(title, fields, details=None):
    return {"qc_title": title, "qc_fields": {"查询编号": QUERY_ID.get() or "—", **fields},
            "qc_details": details}


class ChineseLogFormatter(logging.Formatter):
    """Render an entire event as one record so concurrent blocks do not interleave."""
    def format(self, record):
        level = {"INFO": "信息", "WARNING": "警告", "ERROR": "错误", "DEBUG": "调试"}.get(record.levelname, record.levelname)
        title = getattr(record, "qc_title", None)
        fields = getattr(record, "qc_fields", None)
        if title is None:
            values = dict(re.findall(r"(\w+)=(\S+)", record.getMessage()))
            action = values.get("action")
            names = {"authorize_started": "开始授权", "authorize_finished": "授权完成",
                     "authorize_failed": "授权失败", "accounts_synced": "账户同步完成",
                     "sync_failed": "账户同步失败", "deleted": "删除授权连接", "disabled": "停用授权连接"}
            if action in names:
                title = "千川授权｜" + names[action]
            elif record.getMessage().startswith("qianchuan upload="):
                title = "千川素材｜上传状态变化"
            labels = {"tenant": "租户编号", "actor": "操作用户", "session": "授权会话",
                      "connection": "授权连接", "count": "账户数量", "reason": "失败分类",
                      "upload": "上传任务", "status": "状态"}
            states = {"queued": "等待上传", "running": "处理中", "uploaded": "已上传，等待核验",
                      "unknown": "上传结果待确认", "succeeded": "上传核验成功", "failed": "上传失败"}
            fields = {labels[k]: states.get(v, v) if k == "status" else v
                      for k, v in values.items() if k in labels}
        header = f"{self.formatTime(record)} [{level} {record.levelname}] {title or record.getMessage()}"
        if not title:
            return header
        # Escape untrusted line breaks instead of letting values forge another log event.
        def inline(value):
            return json.dumps(str(value), ensure_ascii=False)[1:-1]
        lines = [header, *(f"  {inline(key)}：{inline(value)}" for key, value in fields.items())]
        details = getattr(record, "qc_details", None)
        if details is not None:
            lines.append("  详细数据：")
            pretty = json.dumps(details, ensure_ascii=False, indent=2)
            lines.extend("    " + line for line in pretty.splitlines())
        return "\n".join(lines)


def response_preview(payload, secrets: list[str], *, truncate=True) -> str:
    """Sanitize JSON without mutating it; raw response logs disable preview limits."""
    def sensitive(key):
        key = re.sub(r"[^a-z0-9]", "", str(key).lower())
        return any(term in key for term in ("token", "secret", "password", "authorization",
                                            "cookie", "authcode", "signature"))

    known = {value for value in secrets if isinstance(value, str) and value}

    def collect(value, depth=0):
        if truncate and depth > 10:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if sensitive(key) and isinstance(child, str) and child:
                    known.add(child)
                collect(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                collect(child, depth + 1)
    collect(payload)

    def scrub(text):
        for secret in sorted(known, key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)\b(?:bearer\s+|(?:access[_-]?token|refresh[_-]?token|auth[_-]?code|secret|password)\s*[:=]\s*)[^\s,;\"'&]+",
                      "[REDACTED]", text)
        def safe_url(match):
            try:
                url = urlsplit(match.group())
                return f"{url.scheme}://{url.hostname or ''}{url.path}"
            except ValueError:
                return "[URL REDACTED]"
        text = re.sub(r'https?://[^\s"<>]+', safe_url, text)
        return text[:4000] + ("...[truncated]" if len(text) > 4000 else "") if truncate else text

    def clean(value, depth=0):
        if truncate and depth > 10:
            return "[depth truncated]"
        if isinstance(value, dict):
            result = {scrub(str(key)): "[REDACTED]" if sensitive(key) else clean(child, depth + 1)
                      for key, child in (list(value.items())[:100] if truncate else value.items())}
            if truncate and len(value) > 100:
                result["_truncated"] = {"说明": "字段过多，已省略", "数量": len(value) - 100}
            return result
        if isinstance(value, list):
            return [clean(child, depth + 1) for child in (value[:100] if truncate else value)] + ([{"truncated": True, "说明": "列表已省略", "数量": len(value) - 100}] if truncate and len(value) > 100 else [])
        return scrub(value) if isinstance(value, str) else value

    encoded = json.dumps(clean(payload), ensure_ascii=False)
    if truncate and len(encoded) > RESPONSE_LIMIT:
        return json.dumps({"truncated": True, "说明": "响应过长，以下仅为部分预览", "preview": encoded[:RESPONSE_LIMIT // 4]}, ensure_ascii=False)
    return encoded


def configure_logging(log_dir: Path | None = None) -> None:
    logger = logging.getLogger("app.services.qianchuan")
    formatter = ChineseLogFormatter()
    if not any(getattr(handler, "_qianchuan_console", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler._qianchuan_console = True
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    if not any(getattr(handler, "_qianchuan_file", False) for handler in logger.handlers):
        directory = log_dir if log_dir is not None else LOG_DIR
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(directory / "qianchuan.log", maxBytes=MAX_BYTES,
                                      backupCount=BACKUP_COUNT, encoding="utf-8", delay=True)
        handler._qianchuan_file = True
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
