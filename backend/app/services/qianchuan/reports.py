from __future__ import annotations

import math
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.models import QianchuanUpload as Upload
from .client import PlatformError, identifier
from .uploads import UploadService
from .logging import log_context, collect_api_calls, event_fields, response_preview, QUERY_ID
import json

TOPIC = "OVERALL_ROI_PRODUCT_MATERIAL"
UNI_TOPIC = "SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO"
logger = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("Asia/Shanghai")
METRICS = (
    "stat_cost_for_roi2",
    "product_show_count_for_roi2",
    "product_click_count_for_roi2",
    "product_cvr_rate_for_roi2",
    "total_pay_order_count_for_roi2",
    "total_pay_order_gmv_include_coupon_for_roi2",
    "total_pay_order_gmv_for_roi2",
    "total_cost_per_pay_order_for_roi2",
    "product_cpc_for_roi2",
    "total_prepay_and_pay_settle_roi2_1h",
    "video_play_count_for_roi2_v2",
    "video_play_finish_rate_for_roi2_v2",
    "video_like_count_for_roi2",
    "video_comment_count_for_roi2_v2",
    "video_play_duration_2s_rate_for_roi2",
    "video_play_duration_3s_rate_for_roi2",
    "video_play_duration_5s_rate_for_roi2",
    "video_play_duration_10s_rate_for_roi2",
)


