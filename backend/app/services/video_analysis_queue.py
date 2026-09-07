"""Durable video-analysis worker backed by database state and Redis wakeups."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import threading
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import VideoAsset, VideoAssetAnalysis
from app.services.redis_client import (
    notify_video_analysis_queue,
    task_lock,
    wait_for_video_analysis_queue,
)

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
logger = logging.getLogger(__name__)


def enqueue_video_analysis() -> None:
    """Wake the worker; queued database rows remain the durable source of truth."""
    notify_video_analysis_queue()


def _next_asset_id() -> Optional[int]:
    db = SessionLocal()
    try:
        asset = (
            db.query(VideoAsset)
            .filter(
                VideoAsset.status == "queued_for_analysis",
                VideoAsset.excluded.is_(False),
            )
            .order_by(VideoAsset.created_at, VideoAsset.id)
            .first()
        )
        return asset.id if asset else None
    finally:
        db.close()


def _run_asset(asset_id: int) -> None:
    # Lazy import avoids a service/API import cycle during application startup.
    from app.api.video_library import analyze_video_asset

    asyncio.run(analyze_video_asset(asset_id))


def _next_validation_id() -> Optional[int]:
    db = SessionLocal()
    try:
        asset = db.query(VideoAsset).filter(VideoAsset.status == "validating").order_by(VideoAsset.created_at, VideoAsset.id).first()
        return asset.id if asset else None
    finally:
        db.close()


def _run_validation(asset_id: int) -> None:
    from app.api.video_library import validate_video_asset
    validate_video_asset(asset_id)


def _loop() -> None:
    while not _stop.is_set():
        try:
            validation_id = _next_validation_id()
            if validation_id is not None:
                lock = task_lock(f"video-validation-{validation_id}")
                if lock is None:
                    _run_validation(validation_id)
                elif lock.acquire(blocking=False):
                    try:
                        _run_validation(validation_id)
                    finally:
                        try: lock.release()
                        except Exception: pass
                else:
                    wait_for_video_analysis_queue(1)
                continue
            asset_id = _next_asset_id()
            if asset_id is None:
                wait_for_video_analysis_queue(1)
                continue

            lock = task_lock(f"video-analysis-{asset_id}")
            if lock is None:
                _run_asset(asset_id)
            elif lock.acquire(blocking=False):
                try:
                    _run_asset(asset_id)
                finally:
                    try:
                        lock.release()
                    except Exception:
                        pass
            else:
                wait_for_video_analysis_queue(1)
        except Exception:
            logger.exception("Video analysis worker iteration failed; retrying from durable database state")
            wait_for_video_analysis_queue(1)


def recover_interrupted_video_analyses(db: Session) -> int:
    """Database rows are the durable source of truth after process restart."""
    db.query(VideoAssetAnalysis).filter(VideoAssetAnalysis.status == "analyzing").update(
        {
            VideoAssetAnalysis.status: "interrupted",
            VideoAssetAnalysis.error_message: "服务重启，分析任务已自动重新排队",
            VideoAssetAnalysis.completed_at: datetime.utcnow(),
        },
        synchronize_session=False,
    )
    count = db.query(VideoAsset).filter(VideoAsset.status == "analyzing").update(
        {VideoAsset.status: "queued_for_analysis"}, synchronize_session=False
    )
    db.commit()
    return int(count or 0)


def start_video_analysis_worker() -> None:
    global _thread
    db = SessionLocal()
    try:
        # An analyzing row cannot still have a live in-process task after restart.
        recover_interrupted_video_analyses(db)
    finally:
        db.close()

    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="video-analysis-queue", daemon=True)
    _thread.start()
    enqueue_video_analysis()


def stop_video_analysis_worker() -> None:
    _stop.set()
    notify_video_analysis_queue()
