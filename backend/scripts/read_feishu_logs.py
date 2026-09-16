"""Read structured .raw.log files (and legacy JSONL) without loading credentials."""
from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path


def read_events(directory, *, request_id=None, notification_uuid=None, task_id=None, limit=30):
    def matching():
        paths = sorted({*Path(directory).rglob("feishu-*.raw.log*"), *Path(directory).rglob("feishu-*.jsonl*")})
        for path in paths:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue  # A concurrently written last line may be incomplete.
                    if not isinstance(event, dict):
                        continue
                    filters = {"request_id": request_id, "notification_uuid": notification_uuid, "task_id": task_id}
                    if all(value is None or event.get(key) == value for key, value in filters.items()):
                        yield event
    return sorted(heapq.nlargest(limit, matching(), key=lambda item: item.get("time", "")), key=lambda item: item.get("time", ""))


def render_event(event, raw=False):
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    lines = [f"{event.get('time', '')} {event.get('level', '')} [{event.get('event', '')}] {quote(event.get('remark', ''))}"]
    labels = {"request_id": "请求编号", "call_id": "第三方调用编号", "notification_uuid": "通知 UUID", "task_id": "拍摄任务编号",
              "project_id": "脚本编号", "method": "请求方法", "path": "接口路径", "http_status": "HTTP 状态码",
              "business_code": "飞书业务码", "business_message": "飞书原始说明", "upstream_request_id": "飞书日志 ID",
              "duration_ms": "耗时（毫秒）", "notification_status": "通知状态", "error_type": "异常类型", "reason": "原因"}
    for key, label in labels.items():
        if key in event and event[key] is not None:
            lines.append(f"  {label}: {quote(event[key])}")
    if event.get("event") == "feishu.http.failed" and event.get("http_status") is None:
        lines.append("  HTTP 状态码: 未收到响应")
    if raw:
        lines.append("  原始事件（凭证及常见个人信息字段已脱敏；超过上限的正文有 truncated 标记）:")
        lines.append(json.dumps(event, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="按请求、通知或任务编号查看飞书日志；默认最近 30 条，--raw 展开原始数据")
    parser.add_argument("--log-dir", type=Path, default=Path(__file__).resolve().parents[1] / "logs")
    parser.add_argument("--request-id")
    parser.add_argument("--notification-uuid")
    parser.add_argument("--task-id", type=int)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--raw", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须大于 0")
    if not args.log_dir.is_dir():
        parser.error(f"日志目录不存在：{args.log_dir}；请确认服务已产生日志，或使用 --log-dir 指定目录")
    events = read_events(args.log_dir, request_id=args.request_id, notification_uuid=args.notification_uuid,
                         task_id=args.task_id, limit=args.limit)
    if not events:
        print("没有匹配的日志记录。")
    for event in events:
        print(render_event(event, raw=args.raw))
        print()


if __name__ == "__main__":
    main()