def _scalar(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PlatformError("invalid_response", "平台报表指标格式异常")
    if isinstance(value, float) and not math.isfinite(value):
        raise PlatformError("invalid_response", "平台报表指标格式异常")
    return str(value)


class ReportService:
    def __init__(self, auth_service):
        self.auth = auth_service
        self.client = auth_service.client
        self.uploads = UploadService(auth_service)
        self.cache = auth_service.report_cache

    def get(self, db, auth, upload_id: str, days: int) -> dict:
        with log_context(), collect_api_calls() as calls:
            try:
                return self._get(db, auth, upload_id, days, calls)
            except HTTPException:
                raise
            except Exception:
                logger.error("qianchuan report_failed upload=%s code=unexpected_error", upload_id,
                    extra=event_fields("千川报表｜查询失败", {"上传任务": upload_id,
                        "错误分类": "unexpected_error"}, {"平台接口调用": calls}))
                raise

    def _get(self, db, auth, upload_id: str, days: int, calls: list) -> dict:
        self.auth.require_content_access(auth)
        row = self.uploads.owned_uploads(db, auth.tenant_id, auth.user_id).filter(Upload.id == upload_id).first()
        if not row:
            raise HTTPException(404, "未找到上传记录")
        self.uploads.asset(db, auth.tenant_id, row.video_asset_id)
        if row.status != "succeeded" or not row.material_id:
            raise HTTPException(409, "素材尚未上传并核验成功，暂不能查询投放数据")
        if not self.auth.settings.qianchuan_allow_account_material_aggregate:
            raise HTTPException(409, "当前千川报表是账号与素材的汇总，无法确认只包含本人投放；为避免显示他人数据，暂不提供此报表")

        try:
            _, version = self.uploads.access(row.id)
            key = ("report", row.tenant_id, row.created_by, row.binding_id, row.advertiser_id,
                   row.material_id, version, days, datetime.now(TIMEZONE).date())
            result, cache_status = self.cache.get(key, lambda: self._query(row, version, days))
            # Recheck even on cache hits or after waiting for another request.
            token, _ = self.uploads.access(row.id, version)
            result.update(upload_id=row.id, cache_status=cache_status)
            fields = {"上传任务": row.id, "账户编号": row.advertiser_id,
                "素材编号": row.material_id, "查询范围": f"近 {days} 天",
                "数据来源": {"hit": "读取缓存，本次未请求千川", "joined": "共用相同并发查询结果", "miss": "本次查询千川"}[cache_status],
                "原始查询编号": result.get("source_query_id", "—"),
                "查询结果": "暂无匹配数据" if result["status"] == "no_data" else "有数据",
                "报表类型": "全域商品视频素材" if result["data_topic"] == UNI_TOPIC else "乘方商品素材",
                "报表主题": result["data_topic"], "统计开始": result["start_time"], "统计结束": result["end_time"],
                "返回行数": 0 if result["status"] == "no_data" else 1,
                "上次成功查询": result["queried_at"], "缓存到期": result["cache_expires_at"]}
            details = {"平台接口调用": calls} if calls else None
            if self.auth.settings.qianchuan_log_material_response and result["status"] != "no_data":
                metrics = {field: {"指标名称": metric["label"], "指标值": metric["value"]}
                           for field, metric in result["metrics"].items()}
                details = {**(details or {}), "整理后的指标": json.loads(response_preview(metrics,
                    [token, self.auth.settings.qianchuan_app_secret, self.auth.settings.qianchuan_token_encryption_key],
                    truncate=False))}
            logger.info("qianchuan report_cache upload=%s days=%s status=%s", row.id, days, cache_status,
                        extra=event_fields("千川报表｜查询结果", fields, details))
            return result
        except PlatformError as exc:
            logger.warning("qianchuan report_failed upload=%s code=%s request_id=%s", row.id, exc.code, exc.request_id,
                extra=event_fields("千川报表｜查询失败", {"上传任务": row.id, "中文说明": exc.message,
                    "账户编号": row.advertiser_id, "素材编号": row.material_id, "查询范围": f"近 {days} 天",
                    "错误分类": exc.code, "平台请求编号": exc.request_id or "未返回"}, {"平台接口调用": calls}))
            status = 409 if exc.code in {"refresh_pending", "scope_changed", "owner_inactive"} else 502
            raise HTTPException(status, exc.message) from None

    def _configuration(self, row, version):
        token, _ = self.uploads.access(row.id, version)
        data_topic = TOPIC
        try:
            config = self.client.report_config(token, row.advertiser_id, [data_topic])
        except PlatformError as exc:
            if exc.code != "report_overall_unavailable":
                raise
            data_topic = UNI_TOPIC
            token, _ = self.uploads.access(row.id, version)
            config = self.client.report_config(token, row.advertiser_id, [data_topic])
        topics = config.get("custom_config_datas")
        if not isinstance(topics, list):
            raise PlatformError("invalid_response", "平台报表配置格式异常")
        matches = [item for item in topics if isinstance(item, dict) and item.get("data_topic") == data_topic]
        if len(matches) != 1:
            raise PlatformError("report_unavailable", "当前账户未开放商品视频素材报表")
        topic = matches[0]
        dimensions = topic.get("dimensions")
        metric_config = topic.get("metrics")
        if not isinstance(dimensions, list) or not isinstance(metric_config, list):
            raise PlatformError("invalid_response", "平台报表配置格式异常")
        dimension_fields = {item.get("field") for item in dimensions if isinstance(item, dict)}
        query_dimensions = ["material_id"]
        filters = [{"field": "material_id", "operator": 7, "values": [identifier(row.material_id)]}]
        if data_topic == UNI_TOPIC:
            query_dimensions.append("roi2_material_video_name")
        else:
            filters.append({"field": "roi2_material_type_v3", "operator": 7, "values": ["3"]})
        required_fields = set(query_dimensions) | {item["field"] for item in filters}
        if not required_fields.issubset(dimension_fields):
            raise PlatformError("report_unavailable", "当前素材报表配置缺少必要筛选字段")
        definitions = {item.get("field"): item for item in metric_config
                       if isinstance(item, dict) and isinstance(item.get("field"), str)}
        selected = [field for field in METRICS if field in definitions]
        if not selected:
            raise PlatformError("report_unavailable", "当前素材报表没有可展示的指标")

        self.uploads.access(row.id, version)
        return data_topic, query_dimensions, definitions, selected

    def _query(self, row, version, days):
        config_key = ("config", row.tenant_id, row.created_by, row.binding_id, row.advertiser_id, version)
        (data_topic, query_dimensions, definitions, selected), _ = self.cache.get(
            config_key, lambda: self._configuration(row, version))
        filters = [{"field": "material_id", "operator": 7, "values": [identifier(row.material_id)]}]
        if data_topic == TOPIC:
            filters.append({"field": "roi2_material_type_v3", "operator": 7, "values": ["3"]})
        now = datetime.now(TIMEZONE)
        start = datetime.combine(now.date() - timedelta(days=days - 1), time.min, TIMEZONE)
        start_time, end_time = start.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")
        token, _ = self.uploads.access(row.id, version)
        data = self.client.report_data(token, row.advertiser_id, data_topic=data_topic,
            dimensions=query_dimensions, metrics=selected, filters=filters,
            start_time=start_time, end_time=end_time)
        rows, page_info = data.get("rows"), data.get("page_info")
        if not isinstance(rows, list) or not isinstance(page_info, dict):
            raise PlatformError("invalid_response", "平台报表响应格式异常")
        total_page = page_info.get("total_page")
        if type(total_page) is not int or total_page < 0 or total_page > 1 or len(rows) > 1:
            raise PlatformError("invalid_response", "平台按素材汇总的分页结果异常")

        values: dict[str, object] = {}
        if rows:
            result = rows[0]
            if not isinstance(result, dict) or not isinstance(result.get("dimensions"), dict) \
                    or not isinstance(result.get("metrics"), dict):
                raise PlatformError("invalid_response", "平台报表数据行格式异常")
            if identifier(result["dimensions"].get("material_id")) != row.material_id:
                raise PlatformError("invalid_response", "平台返回了不匹配的素材报表")
            values = result["metrics"]

        normalized = {}
        for field in selected:
            name = definitions[field].get("name")
            normalized[field] = {
                "label": name if isinstance(name, str) and name else field,
                "value": _scalar(values.get(field)),
            }
        self.uploads.access(row.id, version)
        queried_at = datetime.now(TIMEZONE)
        return {
            "upload_id": row.id, "advertiser_id": row.advertiser_id,
            "source_query_id": QUERY_ID.get(),
            "material_id": row.material_id, "data_topic": data_topic,
            "start_time": start_time, "end_time": end_time,
            "queried_at": queried_at.isoformat(),
            "cache_expires_at": (queried_at + timedelta(seconds=self.cache.ttl)).isoformat(),
            "status": "available" if rows else "no_data", "metrics": normalized,
            "per_second": {"status": "unavailable",
                "message": "千川当前未向本应用开放逐秒互动与流失数据；固定秒数播放率不用于生成逐秒曲线。"},
        }
