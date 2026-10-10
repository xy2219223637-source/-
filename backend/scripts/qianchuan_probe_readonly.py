"""Read-only live probe for an explicitly selected Qianchuan connection.

Run from backend with .venv/Scripts/python.exe scripts/qianchuan_probe_readonly.py.
Never prints credentials, full advertiser IDs, raw report rows, or OAuth objects.
No token refresh, upload, database writes, or promotional mutations.
"""
from __future__ import annotations

import json
import sys
import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.database import SessionLocal
from app.models import QianchuanAccountBinding as Binding
from app.models import QianchuanConnection as Connection
from app.models import QianchuanUpload as Upload
from app.services.qianchuan.client import PlatformError, QianchuanClient, identifier
from app.services.qianchuan.logging import collect_api_calls
from app.services.qianchuan.reports import METRICS, TOPIC, UNI_TOPIC
from app.services.qianchuan.service import get_qianchuan_service


CAPABILITIES = {
    "verified", "permission_missing", "account_not_applicable",
    "no_sample_data", "contract_uncertain",
}


def mask(value: object) -> str:
    text = str(value)
    return "****" + text[-4:] if len(text) >= 4 else "****"


def opaque_id(value: str | None) -> str | None:
    """Validate a local UUID selector without ever echoing it."""
    if not value:
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("本地 ID 必须是 UUID") from None


def response_summary(data: object, list_field: str | None = None) -> dict:
    if not isinstance(data, dict):
        return {"response_keys": "invalid"}
    summary: dict = {"response_keys": sorted(data)}
    if list_field:
        rows = data.get(list_field)
        summary["row_count"] = len(rows) if isinstance(rows, list) else "invalid"
        summary["pagination_keys"] = sorted(data.get("page_info", {})) \
            if isinstance(data.get("page_info"), dict) else []
    return summary


def capability_conclusion(data: object, outcome: str, list_field: str | None = None,
                          *, applicable: bool = True) -> str:
    if not applicable:
        return "account_not_applicable"
    if outcome == "account_forbidden":
        return "permission_missing"
    if outcome != "success" or not isinstance(data, dict):
        return "contract_uncertain"
    if list_field:
        rows = data.get(list_field)
        if not isinstance(rows, list):
            return "contract_uncertain"
        return "verified" if rows else "no_sample_data"
    return "verified"


def emit_capability(name: str, path: str, data: object, result: dict,
                    list_field: str | None = None, *, request_fields: tuple[str, ...] = (),
                    applicable: bool = True) -> str:
    conclusion = capability_conclusion(data, str(result.get("outcome")), list_field,
                                       applicable=applicable)
    assert conclusion in CAPABILITIES
    print(json.dumps({
        "capability": name,
        "path": path,
        "request_fields": sorted(request_fields),
        **response_summary(data, list_field),
        "conclusion": conclusion,
    }, ensure_ascii=False))
    return conclusion


def probe(client: QianchuanClient, label: str, operation):
    with collect_api_calls() as calls:
        try:
            data = operation()
            outcome = "success"
        except PlatformError as exc:
            data = None
            outcome = exc.code
        except Exception as exc:
            data = None
            outcome = type(exc).__name__
    trace = calls[-1] if calls else {}
    result = {"test": label, "outcome": outcome, "http": trace.get("HTTP 状态", "-"),
              "code": trace.get("平台错误码", "-"), "request_id": trace.get("平台请求编号", "-")}
    print(json.dumps(result, ensure_ascii=False))
    return data, result


