"""Recover historical lineage only from exact completed output/job/plan bindings."""
from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.models import EditingPlanVersion, VideoAsset, VideoLineageSegment, VideoRenderJob
from app.services.video_lineage import record_render_lineage


def resolve_historical_plan(db: Session, target: VideoAsset):
    jobs = db.query(VideoRenderJob).filter_by(
        tenant_id=target.tenant_id, project_id=target.project_id,
        output_url=target.object_url, status="completed",
    ).all() if target.project_id and target.object_url else []
    if len(jobs) != 1:
        raise ValueError("没有唯一匹配的已完成渲染记录")
    job = jobs[0]
    plan = db.query(EditingPlanVersion).filter_by(
        id=job.plan_version_id, tenant_id=target.tenant_id, project_id=target.project_id,
    ).first()
    if not plan or plan.created_at > job.created_at or job.created_at > target.created_at:
        raise ValueError("剪辑方案缺失或记录时间不一致")
    variants = [v for v in (plan.plan_json or {}).get("variants", []) if v.get("variant_id") == job.variant_id]
    if len(variants) != 1:
        raise ValueError("没有唯一匹配的剪辑版本")
    variant = variants[0]
    mode = variant.get("edit_mode")
    if mode not in {None, "", "smart_compose", "finished_remix", "manual_compose"}:
        raise ValueError("不支持的历史剪辑模式")
    durations = [float(target.duration_seconds), float(job.duration_seconds), float(variant["actual_duration_seconds"])]
    if not all(math.isfinite(d) and d > 0 for d in durations) or max(durations) - min(durations) > .05:
        raise ValueError("成片与剪辑方案时长不一致")
    clips = variant.get("narrative_segments" if mode in {"finished_remix", "manual_compose"} else "video_track") or []
    picture_duration = sum(float(c["source_range"]["end"]) - float(c["source_range"]["start"]) for c in clips)
    if not math.isfinite(picture_duration) or abs(picture_duration - durations[0]) > .05:
        raise ValueError("画面片段未完整覆盖成片时长")
    all_ids = {c["asset_id"] for c in list(variant.get("video_track") or []) + list(variant.get("narrative_segments") or []) if c.get("asset_id")}
    if target.origin_asset_ids and all_ids != set(target.origin_asset_ids):
        raise ValueError("剪辑方案与历史来源清单不一致")
    picture_ids = {c["asset_id"] for c in clips}
    assets = db.query(VideoAsset).filter(
        VideoAsset.tenant_id == target.tenant_id, VideoAsset.product_id == target.product_id,
        VideoAsset.asset_id.in_(picture_ids),
    ).all()
    if {a.asset_id for a in assets} != picture_ids or any(a.created_at > target.created_at for a in assets):
        raise ValueError("来源素材缺失或来源时间不一致")
    return plan, variant, assets


def backfill_video_lineage(db: Session, tenant_id: str, apply: bool = False) -> list[dict]:
    """Per-output atomic writes; dry runs roll back every generated segment."""
    ids = [row.id for row in db.query(VideoAsset.id).filter_by(
        tenant_id=tenant_id, asset_kind="final_output",
    ).order_by(VideoAsset.created_at, VideoAsset.id).all()]
    report = []
    for asset_id in ids:
        target = db.query(VideoAsset).filter_by(id=asset_id, tenant_id=tenant_id).with_for_update().one()
        item = {"id": target.id, "asset_id": target.asset_id}
        if db.query(VideoLineageSegment.id).filter_by(target_asset_id=target.id).first():
            report.append({**item, "status": "existing"})
            db.rollback()
            continue
        try:
            plan, variant, sources = resolve_historical_plan(db, target)
            record_render_lineage(db, target, variant, sources, plan.id)
            db.flush()
            rows = db.query(VideoLineageSegment).filter_by(target_asset_id=target.id).all()
            result = {**item, "status": "restored" if apply else "ready", "plan_version_id": plan.id,
                      "segments": len(rows), "sources": len({r.original_asset_id or r.source_asset_id for r in rows})}
            if apply:
                db.commit()
            else:
                db.rollback()
            report.append(result)
        except (ValueError, KeyError, TypeError) as exc:
            db.rollback()
            report.append({**item, "status": "skipped", "reason": str(exc)})
        except Exception:
            db.rollback()
            raise
    return report
