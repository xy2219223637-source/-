"""Shared validation helpers for browser-assisted video capture."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from pathlib import Path
from urllib.parse import unquote, urlparse


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
CONTENT_TYPE_SUFFIXES = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-m4v": ".m4v",
    "video/webm": ".webm",
}


def capture_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host == "douyin.com" or host.endswith(".douyin.com"):
        return "douyin"
    if host in {"xiaohongshu.com", "xhslink.com"} or host.endswith((".xiaohongshu.com", ".xhslink.com")):
        return "xiaohongshu"
    if host == "kuaishou.com" or host.endswith(".kuaishou.com"):
        return "kuaishou"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    return "generic"


def new_capture_token() -> str:
    return secrets.token_urlsafe(32)


def capture_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def capture_token_matches(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(capture_token_hash(token), expected_hash)


def capture_filename(raw_filename: str, content_type: str, platform: str) -> str:
    value = unquote(raw_filename.strip())
    if "://" in value:
        value = Path(urlparse(value).path).name
    else:
        value = Path(value).name
    suffix = Path(value).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        suffix = CONTENT_TYPE_SUFFIXES.get(content_type.lower(), ".mp4")
    stem = Path(value).stem if value else f"{platform}-video"
    stem = re.sub(r"[^\w .-]+", "-", stem, flags=re.UNICODE).strip(" .-")[:96]
    return f"{stem or f'{platform}-video'}{suffix}"
