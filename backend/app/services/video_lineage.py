"""Immutable picture-use records captured in the output asset transaction."""
from __future__ import annotations

import math
import re
from typing import Any

from sqlalchemy import func, or_, union_all
from sqlalchemy.orm import Session

from app.models import VideoAsset, VideoLineageSegment


def reference_counts(db: Session, tenant_id: str, asset_ids: list[int]) -> dict[int, int]:
    if not asset_ids:
        return {}
    parts = []
    for column in (VideoLineageSegment.source_asset_id, VideoLineageSegment.original_asset_id):
        parts.append(db.query(column.label("source_id"), VideoLineageSegment.target_asset_id.label("target_id"))
                     .join(VideoAsset, VideoAsset.id == VideoLineageSegment.target_asset_id)
                     .filter(VideoLineageSegment.tenant_id == tenant_id, column.in_(asset_ids),
                             VideoAsset.tenant_id == tenant_id, VideoAsset.asset_kind == "final_output").statement)
    uses = union_all(*parts).subquery()
    return dict(db.query(uses.c.source_id, func.count(func.distinct(uses.c.target_id)))
                .group_by(uses.c.source_id).all())


def source_condition(asset_id: int):
    return or_(VideoLineageSegment.source_asset_id == asset_id,
               VideoLineageSegment.original_asset_id == asset_id)


def record_render_lineage(db: Session, target: VideoAsset, variant: dict[str, Any],
                          assets: list[VideoAsset], plan_version_id: int) -> None:
    """Match the renderer's concatenation order, ignoring audio-only narrative clips."""
    db.flush()
    if target.asset_kind != "final_output" or not math.isfinite(target.duration_seconds) or target.duration_seconds <= 0:
        raise ValueError("成片类型或时长无效")
    if db.query(VideoLineageSegment.id).filter_by(target_asset_id=target.id).first():
        raise ValueError("成片来源记录已存在, 不允许覆盖")
    mode = variant.get("edit_mode")
    clips = variant.get("narrative_segments" if mode in {"finished_remix", "manual_compose"} else "video_track") or []
    if not clips:
        raise ValueError("缺少画面来源记录")
    asset_map = {asset.asset_id: asset for asset in assets}
    originals: dict[int, Any] = {}
    cursor = 0.0
    rows = []
    for index, clip in enumerate(clips):
        source = asset_map.get(clip.get("asset_id"))
        if not source or source.tenant_id != target.tenant_id or source.id == target.id:
            raise ValueError("画面来源素材无效")
        start, end = float(clip["source_range"]["start"]), float(clip["source_range"]["end"])
        if not all(math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
            raise ValueError("来源片段时间无效")
        duration = end - start
        # Manual composition renders whole files from zero; the other modes trim sources.
        if mode == "manual_compose":
            start, end = 0.0, duration
        if end > source.duration_seconds + .05:
            raise ValueError("来源片段超出素材时长")
        target_end = min(cursor + duration, target.duration_seconds)
        if target_end <= cursor:
            break
        end = start + target_end - cursor
        if source.id not in originals:
            originals[source.id] = None
            # A reuploaded, byte-identical final is still the same picture source.
            # Do not infer identity from filenames, OSS ETags, or approximate similarity.
            if source.asset_kind == "material" and re.fullmatch(r"[0-9a-f]{64}", source.content_hash or ""):
                matches = db.query(VideoAsset.id).filter(
                    VideoAsset.tenant_id == target.tenant_id, VideoAsset.product_id == source.product_id,
                    VideoAsset.asset_kind == "final_output", VideoAsset.content_hash == source.content_hash,
                    VideoAsset.id != target.id, VideoAsset.created_at <= source.created_at,
                ).limit(2).all()
                if len(matches) == 1:
                    originals[source.id] = matches[0].id
        rows.append(VideoLineageSegment(
            tenant_id=target.tenant_id, source_asset_id=source.id, original_asset_id=originals[source.id],
            target_asset_id=target.id, plan_version_id=plan_version_id, variant_id=str(variant["variant_id"]),
            segment_index=index, source_start=start, source_end=end,
            target_start=cursor, target_end=target_end, created_at=target.created_at,
        ))
        cursor = target_end
    if not rows:
        raise ValueError("成片时长无效")
    db.add_all(rows)
