from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import VideoAsset, VideoAssetAnalysis, VideoHighlight, VideoLineageSegment
from app.services.video_repair.continuity import assess_continuity


ACTION_ROLES = {
    "VISUAL_REPLACE": {"product_demo", "selling_point", "proof", "brand_trust", "core_content"},
    "AV_SEGMENT_REPLACE": {"product_demo", "usage", "selling_point", "proof", "core_content"},
    "PROCESS_INSERT": {"usage", "product_demo"},
}


def candidate_id(asset: VideoAsset, highlight: VideoHighlight) -> str:
    return f"{asset.asset_id}:{highlight.highlight_id}"


def find_candidates(db: Session, *, tenant_id: str, source_asset: VideoAsset,
                    action: str, target_duration: float, limit: int = 12,
                    forbidden_source_ranges: dict[int, list[tuple[float, float]]] | None = None,
                    issue_description: str = "", target_text: str = "",
                    repair_context: dict[str, Any] | None = None,
                    recommend_score: float = .75, selectable_score: float = .60) -> list[dict[str, Any]]:
    """Return auditable source ranges, scoped to the same tenant and product."""
    roles = ACTION_ROLES.get(action, set())
    rows = (
        db.query(VideoAsset, VideoHighlight)
        .join(VideoAssetAnalysis, VideoAssetAnalysis.asset_id == VideoAsset.id)
        .join(VideoHighlight, VideoHighlight.analysis_id == VideoAssetAnalysis.id)
        .filter(
            VideoAsset.tenant_id == tenant_id,
            VideoAsset.product_id == source_asset.product_id,
            VideoAsset.id != source_asset.id,
            VideoAsset.excluded.is_(False),
            VideoAsset.reusable_status.in_(("reusable", "review_required")),
            VideoAsset.status.in_(("ready", "analyzed", "completed")),
            VideoAssetAnalysis.tenant_id == tenant_id,
            VideoAssetAnalysis.status == "completed",
            VideoHighlight.tenant_id == tenant_id,
            VideoHighlight.asset_id == VideoAsset.id,
            VideoHighlight.end_seconds > VideoHighlight.start_seconds,
        )
        .all()
    )
    result: list[dict[str, Any]] = []
    for asset, highlight in rows:
        duration = float(highlight.end_seconds - highlight.start_seconds)
        if action != "PROCESS_INSERT" and duration + 0.05 < target_duration:
            continue
        if action in {"AV_SEGMENT_REPLACE", "PROCESS_INSERT"} and not asset.has_audio:
            continue
        if action == "PROCESS_INSERT" and highlight.content_role not in ACTION_ROLES[action]:
            continue
        if not asset.content_hash or not asset.object_url:
            continue
        if asset.burned_subtitles:
            # Phase one cannot prove that burned-in candidate copy matches the target.
            # Excluding it is safer than hiding a conflict under another subtitle layer.
            continue
        if (asset.width and asset.height and source_asset.width and source_asset.height
                and (asset.width >= asset.height) != (source_asset.width >= source_asset.height)):
            continue
        forbidden = (forbidden_source_ranges or {}).get(asset.id, [])
        if any(highlight.start_seconds < end and highlight.end_seconds > start
               for start, end in forbidden):
            continue
        candidate = {
            "id": candidate_id(asset, highlight),
            "source_asset_id": asset.id,
            "source_asset_public_id": asset.asset_id,
            "highlight_id": highlight.highlight_id,
            "source_range": {"start": round(highlight.start_seconds, 3), "end": round(highlight.end_seconds, 3)},
            "duration_seconds": round(duration, 3),
            "content_role": highlight.content_role,
            "description": highlight.description,
            "object_url": asset.object_url,
            "has_audio": asset.has_audio,
            "content_hash": asset.content_hash,
            "reason": highlight.reason, "speech_text": highlight.speech_text,
            "asset_kind": asset.asset_kind, "quality_score": float(highlight.score),
            "same_orientation": True, "reusable_status": asset.reusable_status,
        }
        context = repair_context or {
            "narrative": {"target_intent": issue_description, "target_summary": target_text}}
        assessment = assess_continuity(
            candidate, context, target_duration=target_duration,
            recommend_score=recommend_score, selectable_score=selectable_score)
        candidate.update(assessment)
        if asset.reusable_status == "review_required":
            candidate["warnings"].append("素材尚未标记为可直接复用，请在预览中人工确认")
        result.append(candidate)
    result.sort(key=lambda row: (-row["score"], row["source_asset_id"], row["highlight_id"]))
    return result[:limit]


