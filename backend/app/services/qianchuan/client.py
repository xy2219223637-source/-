from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from .logging import response_preview, log_context, event_fields, API_CALLS

API_BASE = "https://api.oceanengine.com"
logger = logging.getLogger(__name__)
API_NAMES = {
    "/open_api/2/file/video/ad/": "上传视频素材",
    "/open_api/v1.0/qianchuan/video/get/": "查询视频素材",
    "/open_api/v1.0/qianchuan/report/uni_promotion/config/get/": "查询报表配置",
    "/open_api/v1.0/qianchuan/report/uni_promotion/data/get/": "查询报表数据",
    "/open_api/v1.0/qianchuan/aweme/authorized/get/": "查询授权抖音号",
    "/open_api/v1.0/qianchuan/product/available/get/": "查询可投商品",
    "/open_api/v1.0/qianchuan/uni_promotion/list/": "查询全域计划",
    "/open_api/v1.0/qianchuan/uni_promotion/ad/detail/": "查询全域计划详情",
    "/open_api/v1.0/qianchuan/uni_promotion/ad/material/get/": "查询全域计划素材",
    "/open_api/v1.0/qianchuan/material/ad/get/": "查询素材关联计划",
    "/open_api/oauth2/access_token/": "获取授权凭证",
    "/open_api/oauth2/refresh_token/": "刷新授权凭证",
}
MATERIAL_RESPONSE_PATHS = {
    "/open_api/2/file/video/ad/",
    "/open_api/v1.0/qianchuan/video/get/",
    "/open_api/v1.0/qianchuan/report/uni_promotion/config/get/",
    "/open_api/v1.0/qianchuan/report/uni_promotion/data/get/",
}
SHOP_ROLE = "PLATFORM_ROLE_SHOP_ACCOUNT"
AGENT_ROLE = "PLATFORM_ROLE_QIANCHUAN_AGENT"
WORKSPACE_ROLES = {"CUSTOMER_ADMIN", "CUSTOMER_OPERATOR"}


class PlatformError(Exception):
    """Application-owned errors; sanitized provider responses stay in diagnostic logs."""
    def __init__(self, code: str, message: str, request_id: str = ""):
        super().__init__(message)
        self.code, self.message, self.request_id = code, message, request_id


def identifier(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise PlatformError("invalid_response", "平台账户 ID 格式不符合接口契约")
    result = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,19}", result):
        raise PlatformError("invalid_response", "平台账户 ID 格式不符合接口契约")
    return result


def items(data: dict, field: str) -> list:
    result = data.get(field)
    if not isinstance(result, list):
        raise PlatformError("invalid_response", "平台列表字段缺失，请核对接口契约")
    return result


class QianchuanClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.http: httpx.Client | None = None
        self._client_lock = threading.Lock()

    def close(self) -> None:
        if self.http:
            self.http.close()

    def request(self, method: str, path: str, *, token: str = "", params=None, body=None, form=None, files=None) -> dict:
        with log_context():
            return self._logged_request(method, path, token=token, params=params, body=body, form=form, files=files)

    def _logged_request(self, method: str, path: str, *, token, params, body, form, files) -> dict:
        started = time.monotonic()
        trace = {"http_status": "-", "provider_code": "-", "request_id": "-"}
        outcome = "success"
        error = "-"
        try:
            return self._request(method, path, token=token, params=params, body=body,
                                 form=form, files=files, trace=trace)
        except PlatformError as exc:
            outcome, error = "failed", exc.code
            raise
        except Exception:
            outcome, error = "failed", "unexpected_error"
            raise
        finally:
            # Paths are internal constants. Never include queries, headers, bodies,
            # platform messages or exception text (which may contain credentials).
            safe_path = urlsplit(path).path
            safe_path = safe_path if re.fullmatch(r"/[A-Za-z0-9_./-]+", safe_path) else "[invalid_path]"
            fallback = error == "report_overall_unavailable"
            elapsed = (time.monotonic() - started) * 1000
            fields = {
                "接口名称": API_NAMES.get(safe_path, "千川账户接口"), "接口路径": safe_path,
                "请求方法": method if method in {"GET", "POST", "DELETE", "PUT", "PATCH"} else "OTHER",
                "HTTP 状态": trace["http_status"], "平台错误码": trace["provider_code"],
                "结果说明": "账户暂无乘方能力，将尝试全域主题" if fallback else "成功" if outcome == "success" else "请结合平台响应说明排查",
                "内部错误分类": error, "平台请求编号": trace["request_id"],
                "请求耗时": f"{elapsed:.1f} 毫秒"}
            details = {"平台原始回参（已脱敏）": trace["response"]} if "response" in trace else None
            calls = API_CALLS.get()
            if calls is not None:
                calls.append({**fields, **(details or {})})
            else:
                logger.log(logging.INFO if outcome == "success" or fallback else logging.WARNING,
                    "qianchuan api_request method=%s path=%s outcome=%s http_status=%s "
                    "provider_code=%s error=%s request_id=%s duration_ms=%.1f%s",
                    fields["请求方法"],
                    safe_path, outcome, trace["http_status"], trace["provider_code"], error,
                    trace["request_id"], elapsed,
                    " response=" + json.dumps(trace["response"], ensure_ascii=False) if details is not None else "",
                    extra=event_fields("千川接口｜" + ("调用成功" if outcome == "success" else "默认主题不适用" if fallback else "调用失败"), fields, details))

    def _request(self, method: str, path: str, *, token: str, params, body, form, files, trace: dict) -> dict:
        headers = {"Access-Token": token} if token else {}
        try:
            with self._client_lock:
                if self.http is None:
                    self.http = httpx.Client(base_url=API_BASE, timeout=httpx.Timeout(10, connect=5),
                                             follow_redirects=False, transport=self.transport)
            response = self.http.request(method, path, params=params, json=body, data=form, files=files, headers=headers)
        except httpx.HTTPError:
            raise PlatformError("transport_uncertain", "平台请求结果不确定，请重新授权或稍后重新同步") from None
        trace["http_status"] = response.status_code
        if self.settings.qianchuan_log_material_response and path in MATERIAL_RESPONSE_PATHS:
            try:
                preview = response_preview(response.json(), [token, self.settings.qianchuan_app_secret,
                    self.settings.qianchuan_token_encryption_key], truncate=False)
            except (ValueError, TypeError, RecursionError):
                preview = '{"omitted": "response is not valid JSON", "说明": "响应不是有效 JSON，正文已省略"}'
            trace["response"] = json.loads(preview)
        if response.status_code != 200:
            raise PlatformError("http_error", "平台 HTTP 请求失败，请检查授权与接口可用性")
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            code = payload["code"]
            if isinstance(code, bool) or not isinstance(code, int):
                raise ValueError
            trace["provider_code"] = code
            request_id = str(payload.get("request_id", ""))
            secrets = [token, self.settings.qianchuan_app_secret,
                       (params or {}).get("access_token", ""), (body or {}).get("auth_code", ""),
                       (body or {}).get("refresh_token", "")]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) or any(s and s in request_id for s in secrets):
                request_id = ""
            trace["request_id"] = request_id or "-"
            if code != 0:
                if (code == 40000
                        and path == "/open_api/v1.0/qianchuan/report/uni_promotion/config/get/"
                        and payload.get("message") == "当前账户暂无乘方投放能力，无法获取对应数据"):
                    raise PlatformError("report_overall_unavailable", "当前账户未开通乘方投放，需使用全域素材报表", request_id)
                if code == 40002 and str(payload.get("message", "")).startswith("No permission to operate account "):
                    raise PlatformError("account_forbidden", "当前授权无权操作所选账户，请选择店铺账户或重新授权", request_id)
                raise PlatformError("provider_rejected", f"平台拒绝请求（错误码 {code}），需核查接口参数、授权及权限",
                                    request_id)
            data = payload["data"]
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, KeyError, TypeError):
            raise PlatformError("invalid_response", "平台响应格式不符合接口契约") from None

    def exchange(self, auth_code: str) -> dict:
        return self.request("POST", "/open_api/oauth2/access_token/", body={
            "app_id": int(self.settings.qianchuan_app_id),
            "secret": self.settings.qianchuan_app_secret, "auth_code": auth_code,
        })

    def refresh(self, refresh_token: str) -> dict:
        return self.request("POST", "/open_api/oauth2/refresh_token/", body={
            "app_id": int(self.settings.qianchuan_app_id),
            "secret": self.settings.qianchuan_app_secret, "refresh_token": refresh_token,
        })

    def upload_video(self, token: str, advertiser_id: str, file, filename: str, signature: str, is_aigc: bool) -> dict:
        class TimedFile:
            def __init__(self, source):
                self.source, self.deadline = source, time.monotonic() + 60

            def read(self, size=-1):
                if time.monotonic() > self.deadline:
                    raise httpx.WriteTimeout("upload deadline")
                return self.source.read(size)

            def seek(self, *args):
                return self.source.seek(*args)

            def tell(self):
                return self.source.tell()

        return self.request("POST", "/open_api/2/file/video/ad/", token=token, form={
            "advertiser_id": identifier(advertiser_id), "upload_type": "UPLOAD_BY_FILE",
            "filename": filename, "video_signature": signature, "is_aigc": str(is_aigc).lower(),
        }, files={"video_file": (filename, TimedFile(file), "video/mp4")})

    def find_video(self, token: str, advertiser_id: str, signature: str, material_id: str,
                   video_id: str, check: Callable[[], None]) -> dict | None:
        filtering = ({"material_ids": [int(identifier(material_id))]} if material_id else
                     {"video_ids": [video_id]} if video_id else {"signatures": [signature]})
        rows = self._pages("/open_api/v1.0/qianchuan/video/get/", token,
            {"advertiser_id": identifier(advertiser_id), "filtering": json.dumps(filtering)},
            lambda data: items(data, "list"), check)
        for row in rows:
            if not isinstance(row, dict):
                raise PlatformError("invalid_response", "平台素材响应格式异常")
            if ((material_id and str(row.get("material_id", "")) == material_id)
                    or (video_id and row.get("id") == video_id)
                    or (not material_id and not video_id and row.get("signature") == signature)):
                return row
        return None

    def report_config(self, token: str, advertiser_id: str, data_topics: list[str]) -> dict:
        return self.request("GET", "/open_api/v1.0/qianchuan/report/uni_promotion/config/get/",
                            token=token, params={"advertiser_id": identifier(advertiser_id),
                            "data_topics": json.dumps(data_topics, separators=(",", ":"))})

    def report_data(self, token: str, advertiser_id: str, *, data_topic: str,
                    dimensions: list[str], metrics: list[str], filters: list[dict],
                    start_time: str, end_time: str) -> dict:
        params = {
            "advertiser_id": identifier(advertiser_id), "data_topic": data_topic,
            "dimensions": json.dumps(dimensions, separators=(",", ":")),
            "metrics": json.dumps(metrics, separators=(",", ":")),
            "filters": json.dumps(filters, separators=(",", ":")),
            "start_time": start_time, "end_time": end_time, "order_by": "[]",
            "page": 1, "page_size": 10,
        }
        return self.request("GET", "/open_api/v1.0/qianchuan/report/uni_promotion/data/get/",
                            token=token, params=params)

    def _pages(self, path: str, token: str, params: dict, read_rows: Callable[[dict], list],
               check: Callable[[], None]) -> list:
        rows, seen = [], set()
        for page in range(1, 101):
            check()
            data = self.request("GET", path, token=token, params={**params, "page": page, "page_size": 100})
            batch = read_rows(data)
            info = data.get("page_info")
            if not isinstance(info, dict):
                raise PlatformError("invalid_pagination", "平台分页字段缺失，未更新账户范围")
            total = info.get("total_page")
            current = info.get("page")
            if type(total) is not int or type(current) is not int or current != page or total < 0 or total > 100:
                raise PlatformError("invalid_pagination", "平台分页异常，未更新账户范围")
            signature = json.dumps(batch, sort_keys=True)
            if batch and signature in seen:
                raise PlatformError("invalid_pagination", "平台重复返回同一页，未更新账户范围")
            seen.add(signature)
            rows.extend(batch)
            if total == 0 and batch:
                raise PlatformError("invalid_pagination", "平台分页总数与结果不一致")
            if page >= total:
                return rows
            if not batch:
                raise PlatformError("invalid_pagination", "平台分页中断，未更新账户范围")
        raise PlatformError("invalid_pagination", "账户分页超过首版处理上限，请缩小授权范围")

    @staticmethod
    def _shop_rows(data: dict) -> list:
        if data.get("adv_id_list") or (isinstance(data.get("adv_id_list"), list) and "list" not in data):
            rows = data["adv_id_list"]
            if any(not isinstance(row, dict) for row in rows):
                raise PlatformError("invalid_response", "店铺账户格式异常")
            return [{"id": identifier(row.get("adv_id")), "name": str(row.get("adv_name", ""))[:256]}
                    for row in rows]
        return [{"id": identifier(value), "name": ""} for value in items(data, "list")]

    def _type(self, token: str, advertiser_id: str, check: Callable[[], None]) -> str:
        check()
        data = self.request("GET", "/open_api/v1.0/qianchuan/advertiser/type/get/",
                            token=token, params={"advertiser_ids": json.dumps([int(identifier(advertiser_id))])})
        matches = [row for row in items(data, "list") if isinstance(row, dict)
                   and identifier(row.get("advertiser_id")) == advertiser_id]
        if len(matches) != 1 or matches[0].get("ecp_type") not in {"SHOP", "SHOP_STAR", "COMMON_STAR"}:
            raise PlatformError("unverified_account", "无法确认目标千川投放账户类型")
        return matches[0]["ecp_type"]

    def resolve_accounts(self, token: str, check: Callable[[], None] = lambda: None) -> tuple[list, list, list]:
        check()
        data = self.request("GET", "/open_api/oauth2/advertiser/get/", params={"access_token": token})
        bindings, snapshot, warnings = [], [], []
        for source in items(data, "list"):
            if not isinstance(source, dict):
                raise PlatformError("invalid_response", "授权对象格式异常")
            role = source.get("account_type")
            # False, missing, strings and numbers must never authorize a binding.
            if source.get("is_valid") is not True:
                warnings.append("存在失效或未确认有效性的授权对象，已跳过")
                continue
            if role not in {SHOP_ROLE, AGENT_ROLE, "ADVERTISER"} | WORKSPACE_ROLES:
                warnings.append("存在尚未支持的授权对象类型，已跳过")
                continue
            source_id = identifier(source.get("account_id"))
            name = str(source.get("account_name", ""))[:256]
            company_ids = []
            if role == SHOP_ROLE:
                candidates = self._pages("/open_api/v1.0/qianchuan/shop/advertiser/list/", token,
                                         {"shop_id": source_id}, self._shop_rows, check)
            elif role == AGENT_ROLE:
                companies = items(source, "company_list")
                if any(not isinstance(company, dict) for company in companies):
                    raise PlatformError("invalid_response", "代理商授权公司范围格式异常")
                company_ids = [identifier(company.get("customer_company_id")) for company in companies]
                params = {"advertiser_id": source_id}
                if company_ids:
                    # SDK declares a repeated query parameter, not JSON or a comma-separated string.
                    params["company_ids"] = company_ids
                def agent_rows(page_data):
                    field = "advertiser_ids" if isinstance(page_data.get("advertiser_ids"), list) else "list"
                    return [{"id": identifier(value), "name": ""} for value in items(page_data, field)]
                candidates = self._pages("/open_api/2/agent/advertiser/select/", token, params, agent_rows, check)
            elif role in WORKSPACE_ROLES:
                def workspace_rows(page_data):
                    result = []
                    for row in items(page_data, "accounts"):
                        if not isinstance(row, dict) or row.get("account_type") != "QIANCHUAN":
                            raise PlatformError("unverified_account", "工作台返回非千川或类型不明的账户，未更新账户范围")
                        result.append({"id": identifier(row.get("account_id")),
                                       "name": str(row.get("account_name", ""))[:256]})
                    return result
                candidates = self._pages("/open_api/v3.0/customer_center/account/list/", token,
                    {"account_id": source_id, "filter": json.dumps({"account_type": "QIANCHUAN"})},
                    workspace_rows, check)
                if not candidates:
                    warnings.append("平台未返回此工作台下可访问的千川账户，请核对工作台选择、账户绑定及授权范围")
            else:
                candidates = [{"id": source_id, "name": name}]
            snapshot.append({"account_id": source_id, "account_name": name, "account_type": role,
                             "company_ids": company_ids})
            for candidate in candidates:
                advertiser_type = "SHOP_LINKED" if role == SHOP_ROLE else self._type(token, candidate["id"], check)
                bindings.append({"source_account_id": source_id, "source_account_name": name,
                                 "source_account_type": role, "source_company_ids": company_ids,
                                 "advertiser_id": candidate["id"], "advertiser_name": candidate["name"],
                                 "advertiser_type": advertiser_type})
        # The same Qianchuan advertiser can be reachable through several OAuth
        # subjects (for example a workspace/PAMIS account and its shop).  It is
        # still one upload target, so expose it only once.  Prefer the most
        # direct subject and keep richer names when priorities are equal.
        priority = {"ADVERTISER": 4, SHOP_ROLE: 3, AGENT_ROLE: 2}
        unique: dict[str, dict] = {}
        for binding in bindings:
            advertiser_id = binding["advertiser_id"]
            current = unique.get(advertiser_id)
            candidate_rank = (priority.get(binding["source_account_type"], 1),
                              bool(binding["advertiser_name"]), bool(binding["source_account_name"]))
            current_rank = ((priority.get(current["source_account_type"], 1),
                             bool(current["advertiser_name"]), bool(current["source_account_name"]))) \
                if current else (-1, False, False)
            if current is None or candidate_rank > current_rank:
                unique[advertiser_id] = binding
        return list(unique.values()), snapshot, sorted(set(warnings))
