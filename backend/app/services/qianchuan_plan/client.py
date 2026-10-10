from __future__ import annotations

import json
from typing import Any

from app.services.qianchuan.client import PlatformError, QianchuanClient, identifier, items


AWEME_PATH = "/open_api/v1.0/qianchuan/aweme/authorized/get/"
PRODUCT_PATH = "/open_api/v1.0/qianchuan/product/available/get/"
MAX_AWEME_SEARCH_PAGES = 100


def _text(value: object, field: str, *, maximum: int = 1024) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise PlatformError("invalid_response", f"平台返回的{field}格式异常")
    return value


def _optional_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise PlatformError("invalid_response", f"平台返回的{field}格式异常")
    return value


def _non_negative_int(value: object, field: str, *, required: bool = True) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PlatformError("invalid_pagination", f"平台返回的{field}格式异常")
    return value


def _string_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PlatformError("invalid_response", f"平台返回的{field}格式异常")
    return [_text(item, field, maximum=64) for item in value]


class QianchuanPlanClient:
    """Read/write plan-domain adapter kept separate from the legacy auth/upload client."""

    def __init__(self, transport_client: QianchuanClient):
        self.transport = transport_client

    def _aweme_page(self, token: str, advertiser_id: str, page: int, page_size: int) -> dict[str, Any]:
        data = self.transport.request("GET", AWEME_PATH, token=token, params={
            "advertiser_id": identifier(advertiser_id),
            "page": page,
            "page_size": page_size,
        })
        raw_page = data.get("page_info")
        if not isinstance(raw_page, dict):
            raise PlatformError("invalid_pagination", "平台抖音号分页字段缺失")
        page_info = {
            "page": _non_negative_int(raw_page.get("page"), "页码"),
            "page_size": _non_negative_int(raw_page.get("page_size"), "分页大小"),
            "total_number": _non_negative_int(raw_page.get("total_number"), "总条数"),
            "total_page": _non_negative_int(raw_page.get("total_page"), "总页数"),
        }
        if page_info["page"] != page or page_info["page_size"] != page_size:
            raise PlatformError("invalid_pagination", "平台抖音号分页与请求不一致")
        normalized = []
        for raw in items(data, "aweme_id_list"):
            if not isinstance(raw, dict):
                raise PlatformError("invalid_response", "平台抖音号列表格式异常")
            normalized.append({
                "id": identifier(raw.get("aweme_id")),
                "name": _text(raw.get("aweme_name"), "抖音号名称", maximum=256),
                "show_id": _text(raw.get("aweme_show_id"), "展示抖音号", maximum=128),
                "avatar_url": _text(raw.get("aweme_avatar"), "抖音头像", maximum=2048),
                "status": _text(raw.get("aweme_status"), "抖音号状态", maximum=64),
                "has_live_permission": _optional_bool(raw.get("aweme_has_live_permission"), "直播权限"),
                "has_video_permission": _optional_bool(raw.get("aweme_has_video_permission"), "短视频权限"),
                "has_publish_permission": _optional_bool(raw.get("aweme_has_publish_permission"), "发布权限"),
                "bind_types": _string_list(raw.get("bind_type"), "授权类型"),
            })
        if len(normalized) > page_size:
            raise PlatformError("invalid_pagination", "平台抖音号单页数量超过请求上限")
        return {"items": normalized, **page_info}

    def authorized_awemes(
        self, token: str, advertiser_id: str, *, page: int, page_size: int, keyword: str = ""
    ) -> dict[str, Any]:
        if not 1 <= page <= 100000 or not 1 <= page_size <= 100:
            raise ValueError("invalid local pagination")
        keyword = keyword.strip().casefold()
        if not keyword:
            return self._aweme_page(token, advertiser_id, page, page_size)

        # The official endpoint has no name filter. Search the bounded complete set first,
        # then paginate locally so results are not silently limited to one provider page.
        all_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        provider_page_size = 100
        expected_total: int | None = None
        expected_pages: int | None = None
        for provider_page in range(1, MAX_AWEME_SEARCH_PAGES + 1):
            result = self._aweme_page(token, advertiser_id, provider_page, provider_page_size)
            if expected_total is None:
                expected_total = result["total_number"]
                expected_pages = result["total_page"]
            elif (result["total_number"] != expected_total
                  or result["total_page"] != expected_pages):
                raise PlatformError("invalid_pagination", "平台抖音号分页在查询中发生变化")
            for item in result["items"]:
                if item["id"] in seen_ids:
                    raise PlatformError("invalid_pagination", "平台重复返回抖音号，搜索已停止")
                seen_ids.add(item["id"])
                all_items.append(item)
            if provider_page >= result["total_page"]:
                break
        else:
            raise PlatformError("invalid_pagination", "授权抖音号分页超过安全上限")
        if len(all_items) != expected_total:
            raise PlatformError("invalid_pagination", "平台抖音号分页总数与结果不一致")

        matched = [item for item in all_items if keyword in item["name"].casefold()
                   or keyword in item["show_id"].casefold()]
        start = (page - 1) * page_size
        total = len(matched)
        return {
            "items": matched[start:start + page_size],
            "page": page,
            "page_size": page_size,
            "total_number": total,
            "total_page": (total + page_size - 1) // page_size,
        }

    def authorized_aweme(self, token: str, advertiser_id: str, aweme_id: str) -> dict[str, Any] | None:
        target = identifier(aweme_id)
        seen_ids: set[str] = set()
        expected_total: int | None = None
        for page in range(1, MAX_AWEME_SEARCH_PAGES + 1):
            result = self._aweme_page(token, advertiser_id, page, 100)
            if expected_total is None:
                expected_total = result["total_number"]
            elif result["total_number"] != expected_total:
                raise PlatformError("invalid_pagination", "平台抖音号分页在校验中发生变化")
            for item in result["items"]:
                if item["id"] in seen_ids:
                    raise PlatformError("invalid_pagination", "平台重复返回抖音号，校验已停止")
                seen_ids.add(item["id"])
                if item["id"] == target:
                    return item
            if page >= result["total_page"]:
                return None
        raise PlatformError("invalid_pagination", "授权抖音号分页超过安全上限")

    def available_products(
        self, token: str, advertiser_id: str, *, aweme_id: str | None = None,
        keyword: str = "", product_id: str | None = None,
        cursor: int | None = None, page_size: int = 20,
    ) -> dict[str, Any]:
        if (cursor is not None and cursor < 0) or not 1 <= page_size <= 100:
            raise ValueError("invalid local pagination")
        params: dict[str, object] = {
            "advertiser_id": identifier(advertiser_id),
            "page_size": page_size,
            "campaign_scene": "VIDEO_PROM_GOODS",
        }
        if aweme_id:
            params["aweme_id"] = identifier(aweme_id)
        product_filter: dict[str, object] = {}
        if keyword.strip():
            product_filter["product_name"] = keyword.strip()
        if product_id:
            product_filter["product_ids"] = [int(identifier(product_id))]
        if product_filter:
            params["filter"] = json.dumps(product_filter, ensure_ascii=False, separators=(",", ":"))
        if cursor is None:
            params["page"] = 1
        else:
            params["cursor"] = cursor

        data = self.transport.request("GET", PRODUCT_PATH, token=token, params=params)
        raw_page = data.get("page_info")
        if not isinstance(raw_page, dict):
            raise PlatformError("invalid_pagination", "平台商品分页字段缺失")
        has_more = raw_page.get("has_more")
        if has_more is not None and not isinstance(has_more, bool):
            raise PlatformError("invalid_pagination", "平台商品分页标记格式异常")
        next_cursor = _non_negative_int(raw_page.get("cursor"), "商品游标", required=False)
        normalized = []
        for raw in items(data, "product_list"):
            if not isinstance(raw, dict):
                raise PlatformError("invalid_response", "平台商品列表格式异常")
            normalized.append({
                "id": identifier(raw.get("id")),
                "name": _text(raw.get("name"), "商品名称", maximum=512),
                "image_url": _text(raw.get("img"), "商品图片", maximum=2048),
                "category_name": _text(raw.get("category_name"), "商品分类", maximum=256),
                "channel_type": _text(raw.get("channel_type"), "商品渠道", maximum=64),
            })
        if len(normalized) > page_size:
            raise PlatformError("invalid_pagination", "平台商品单页数量超过请求上限")
        return {
            "items": normalized,
            "cursor": next_cursor,
            "has_more": bool(has_more),
            "page_size": page_size,
        }

    def available_product(
        self, token: str, advertiser_id: str, aweme_id: str, product_id: str,
    ) -> dict[str, Any] | None:
        target = identifier(product_id)
        cursor: int | None = None
        seen_cursors: set[int] = set()
        seen_ids: set[str] = set()
        for _ in range(100):
            result = self.available_products(
                token, advertiser_id, aweme_id=aweme_id, product_id=target,
                cursor=cursor, page_size=100)
            for item in result["items"]:
                if item["id"] in seen_ids:
                    raise PlatformError("invalid_pagination", "平台重复返回商品，校验已停止")
                seen_ids.add(item["id"])
                if item["id"] == target:
                    return item
            if not result["has_more"]:
                return None
            next_cursor = result["cursor"]
            if next_cursor is None or next_cursor in seen_cursors:
                raise PlatformError("invalid_pagination", "平台商品游标未推进，校验已停止")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise PlatformError("invalid_pagination", "可投商品分页超过安全上限")
