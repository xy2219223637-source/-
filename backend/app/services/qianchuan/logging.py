"""Bounded console/file logging with opt-in sanitized material response previews."""
import logging
import json
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
RESPONSE_LIMIT = 16000


def response_preview(payload, secrets: list[str]) -> str:
    """Preserve material JSON structure while redacting credentials and bounding output."""
    def sensitive(key):
        key = re.sub(r"[^a-z0-9]", "", str(key).lower())
        return any(term in key for term in ("token", "secret", "password", "authorization",
                                            "cookie", "authcode", "signature"))

    known = {value for value in secrets if isinstance(value, str) and value}

    def collect(value, depth=0):
        if depth > 10:
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
        return text[:4000] + ("...[truncated]" if len(text) > 4000 else "")

    def clean(value, depth=0):
        if depth > 10:
            return "[depth truncated]"
        if isinstance(value, dict):
            result = {scrub(str(key)): "[REDACTED]" if sensitive(key) else clean(child, depth + 1)
                      for key, child in list(value.items())[:100]}
            if len(value) > 100:
                result["_truncated"] = True
            return result
        if isinstance(value, list):
            return [clean(child, depth + 1) for child in value[:100]] + (["[truncated]"] if len(value) > 100 else [])
        return scrub(value) if isinstance(value, str) else value

    encoded = json.dumps(clean(payload), ensure_ascii=False)
    if len(encoded) > RESPONSE_LIMIT:
        return json.dumps({"truncated": True, "preview": encoded[:RESPONSE_LIMIT // 4]}, ensure_ascii=False)
    return encoded


def configure_logging(log_dir: Path | None = None) -> None:
    logger = logging.getLogger("app.services.qianchuan")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
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
