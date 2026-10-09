"""Reuse compatible observations bound to the same uploaded media version."""
from __future__ import annotations

from copy import deepcopy
import re

from app.models import VideoAssetAnalysis, VideoUploadSession
from app.services.storage import object_ref


def _binding(row, asset, uploads):
    data = row.analysis_json or {}
    recorded_hash = data.get("source_hash")
    if recorded_hash:
        return {"method": "content_hash"} if recorded_hash == asset.content_hash else None
    # Historical library analyses predate source_hash. A completed upload links
    # their public asset identity to an immutable object and upload version.
    if (row.prompt_version != "video-library-v3-whisper-timestamps"
            or data.get("asset_id") != asset.asset_id
            or data.get("schema_version") != "1.0"
            or asset.source != "manual_upload"):
        return None
    try:
        provider, key = object_ref(asset.object_url)
    except (ValueError, UnicodeError):
        return None
    prefix = provider + ":"
    if (not key or provider != asset.storage_provider
            or not asset.content_hash.startswith(prefix)
            or not re.fullmatch(r"[0-9a-fA-F]{32}(?:-[1-9][0-9]*)?", asset.content_hash[len(prefix):].strip('"'))):
        return None
    for upload in uploads:
        if (upload.tenant_id == asset.tenant_id and upload.video_asset_id == asset.id
                and upload.product_id == asset.product_id and upload.status == "completed"
                and upload.storage_provider == provider and upload.object_key == key
                and upload.completed_at and row.created_at and row.completed_at
                and upload.completed_at <= row.created_at <= row.completed_at
                and asset.duration_seconds > 0
                and abs(row.input_video_seconds - asset.duration_seconds) <= .05):
            return {"method": "completed_upload", "upload_id": upload.id, "object_key": key}
    return None


def load_cached_analyses(db, tenant_id, assets, *, require_visual=False):
    """Choose the latest compatible record, skipping newer incompatible ones."""
    by_id = {asset.id: asset for asset in assets if asset.tenant_id == tenant_id}
    result = {}
    ids = list(by_id)
    for offset in range(0, len(ids), 200):
        batch = ids[offset:offset + 200]
        uploads = {}
        for upload in db.query(VideoUploadSession).filter(
                VideoUploadSession.tenant_id == tenant_id,
                VideoUploadSession.video_asset_id.in_(batch),
                VideoUploadSession.status == "completed").all():
            uploads.setdefault(upload.video_asset_id, []).append(upload)
        rows = db.query(VideoAssetAnalysis).filter(
            VideoAssetAnalysis.tenant_id == tenant_id,
            VideoAssetAnalysis.asset_id.in_(batch),
            VideoAssetAnalysis.status == "completed").order_by(VideoAssetAnalysis.id.desc()).all()
        known_hashes = {}
        for row in rows:
            if (row.analysis_json or {}).get("source_hash"):
                known_hashes.setdefault(row.asset_id, set()).add(row.analysis_json["source_hash"])
        # Prefer the library's complete observations over sparse frame supplements.
        rows.sort(key=lambda row: (row.prompt_version == "video-library-v3-whisper-timestamps", row.id), reverse=True)
        for row in rows:
            if row.asset_id in result:
                continue
            asset = by_id[row.asset_id]
            data = row.analysis_json or {}
            if (row.schema_version != "1.0" or row.model in {"placeholder", "mock"}
                    or data.get("is_mock") or not data):
                continue
            visual = require_visual or getattr(asset, "asset_kind", None) == "material"
            if visual and not any(key in data for key in ("highlights", "video_tags", "actions", "scenes")):
                continue
            if not data.get("source_hash") and known_hashes.get(asset.id) and asset.content_hash not in known_hashes[asset.id]:
                continue
            binding = _binding(row, asset, uploads.get(asset.id, []))
            if not binding:
                continue
            if not data.get("source_hash"):
                data = {**deepcopy(data), "source_hash": asset.content_hash, "source_binding": binding}
                row.analysis_json = data
            result[asset.id] = {
                "analysis_id": row.id, "model": row.model, "summary": row.summary,
                "content_hash": asset.content_hash, "schema_version": row.schema_version,
                "source_binding": binding,
                "duration_ms": data.get("media_duration_ms") or round(asset.duration_seconds * 1000),
                "data": {**deepcopy(data), "source_hash": asset.content_hash},
            }
    return result