def resolve_candidate(db: Session, *, tenant_id: str, source_asset: VideoAsset,
                      candidate_value: str,
                      forbidden_source_ranges: dict[int, list[tuple[float, float]]] | None = None) -> dict[str, Any] | None:
    try:
        public_asset_id, highlight_id = candidate_value.split(":", 1)
    except ValueError:
        return None
    row = (
        db.query(VideoAsset, VideoHighlight)
        .join(VideoAssetAnalysis, VideoAssetAnalysis.asset_id == VideoAsset.id)
        .join(VideoHighlight, VideoHighlight.analysis_id == VideoAssetAnalysis.id)
        .filter(
            VideoAsset.tenant_id == tenant_id,
            VideoAsset.product_id == source_asset.product_id,
            VideoAsset.asset_id == public_asset_id,
            VideoAsset.id != source_asset.id,
            VideoAsset.excluded.is_(False),
            VideoAsset.reusable_status.in_(("reusable", "review_required")),
            VideoAsset.status.in_(("ready", "analyzed", "completed")),
            VideoAssetAnalysis.tenant_id == tenant_id,
            VideoAssetAnalysis.status == "completed",
            VideoHighlight.tenant_id == tenant_id,
            VideoHighlight.asset_id == VideoAsset.id,
            VideoHighlight.highlight_id == highlight_id,
            VideoHighlight.end_seconds > VideoHighlight.start_seconds,
        ).first()
    )
    if not row:
        return None
    asset, highlight = row
    if any(highlight.start_seconds < end and highlight.end_seconds > start
           for start, end in (forbidden_source_ranges or {}).get(asset.id, [])):
        return None
    return {
        "id": candidate_id(asset, highlight), "source_asset_id": asset.id,
        "source_asset_public_id": asset.asset_id, "highlight_id": highlight.highlight_id,
        "source_range": {"start": round(highlight.start_seconds, 3),
                         "end": round(highlight.end_seconds, 3)},
        "duration_seconds": round(highlight.end_seconds - highlight.start_seconds, 3),
        "content_role": highlight.content_role, "description": highlight.description,
        "object_url": asset.object_url, "has_audio": asset.has_audio,
        "content_hash": asset.content_hash, "burned_subtitles": asset.burned_subtitles,
        "same_orientation": not (
            asset.width and asset.height and source_asset.width and source_asset.height
        ) or ((asset.width >= asset.height) == (source_asset.width >= source_asset.height)),
    }


def validate_candidate_for_action(candidate: dict[str, Any], action: str,
                                  target_duration_seconds: float) -> None:
    duration = float(candidate["source_range"]["end"]) - float(candidate["source_range"]["start"])
    if action != "PROCESS_INSERT" and duration + 0.05 < target_duration_seconds:
        raise ValueError("CANDIDATE_TOO_SHORT")
    if action in {"AV_SEGMENT_REPLACE", "PROCESS_INSERT"} and not candidate.get("has_audio"):
        raise ValueError("CANDIDATE_AUDIO_REQUIRED")
    if action == "PROCESS_INSERT" and candidate.get("content_role") not in ACTION_ROLES[action]:
        raise ValueError("CANDIDATE_NOT_ALLOWED")
    if candidate.get("burned_subtitles"):
        raise ValueError("CANDIDATE_SUBTITLE_CONFLICT")
    if not candidate.get("same_orientation", True):
        raise ValueError("CANDIDATE_ASPECT_MISMATCH")
    if not candidate.get("content_hash") or not candidate.get("object_url"):
        raise ValueError("CANDIDATE_SOURCE_INVALID")


def candidate_snapshot(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["id"],
        "source_asset_id": candidate["source_asset_id"],
        "source_asset_public_id": candidate["source_asset_public_id"],
        "source_range": {
            "start_ms": round(float(candidate["source_range"]["start"]) * 1000),
            "end_ms": round(float(candidate["source_range"]["end"]) * 1000),
        },
        "content_hash": candidate["content_hash"],
        "has_audio": bool(candidate.get("has_audio")),
    }


def forbidden_lineage_ranges(db: Session, *, tenant_id: str, target_asset_id: int,
                             issue_ranges: list[dict[str, Any]]) -> dict[int, list[tuple[float, float]]]:
    """Map offending output ranges back to exact source ranges so they cannot be reselected."""
    result: dict[int, list[tuple[float, float]]] = {}
    rows = db.query(VideoLineageSegment).filter_by(
        tenant_id=tenant_id, target_asset_id=target_asset_id).all()
    for lineage in rows:
        for issue in issue_ranges:
            scale = 1000.0 if "start_ms" in issue else 1.0
            overlap_start = max(float(issue.get("start_ms", issue.get("start"))) / scale, lineage.target_start)
            overlap_end = min(float(issue.get("end_ms", issue.get("end"))) / scale, lineage.target_end)
            if overlap_end <= overlap_start:
                continue
            source_start = lineage.source_start + overlap_start - lineage.target_start
            source_end = lineage.source_start + overlap_end - lineage.target_start
            result.setdefault(lineage.source_asset_id, []).append((source_start, source_end))
    return result
