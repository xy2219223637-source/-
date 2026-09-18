"""Tenant-scoped, direct video provenance and reverse-use queries."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_auth
from app.database import get_db
from app.models import VideoAsset, VideoLineageSegment
from app.services.video_lineage import reference_counts, source_condition

router = APIRouter(prefix="/video-assets", tags=["video-lineage"])


def asset_summary(asset: VideoAsset, counts: dict[int, int]) -> dict:
    return {"id": asset.id, "asset_id": asset.asset_id, "filename": asset.filename,
            "asset_kind": asset.asset_kind, "library_type": asset.library_type,
            "object_url": asset.object_url, "duration_seconds": asset.duration_seconds,
            "reference_count": counts.get(asset.id, 0),
            "created_at": asset.created_at.replace(tzinfo=timezone.utc).isoformat()}


@router.get("/{asset_id}/lineage")
def get_lineage(asset_id: int, direction: Literal["sources", "used"] = "sources",
                q: str = "", sort: Literal["recent", "old", "name"] = "recent",
                start_date: Optional[date] = None, end_date: Optional[date] = None,
                page: int = Query(1, ge=1), page_size: int = Query(6, ge=1, le=100),
                db: Session = Depends(get_db), auth: AuthContext = Depends(current_auth)):
    asset = db.query(VideoAsset).filter_by(id=asset_id, tenant_id=auth.tenant_id).first()
    if not asset:
        raise HTTPException(404, "视频不存在")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    base = db.query(VideoLineageSegment).filter(VideoLineageSegment.tenant_id == auth.tenant_id)
    source_id = func.coalesce(VideoLineageSegment.original_asset_id, VideoLineageSegment.source_asset_id)
    source_count = base.filter(VideoLineageSegment.target_asset_id == asset_id).with_entities(
        func.count(func.distinct(source_id))).scalar() or 0
    use_count = reference_counts(db, auth.tenant_id, [asset_id]).get(asset_id, 0)
    related_id = source_id if direction == "sources" else VideoLineageSegment.target_asset_id
    query = base.filter(VideoLineageSegment.target_asset_id == asset_id) if direction == "sources" else base.filter(source_condition(asset_id))
    query = query.join(VideoAsset, VideoAsset.id == related_id).filter(VideoAsset.tenant_id == auth.tenant_id)
    if direction == "used":
        query = query.filter(VideoAsset.asset_kind == "final_output")
    if q.strip():
        needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(VideoAsset.filename.ilike(f"%{needle}%", escape="\\") |
                             VideoAsset.asset_id.ilike(f"%{needle}%", escape="\\"))
    # Dates in this UI are Asia/Shanghai calendar days; stored timestamps are UTC.
    if start_date:
        query = query.filter(VideoLineageSegment.created_at >= datetime.combine(start_date, time()) - timedelta(hours=8))
    if end_date:
        query = query.filter(VideoLineageSegment.created_at < datetime.combine(end_date, time()) + timedelta(days=1, hours=-8))
    grouped = query.with_entities(related_id.label("related_id"), func.min(VideoLineageSegment.created_at).label("linked_at"),
                                 VideoAsset.filename.label("filename")).group_by(related_id, VideoAsset.filename)
    total = grouped.count()
    page = min(page, max(1, (total + page_size - 1) // page_size))
    if direction == "sources":
        grouped = grouped.order_by(func.min(VideoLineageSegment.target_start), related_id)
    elif sort == "name":
        grouped = grouped.order_by(VideoAsset.filename, related_id)
    else:
        order = func.min(VideoLineageSegment.created_at)
        grouped = grouped.order_by(order.asc() if sort == "old" else order.desc(), related_id)
    # All source segments are needed to draw a truthful full-duration timeline.
    if direction == "sources":
        page, page_size = 1, max(1, total)
        selected = grouped.all()
    else:
        selected = grouped.offset((page - 1) * page_size).limit(page_size).all()
    ids = [item.related_id for item in selected]
    related = {item.id: item for item in db.query(VideoAsset).filter(VideoAsset.tenant_id == auth.tenant_id, VideoAsset.id.in_(ids)).all()}
    counts = reference_counts(db, auth.tenant_id, [asset_id, *ids])
    segments: dict[int, list] = {item_id: [] for item_id in ids}
    for row in query.filter(related_id.in_(ids)).order_by(VideoLineageSegment.target_start, VideoLineageSegment.segment_index).all():
        key = (row.original_asset_id or row.source_asset_id) if direction == "sources" else row.target_asset_id
        segments[key].append({"id": row.id, "source_start": row.source_start, "source_end": row.source_end,
                              "target_start": row.target_start, "target_end": row.target_end})
    current = asset_summary(asset, counts)
    return {"source_count": source_count, "reference_count": use_count, "total": total,
            "matched_asset_ids": [item.related_id for item in grouped.all()],
            "page": page, "page_size": page_size, "items": [
                {"source": asset_summary(related[item.related_id], counts) if direction == "sources" else current,
                 "target": current if direction == "sources" else asset_summary(related[item.related_id], counts),
                 "created_at": item.linked_at.replace(tzinfo=timezone.utc).isoformat(),
                 "segments": segments[item.related_id]} for item in selected]}
