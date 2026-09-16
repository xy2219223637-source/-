"""Small application-identity Feishu client. No mock recipients or message fallback."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

import httpx

from app.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

API_BASE = "https://open.feishu.cn/open-apis"
_cache: dict[tuple, tuple[float, object]] = {}
_cache_lock = threading.RLock()


class FeishuError(Exception):
    def __init__(self, message: str, *, status: int = 502, uncertain: bool = False):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain


def configured(settings) -> bool:
    return bool(settings.feishu_enabled is not False and settings.feishu_app_id.strip()
                and settings.feishu_app_secret.strip())


def get_feishu_client(tenant_id: str, db: Session | None = None) -> "FeishuClient":
    settings = get_settings()
    if not configured(settings):
        raise FeishuError("飞书通知尚未配置，请联系管理员", status=503)
    bound_tenant_id = settings.feishu_system_tenant_id.strip()
    if not bound_tenant_id:
        if db is None:
            raise FeishuError("无法确认飞书应用所属企业，请通过已登录的系统使用", status=503)
        from app.models import Tenant
        # Recheck the database on each request; never guess when several enterprises exist.
        tenants = db.query(Tenant.id).filter_by(status="active").limit(2).all()
        if not tenants:
            raise FeishuError("请先创建系统企业后使用飞书通知", status=503)
        if len(tenants) != 1:
            raise FeishuError("系统存在多家企业，请管理员指定飞书应用对应的企业", status=503)
        bound_tenant_id = tenants[0][0]
    if tenant_id != bound_tenant_id:
        raise FeishuError("当前企业未开通飞书通知", status=403)
    return FeishuClient(settings, tenant_id)


def public_script_url(settings, project_id: int) -> str:
    base = settings.app_public_base_url.strip().rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise FeishuError("请先配置可访问的 HTTPS 系统地址 APP_PUBLIC_BASE_URL", status=503)
    return f"{base}/director-inspiration/{project_id}/workspace"


def _cached(key):
    with _cache_lock:
        value = _cache.get(key)
        if value and value[0] > time.monotonic():
            return copy.deepcopy(value[1])
        _cache.pop(key, None)
    return None


def _put(key, value, seconds):
    with _cache_lock:
        _cache[key] = (time.monotonic() + max(1, seconds), copy.deepcopy(value))


class FeishuClient:
    def __init__(self, settings, tenant_id: str, transport=None):
        self.settings = settings
        self.tenant_id = tenant_id
        self.transport = transport
        # Credential rotation invalidates all cached tokens and directory entries.
        self.key = (tenant_id, settings.feishu_app_id, hashlib.sha256(settings.feishu_app_secret.encode()).hexdigest())

    def _request(self, method, path, *, params=None, payload=None, token=None, sending=False):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with httpx.Client(timeout=httpx.Timeout(15, connect=5), transport=self.transport) as client:
                response = client.request(method, API_BASE + path, params=params, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise FeishuError("无法连接飞书，请稍后重试") from exc
        except httpx.RequestError as exc:
            raise FeishuError("飞书请求中断，请核对是否收到消息" if sending else "飞书查询超时，请重试", uncertain=sending) from exc
        if response.status_code >= 500 or response.status_code == 408:
            raise FeishuError("飞书服务暂时不可用", uncertain=sending)
        if response.status_code == 429:
            raise FeishuError("飞书请求过于频繁，请稍后重试")
        if not response.is_success:
            raise FeishuError(f"飞书请求被拒绝（HTTP {response.status_code}），请检查应用权限")
        try:
            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get("code"), int):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise FeishuError("飞书返回内容异常，请核对发送结果" if sending else "飞书返回内容异常", uncertain=sending) from exc
        return result

    @staticmethod
    def _check(result):
        code = result["code"]
        if code != 0:
            # Never expose the remote response, which may include credentials/request data.
            raise FeishuError(f"飞书请求失败（错误码 {code}），请检查应用权限、授权范围及机器人是否已入群")

    def token(self, refresh=False):
        key = (*self.key, "token")
        if not refresh:
            value = _cached(key)
            if value:
                return value
        result = self._request("POST", "/auth/v3/tenant_access_token/internal", payload={
            "app_id": self.settings.feishu_app_id, "app_secret": self.settings.feishu_app_secret,
        })
        self._check(result)
        token = result.get("tenant_access_token")
        expire = result.get("expire")
        if not isinstance(token, str) or not token or not isinstance(expire, (int, float)) or expire <= 0:
            raise FeishuError("飞书未返回有效的应用令牌")
        _put(key, token, max(1, expire - 60))
        return token

    def call(self, method, path, *, params=None, payload=None, sending=False):
        token = self.token()
        result = self._request(method, path, params=params, payload=payload, token=token, sending=sending)
        if result["code"] in {99991661, 99991663, 99991668}:
            token = self.token(refresh=True)
            result = self._request(method, path, params=params, payload=payload, token=token, sending=sending)
        self._check(result)
        data = result.get("data")
        if not isinstance(data, dict):
            raise FeishuError("飞书返回数据不完整", uncertain=sending)
        return data

    def pages(self, path, params=None):
        query = {"page_size": 50, **(params or {})}
        seen = set()
        for _ in range(1000):
            data = self.call("GET", path, params=query)
            yield data
            if not data.get("has_more"):
                return
            token = data.get("page_token")
            if not isinstance(token, str) or not token or token in seen:
                raise FeishuError("飞书分页异常，名单尚未加载完整，请刷新")
            seen.add(token)
            query["page_token"] = token
        raise FeishuError("飞书目录过大，名单尚未加载完整，请缩小应用授权范围")

    def directory(self, kind: str, refresh=False):
        key = (*self.key, "directory", kind)
        cached = None if refresh else _cached(key)
        if cached is not None:
            return cached
        if kind == "chat":
            items = {}
            for page in self.pages("/im/v1/chats"):
                for chat in page.get("items", []):
                    chat_id = chat.get("chat_id", "")
                    if chat_id.startswith("oc_") and chat.get("external") is False:
                        items[chat_id] = {"id": chat_id, "name": chat.get("name") or chat_id, "department_name": ""}
        else:
            items = self._users()
        result = {"configured": True, "type": kind, "items": sorted(items.values(), key=lambda item: (item["name"], item["id"])),
                  "refreshed_at": datetime.utcnow().isoformat() + "Z"}
        _put(key, result, 300)
        return result

    def _users(self):
        params = {"user_id_type": "open_id", "department_id_type": "open_department_id"}
        departments, singles = set(), set()
        for page in self.pages("/contact/v3/scopes", params):
            if page.get("group_ids"):
                raise FeishuError("当前版本支持部门和指定员工授权；检测到用户组授权，请管理员调整范围后刷新", status=409)
            departments.update(page.get("department_ids", []))
            singles.update(page.get("user_ids", []))
        names = {}
        for department in list(departments):
            for page in self.pages(f"/contact/v3/departments/{quote(department, safe='')}/children", {**params, "fetch_child": "true"}):
                for child in page.get("items", []):
                    child_id = child.get("open_department_id")
                    if child_id:
                        departments.add(child_id)
                        names[child_id] = child.get("name", "")
        users = {}

        def add(user, department=""):
            user_id = user.get("open_id", "")
            state = user.get("status") or {}
            if not user_id.startswith("ou_") or state.get("is_resigned") or state.get("is_frozen") or state.get("is_exited"):
                return
            if not user.get("name"):
                raise FeishuError("无法读取员工姓名，请开通用户基本信息权限后刷新")
            if user_id not in users:
                users[user_id] = {"id": user_id, "name": user["name"], "department_name": names.get(department, ""), "department_id": department}

        for department in sorted(departments):
            for page in self.pages("/contact/v3/users/find_by_department", {**params, "department_id": department}):
                for user in page.get("items", []):
                    add(user, department)
        for user_id in sorted(singles - users.keys()):
            data = self.call("GET", f"/contact/v3/users/{quote(user_id, safe='')}", params=params)
            if not isinstance(data.get("user"), dict):
                raise FeishuError("飞书返回员工信息不完整")
            add(data["user"])
        return users

    def recipient(self, kind, recipient_id):
        items = self.directory(kind)["items"]
        for item in items:
            if item["id"] == recipient_id:
                return item
        raise FeishuError("接收对象不在当前可选范围，请刷新名单后重试", status=422)

    def send(self, kind, recipient_id, text, notification_uuid):
        data = self.call("POST", "/im/v1/messages", params={"receive_id_type": "open_id" if kind == "user" else "chat_id"}, payload={
            "receive_id": recipient_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False), "uuid": notification_uuid,
        }, sending=True)
        if not isinstance(data.get("message_id"), str) or not data["message_id"]:
            raise FeishuError("飞书未返回消息编号，请核对是否已收到通知", uncertain=True)
        return data["message_id"]
