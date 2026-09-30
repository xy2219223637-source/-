from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import VideoAsset, VideoRepairInput


PUBLIC_SOURCE_KINDS = {"MANUAL", "INTERNAL_REVIEW"}


def create_input(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    source_asset_id: int,
    source_kind: str,
    audit_platform: str,
    raw_reason: str,
    expected_result: str = "",
    suggestions: list[str],
    time_ranges: list[dict[str, float]],
    raw_payload: dict[str, Any] | None = None,
    external_ref_type: str = "",
    external_ref_id: str = "",
    occurred_at: datetime | None = None,
    allow_external_source: bool = False,
) -> VideoRepairInput:
    if not allow_external_source and source_kind not in PUBLIC_SOURCE_KINDS:
        raise HTTPException(403, detail={
            "code": "INPUT_SOURCE_FORBIDDEN",
            "message": "外部平台来源只能由服务端适配器创建",
            "field": "input.source_kind",
            "retryable": False,
        })
    payload_snapshot = dict(raw_payload or {})
    if not external_ref_type and not external_ref_id:
        canonical = json.dumps({
            "source_kind": source_kind, "audit_platform": audit_platform,
            "raw_reason": raw_reason.strip(), "expected_result": expected_result.strip(),
            "suggestions": suggestions,
            "time_ranges": time_ranges, "raw_payload": payload_snapshot,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        external_ref_type = "video_repair_input_hash"
        external_ref_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    existing = db.query(VideoRepairInput).filter_by(
        tenant_id=tenant_id, created_by=user_id, source_asset_id=source_asset_id,
        external_ref_type=external_ref_type, external_ref_id=external_ref_id,
    ).first()
    if existing:
        return existing
    source = db.query(VideoAsset).filter_by(
        id=source_asset_id, tenant_id=tenant_id, asset_kind="final_output").first()
    if not source:
        raise HTTPException(404, detail={
            "code": "SOURCE_ASSET_NOT_FOUND", "message": "成片不存在或无权访问",
            "field": "source_asset_id", "retryable": False,
        })
    duration = float(source.duration_seconds or 0)
    for row in time_ranges:
        start, end = float(row["start"]), float(row["end"])
        if start < 0 or end <= start or (duration > 0 and end > duration):
            raise HTTPException(422, detail={
                "code": "INVALID_TIME_RANGE",
                "message": f"问题时间段必须位于成片时长 0–{duration:.1f} 秒内",
                "field": "input.time_ranges",
                "retryable": False,
            })
    row = VideoRepairInput(
        tenant_id=tenant_id,
        created_by=user_id,
        source_asset_id=source_asset_id,
        source_kind=source_kind,
        external_ref_type=external_ref_type,
        external_ref_id=external_ref_id,
        audit_platform=audit_platform,
        raw_reason=raw_reason.strip(),
        expected_result=expected_result.strip(),
        suggestions_json=list(suggestions),
        time_ranges_json=list(time_ranges),
        raw_payload_json=payload_snapshot,
        occurred_at=occurred_at or datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row