def probe_plan_capabilities(client: QianchuanClient, token: str, advertiser_id: str,
                            upload: Upload | None) -> None:
    """Probe only documented GET endpoints; never mutates platform or local state."""
    aweme_path = "/open_api/v1.0/qianchuan/aweme/authorized/get/"
    awemes, result = probe(client, "authorized_awemes", lambda: client.request(
        "GET", aweme_path, token=token,
        params={"advertiser_id": advertiser_id, "page": 1, "page_size": 100}))
    emit_capability("authorized_awemes", aweme_path, awemes, result, "aweme_id_list",
                    request_fields=("advertiser_id", "page", "page_size"))

    product_path = "/open_api/v1.0/qianchuan/product/available/get/"
    products, result = probe(client, "available_products", lambda: client.request(
        "GET", product_path, token=token,
        params={"advertiser_id": advertiser_id, "page": 1, "page_size": 100}))
    emit_capability("available_products", product_path, products, result, "product_list",
                    request_fields=("advertiser_id", "page", "page_size"))

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    start_date = (now - timedelta(days=29)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")
    start_time = start_date + " 00:00:00"
    end_time = end_date + " 23:59:59"
    list_path = "/open_api/v1.0/qianchuan/uni_promotion/list/"
    plan_rows: list[dict] = []
    for goal in ("VIDEO_PROM_GOODS", "LIVE_PROM_GOODS"):
        plans, result = probe(client, f"plan_list:{goal}", lambda goal=goal: client.request(
            "GET", list_path, token=token,
            params={"advertiser_id": advertiser_id, "start_time": start_time, "end_time": end_time,
                    "marketing_goal": goal,
                    "fields": json.dumps(["stat_cost"], separators=(",", ":")),
                    "page": 1, "page_size": 10}))
        emit_capability(f"plan_list:{goal}", list_path, plans, result, "ad_list",
                        request_fields=("advertiser_id", "start_time", "end_time",
                                        "marketing_goal", "fields", "page", "page_size"))
        rows = plans.get("ad_list") if isinstance(plans, dict) else None
        if isinstance(rows, list):
            plan_rows.extend(row for row in rows if isinstance(row, dict))

    first_info = next((row.get("ad_info") for row in plan_rows
                       if isinstance(row.get("ad_info"), dict)
                       and row["ad_info"].get("id") is not None), None)
    if not first_info:
        for name in ("plan_detail", "plan_materials", "plan_report"):
            print(json.dumps({"capability": name, "conclusion": "no_sample_data",
                              "reason": "no_plan_in_30d_window"}, ensure_ascii=False))
    else:
        ad_id = identifier(first_info["id"])
        detail_path = "/open_api/v1.0/qianchuan/uni_promotion/ad/detail/"
        detail, result = probe(client, "plan_detail", lambda: client.request(
            "GET", detail_path, token=token,
            params={"advertiser_id": advertiser_id, "ad_id": ad_id}))
        emit_capability("plan_detail", detail_path, detail, result,
                        request_fields=("advertiser_id", "ad_id"))

        material_path = "/open_api/v1.0/qianchuan/uni_promotion/ad/material/get/"
        materials, result = probe(client, "plan_materials", lambda: client.request(
            "GET", material_path, token=token,
            params={"advertiser_id": advertiser_id, "ad_id": ad_id,
                    "filtering": json.dumps({"material_type": "VIDEO"}, separators=(",", ":")),
                    "fields": json.dumps(["stat_cost_for_roi2"], separators=(",", ":")),
                    "page": 1, "page_size": 10}))
        material_field = "material_list" if isinstance(materials, dict) and "material_list" in materials else "list"
        emit_capability("plan_materials", material_path, materials, result, material_field,
                        request_fields=("advertiser_id", "ad_id", "filtering", "fields",
                                        "page", "page_size"))

        stats = [row.get("stats_info") for row in plan_rows if isinstance(row.get("stats_info"), dict)]
        report_data = {"stats_info": stats}
        report_result = {"outcome": "success"}
        emit_capability("plan_report", list_path, report_data, report_result, "stats_info",
                        request_fields=("fields", "marketing_goal", "time_window"))

    if not upload:
        print(json.dumps({"capability": "material_plans", "conclusion": "no_sample_data",
                          "reason": "no_owner_verified_material"}, ensure_ascii=False))
        return

    association_path = "/open_api/v1.0/qianchuan/material/ad/get/"
    association_results: list[str] = []
    for scene in ("FEED", "SEARCH", "SHOPPING_MALL"):
        associations, result = probe(client, f"material_plans:{scene}", lambda scene=scene: client.request(
            "GET", association_path, token=token,
            params={"advertiser_id": advertiser_id, "material_id": upload.material_id,
                    "material_type": "VIDEO", "marketing_scene": scene,
                    "marketing_goal": "VIDEO_PROM_GOODS", "start_time": start_date, "end_time": end_date,
                    "fields": json.dumps(["stat_cost"], separators=(",", ":")),
                    "page": 1, "page_size": 10}))
        list_field = "ad_list" if isinstance(associations, dict) and "ad_list" in associations else "list"
        association_results.append(emit_capability(
            f"material_plans:{scene}", association_path, associations, result, list_field,
            request_fields=("advertiser_id", "material_id", "material_type", "marketing_scene",
                            "marketing_goal", "start_time", "end_time", "fields", "page", "page_size")))
    overall = "verified" if "verified" in association_results else (
        "no_sample_data" if all(item == "no_sample_data" for item in association_results)
        else "permission_missing" if all(item == "permission_missing" for item in association_results)
        else "contract_uncertain")
    print(json.dumps({"capability": "material_plans", "conclusion": overall,
                      "scenes_tested": ["FEED", "SEARCH", "SHOPPING_MALL"]}, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-only", action="store_true", help="recheck material lookup without other calls")
    parser.add_argument("--plan-only", action="store_true", help="only probe full-domain read capabilities")
    parser.add_argument("--connection-id", help="explicit local connection UUID; never printed")
    parser.add_argument("--binding-id", help="explicit local account-binding UUID; never printed")
    args = parser.parse_args()
    try:
        connection_id = opaque_id(args.connection_id)
        binding_id = opaque_id(args.binding_id)
    except ValueError as exc:
        print(f"Preflight: {exc}，未发送任何平台请求")
        return 2
    settings = get_settings()
    service = get_qianchuan_service()
    if service.configuration()["status"] != "ready":
        print("Preflight: 千川应用配置不完整")
        return 2
    with SessionLocal() as db:
        connection_query = db.query(Connection).filter_by(status="active")
        if connection_id:
            connection_query = connection_query.filter_by(id=connection_id)
        connections = connection_query.all()
        if len(connections) != 1:
            hint = "请传 --connection-id" if not connection_id else "所选连接不存在或不是 active"
            print(f"Preflight: 候选 active 连接数为 {len(connections)}，{hint}，未发送任何平台请求")
            return 2
        conn = connections[0]
        binding_query = db.query(Binding).filter_by(
            connection_id=conn.id, tenant_id=conn.tenant_id, is_valid=True)
        if binding_id:
            binding_query = binding_query.filter_by(id=binding_id)
        bindings = binding_query.all()
        if len(bindings) != 1:
            hint = "请传 --binding-id" if not binding_id else "所选绑定不属于该连接或已失效"
            print(f"Preflight: 候选有效广告账户数为 {len(bindings)}，{hint}，未发送任何平台请求")
            return 2
        binding = bindings[0]
        if not conn.access_token_expires_at or conn.access_token_expires_at <= datetime.now(UTC).replace(tzinfo=None):
            print("Preflight: 当前访问令牌已过期，未自动刷新、未发送任何平台请求")
            return 2
        owner_uploads = db.query(Upload).filter_by(
            tenant_id=conn.tenant_id, created_by=conn.created_by,
            binding_id=binding.id, advertiser_id=binding.advertiser_id, status="succeeded",
        ).order_by(Upload.created_at.desc()).all()
        upload = next((item for item in owner_uploads if item.material_id), None)
        token = service.decrypt(conn.access_token_ciphertext)
        print(json.dumps({"preflight": "ready", "advertiser": mask(binding.advertiser_id),
                          "source_role": binding.source_account_type,
                          "owner_verified_material": bool(upload),
                          "token_expiry_utc": conn.access_token_expires_at.isoformat()}, ensure_ascii=False))

        client = QianchuanClient(settings.model_copy(update={"qianchuan_log_material_response": False}))
        try:
            if args.video_only:
                if not upload:
                    print(json.dumps({"test": "video_get_by_material", "outcome": "not_tested_no_owner_material"},
                                     ensure_ascii=False))
                    return 0
                video, _ = probe(client, "video_get_by_material", lambda: client.request(
                    "GET", "/open_api/v1.0/qianchuan/video/get/", token=token,
                    params={"advertiser_id": binding.advertiser_id,
                            "filtering": json.dumps({"material_ids": [int(upload.material_id)]}),
                            "page": 1, "page_size": 100}))
                if video is not None:
                    rows = video.get("list")
                    matched = any(str(r.get("material_id")) == upload.material_id for r in rows
                                  if isinstance(r, dict)) if isinstance(rows, list) else False
                    print(json.dumps({"test": "video_get_shape", "matched": matched,
                                      "row_count": len(rows) if isinstance(rows, list) else "invalid"},
                                     ensure_ascii=False))
                return 0
            probe_plan_capabilities(client, token, binding.advertiser_id, upload)
            if args.plan_only:
                return 0
            authorized, _ = probe(client, "oauth_advertiser_get", lambda: client.request(
                "GET", "/open_api/oauth2/advertiser/get/", params={"access_token": token}))
            if authorized is not None:
                objects = authorized.get("list")
                print(json.dumps({"test": "oauth_advertiser_get_shape", "object_count":
                                  len(objects) if isinstance(objects, list) else "invalid"}, ensure_ascii=False))

            role = binding.source_account_type
            if role == "PLATFORM_ROLE_SHOP_ACCOUNT":
                shop, _ = probe(client, "shop_advertiser_list", lambda: client.request(
                    "GET", "/open_api/v1.0/qianchuan/shop/advertiser/list/", token=token,
                    params={"shop_id": binding.source_account_id, "page": 1, "page_size": 100}))
                if shop is not None:
                    rows = shop.get("adv_id_list", shop.get("list"))
                    print(json.dumps({"test": "shop_advertiser_list_shape", "count":
                                      len(rows) if isinstance(rows, list) else "invalid"}, ensure_ascii=False))
            elif role in {"CUSTOMER_ADMIN", "CUSTOMER_OPERATOR"}:
                workspace, _ = probe(client, "workspace_account_list", lambda: client.request(
                    "GET", "/open_api/v3.0/customer_center/account/list/", token=token,
                    params={"account_id": binding.source_account_id,
                            "filter": json.dumps({"account_type": "QIANCHUAN"}),
                            "page": 1, "page_size": 100}))
                if workspace is not None:
                    rows = workspace.get("accounts")
                    print(json.dumps({"test": "workspace_account_list_shape", "count":
                                      len(rows) if isinstance(rows, list) else "invalid"}, ensure_ascii=False))
            else:
                print(json.dumps({"test": "source_discovery", "outcome": "not_applicable",
                                  "role": role}, ensure_ascii=False))

            account_type, _ = probe(client, "advertiser_type_get", lambda: client.request(
                "GET", "/open_api/v1.0/qianchuan/advertiser/type/get/", token=token,
                params={"advertiser_ids": json.dumps([int(identifier(binding.advertiser_id))])}))
            if account_type is not None:
                rows = account_type.get("list")
                types = sorted({r.get("ecp_type") for r in rows if isinstance(r, dict)
                                and isinstance(r.get("ecp_type"), str)}) if isinstance(rows, list) else []
                print(json.dumps({"test": "advertiser_type_shape", "types": types}, ensure_ascii=False))

            if upload:
                video, _ = probe(client, "video_get_by_material", lambda: client.request(
                    "GET", "/open_api/v1.0/qianchuan/video/get/", token=token,
                    params={"advertiser_id": binding.advertiser_id,
                            "filtering": json.dumps({"material_ids": [int(upload.material_id)]}),
                            "page": 1, "page_size": 100}))
                if video is not None:
                    rows = video.get("list")
                    matched = any(str(r.get("material_id")) == upload.material_id for r in rows
                                  if isinstance(r, dict)) if isinstance(rows, list) else False
                    print(json.dumps({"test": "video_get_shape", "matched": matched,
                                      "row_count": len(rows) if isinstance(rows, list) else "invalid"},
                                     ensure_ascii=False))
            else:
                print(json.dumps({"test": "video_get_by_material", "outcome": "not_tested_no_owner_material"},
                                 ensure_ascii=False))

            chosen = None
            for topic in (TOPIC, UNI_TOPIC):
                config, status = probe(client, f"report_config:{topic}", lambda topic=topic: client.report_config(
                    token, binding.advertiser_id, [topic]))
                if config is not None:
                    topics = config.get("custom_config_datas")
                    entry = next((item for item in topics if isinstance(item, dict)
                                  and item.get("data_topic") == topic), None) if isinstance(topics, list) else None
                    if entry:
                        dimensions = entry.get("dimensions") or []
                        metrics = entry.get("metrics") or []
                        dim_fields = {item.get("field") for item in dimensions if isinstance(item, dict)}
                        metric_fields = {item.get("field") for item in metrics if isinstance(item, dict)}
                        print(json.dumps({"test": f"report_config_shape:{topic}",
                                          "material_dimension": "material_id" in dim_fields,
                                          "supported_ui_metric_count": len(set(METRICS) & metric_fields)},
                                         ensure_ascii=False))
                        if chosen is None and "material_id" in dim_fields:
                            chosen = (topic, dim_fields, metric_fields)
                if topic == TOPIC and status["outcome"] != "report_overall_unavailable":
                    break

            if upload and chosen:
                topic, dimensions_available, metrics_available = chosen
                dimensions = ["material_id"]
                filters = [{"field": "material_id", "operator": 7,
                            "values": [identifier(upload.material_id)]}]
                if topic == TOPIC:
                    if "roi2_material_type_v3" not in dimensions_available:
                        print(json.dumps({"test": "report_data", "outcome": "not_tested_missing_filter"},
                                         ensure_ascii=False))
                        chosen = None
                    else:
                        filters.append({"field": "roi2_material_type_v3", "operator": 7, "values": ["3"]})
                elif "roi2_material_video_name" in dimensions_available:
                    dimensions.append("roi2_material_video_name")
                selected = [field for field in METRICS if field in metrics_available]
                if chosen and selected:
                    for days in (7, 30):
                        now = datetime.now(ZoneInfo("Asia/Shanghai"))
                        start = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00:00")
                        report, _ = probe(client, f"report_data:{topic}:{days}d", lambda: client.report_data(
                            token, binding.advertiser_id, data_topic=topic, dimensions=dimensions,
                            metrics=selected, filters=filters, start_time=start,
                            end_time=now.strftime("%Y-%m-%d %H:%M:%S")))
                        if report is not None:
                            rows = report.get("rows")
                            print(json.dumps({"test": f"report_data_shape:{days}d", "row_count":
                                              len(rows) if isinstance(rows, list) else "invalid",
                                              "nonempty_metric_fields": sorted({key for row in rows
                                                  if isinstance(row, dict) and isinstance(row.get("metrics"), dict)
                                                  for key, value in row["metrics"].items() if value is not None})
                                                  if isinstance(rows, list) else []}, ensure_ascii=False))
            else:
                print(json.dumps({"test": "report_data", "outcome": "not_tested_no_owner_material_or_config"},
                                 ensure_ascii=False))

            if upload:
                now = datetime.now(ZoneInfo("Asia/Shanghai"))
                daily, _ = probe(client, "video_user_lose_get", lambda: client.request(
                    "GET", "/open_api/v1.0/qianchuan/report/video_user_lose/get/", token=token,
                    params={"advertiser_id": binding.advertiser_id,
                            "fields": json.dumps(["click_cnt", "dy_comment", "dy_follow", "dy_like",
                                                  "user_lose_cnt"], separators=(",", ":")),
                            "start_date": (now - timedelta(days=6)).strftime("%Y-%m-%d"),
                            "end_date": now.strftime("%Y-%m-%d"),
                            "filtering": json.dumps({"material_id": int(upload.material_id)})}))
                if daily is not None:
                    rows = daily.get("list")
                    print(json.dumps({"test": "video_user_lose_shape", "row_count":
                                      len(rows) if isinstance(rows, list) else "invalid",
                                      "has_second_field": any("h_sec" in row for row in rows if isinstance(row, dict))
                                      if isinstance(rows, list) else False}, ensure_ascii=False))
            else:
                print(json.dumps({"test": "video_user_lose_get", "outcome": "not_tested_no_owner_material"},
                                 ensure_ascii=False))
        finally:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
