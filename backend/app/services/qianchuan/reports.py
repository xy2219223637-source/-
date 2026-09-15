from __future__ import annotations

import math
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.models import QianchuanUpload as Upload
from .client import PlatformError, identifier
from .uploads import UploadService

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
        row = db.query(Upload).filter_by(id=upload_id, tenant_id=auth.tenant_id).first()
        if not row:
            raise HTTPException(404, "未找到上传记录")
        self.uploads.asset(db, auth.tenant_id, row.video_asset_id)
        if row.status != "succeeded" or not row.material_id:
            raise HTTPException(409, "素材尚未上传并核验成功，暂不能查询投放数据")

        try:
            _, version = self.uploads.access(row.id)
            key = ("report", row.tenant_id, row.binding_id, row.advertiser_id,
                   row.material_id, version, days, datetime.now(TIMEZONE).date())
            result, cache_status = self.cache.get(key, lambda: self._query(row, version, days))
            # Recheck even on cache hits or after waiting for another request.
            self.uploads.access(row.id, version)
            result.update(upload_id=row.id, cache_status=cache_status)
            logger.info("qianchuan report_cache upload=%s days=%s status=%s", row.id, days, cache_status)
            return result
        except PlatformError as exc:
            logger.warning("qianchuan report_failed upload=%s code=%s request_id=%s", row.id, exc.code, exc.request_id)
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
            logger.info("qianchuan report_fallback upload=%s request_id=%s", row.id, exc.request_id)
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
        config_key = ("config", row.tenant_id, row.binding_id, row.advertiser_id, version)
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
        logger.info("qianchuan report_finished upload=%s topic=%s days=%s status=%s rows=%s",
                    row.id, data_topic, days, "available" if rows else "no_data", len(rows))
        self.uploads.access(row.id, version)
        queried_at = datetime.now(TIMEZONE)
        return {
            "upload_id": row.id, "advertiser_id": row.advertiser_id,
            "material_id": row.material_id, "data_topic": data_topic,
            "start_time": start_time, "end_time": end_time,
            "queried_at": queried_at.isoformat(),
            "cache_expires_at": (queried_at + timedelta(seconds=self.cache.ttl)).isoformat(),
            "status": "available" if rows else "no_data", "metrics": normalized,
            "per_second": {"status": "unavailable",
                "message": "千川当前未向本应用开放逐秒互动与流失数据；固定秒数播放率不用于生成逐秒曲线。"},
        }
