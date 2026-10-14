"""Durable video-analysis worker backed by database state and Redis wakeups."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import threading
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import VideoAsset, VideoAssetAnalysis, EcommerceVideoAsset, EcommerceVideoAssetAnalysis
from app.services.redis_client import (
    notify_video_analysis_queue,
    task_lock,
    wait_for_video_analysis_queue,
)

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
logger = logging.getLogger(__name__)
ECOMMERCE_LEASE_SECONDS = 120
ECOMMERCE_HEARTBEAT_SECONDS = 30


def _ecommerce_lock(task_id: str):
    return task_lock(task_id, timeout=ECOMMERCE_LEASE_SECONDS, thread_local=False)


def _run_ecommerce_task(asset_id: int, task: str, callback: Callable[[int], None]) -> None:
    lock = _ecommerce_lock(f"ecommerce-video-{task}-{asset_id}")
    if lock is not None and not lock.acquire(blocking=False):
        _stop.wait(1)
        return
    finished = threading.Event()

    def heartbeat() -> None:
        while not finished.wait(ECOMMERCE_HEARTBEAT_SECONDS):
            try:
                if lock is not None:
                    lock.extend(ECOMMERCE_LEASE_SECONDS, replace_ttl=True)
                with SessionLocal() as db:
                    db.query(EcommerceVideoAsset).filter(
                        EcommerceVideoAsset.id == asset_id,
                        EcommerceVideoAsset.status.in_(("analyzing", "validating")),
                    ).update({EcommerceVideoAsset.updated_at: datetime.utcnow()}, synchronize_session=False)
                    db.commit()
            except Exception:
                logger.exception("Ecommerce video task %s heartbeat failed", asset_id)
                if lock is not None:
                    return

    renewal = threading.Thread(target=heartbeat, name=f"ecommerce-video-heartbeat-{asset_id}", daemon=True)
    renewal.start()
    try:
        callback(asset_id)
    finally:
        finished.set()
        renewal.join(timeout=5)
        if lock is not None:
            try:
                lock.release()
            except Exception:
                logger.warning("Ecommerce video task %s lease was lost before release", asset_id)


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


def _next_ecommerce_asset_id() -> Optional[int]:
    db = SessionLocal()
    try:
        assets = db.query(EcommerceVideoAsset).filter(
            EcommerceVideoAsset.asset_kind == "material",
            EcommerceVideoAsset.status == "queued_for_analysis",
            EcommerceVideoAsset.asset_kind == "material",
            EcommerceVideoAsset.excluded.is_(False),
        ).order_by(EcommerceVideoAsset.created_at, EcommerceVideoAsset.id)
        for asset in assets.yield_per(100):
            lock = _ecommerce_lock(f"ecommerce-video-analysis-{asset.id}")
            if lock is None or not lock.locked():
                return asset.id
        return None
    finally:
        db.close()


def _run_ecommerce_asset(asset_id: int) -> None:
    from app.api.ecommerce_video_library import analyze_video_asset
    asyncio.run(analyze_video_asset(asset_id, force=True, queued_only=True))


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


def _next_ecommerce_validation_id() -> Optional[int]:
    db = SessionLocal()
    try:
        assets = db.query(EcommerceVideoAsset).filter(EcommerceVideoAsset.status == "validating").order_by(
            EcommerceVideoAsset.created_at, EcommerceVideoAsset.id)
        for asset in assets.yield_per(100):
            lock = _ecommerce_lock(f"ecommerce-video-validation-{asset.id}")
            if lock is None or not lock.locked():
                return asset.id
        return None
    finally:
        db.close()


def _run_ecommerce_validation(asset_id: int) -> None:
    from app.api.ecommerce_video_library import validate_video_asset
    validate_video_asset(asset_id)


def _loop(*, include_legacy: bool = True, include_ecommerce: bool = True) -> None:
    next_recovery = datetime.utcnow() + timedelta(seconds=ECOMMERCE_HEARTBEAT_SECONDS)
    while not _stop.is_set():
        try:
            if include_ecommerce and datetime.utcnow() >= next_recovery:
                with SessionLocal() as db:
                    recover_interrupted_ecommerce_video_analyses(db)
                next_recovery = datetime.utcnow() + timedelta(seconds=ECOMMERCE_HEARTBEAT_SECONDS)
            validation_id = _next_validation_id() if include_legacy else None
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
            ecommerce_validation_id = _next_ecommerce_validation_id() if include_ecommerce else None
            if ecommerce_validation_id is not None:
                _run_ecommerce_task(ecommerce_validation_id, "validation", _run_ecommerce_validation)
                continue
            asset_id = _next_asset_id() if include_legacy else None
            ecommerce_asset_id = _next_ecommerce_asset_id() if include_ecommerce and asset_id is None else None
            if asset_id is None and ecommerce_asset_id is None:
                wait_for_video_analysis_queue(1)
                _stop.wait(.1)
                continue

            if ecommerce_asset_id is not None:
                _run_ecommerce_task(ecommerce_asset_id, "analysis", _run_ecommerce_asset)
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
            _stop.wait(1)


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


def recover_interrupted_ecommerce_video_analyses(db: Session) -> int:
    """Recover abandoned jobs without interrupting another instance's live task."""
    candidates = db.query(EcommerceVideoAsset.id).filter(
        EcommerceVideoAsset.asset_kind == "material", EcommerceVideoAsset.status == "analyzing",
    ).all()
    db.commit()
    recovered = 0
    for (asset_id,) in candidates:
        lock = _ecommerce_lock(f"ecommerce-video-analysis-{asset_id}")
        if lock is not None and not lock.acquire(blocking=False):
            continue
        try:
            asset = db.query(EcommerceVideoAsset).filter(EcommerceVideoAsset.id == asset_id).with_for_update().first()
            if not asset or asset.asset_kind != "material" or asset.status != "analyzing":
                db.rollback()
                continue
            # Without Redis, a fresh database heartbeat also proves task ownership.
            cutoff = datetime.utcnow() - timedelta(seconds=ECOMMERCE_LEASE_SECONDS)
            if lock is None and asset.updated_at > cutoff:
                db.rollback()
                continue
            db.query(EcommerceVideoAssetAnalysis).filter(
                EcommerceVideoAssetAnalysis.asset_id == asset.id,
                EcommerceVideoAssetAnalysis.status == "analyzing",
            ).update({
                EcommerceVideoAssetAnalysis.status: "interrupted",
                EcommerceVideoAssetAnalysis.error_message: "分析进程已中断, 任务已自动重新排队",
                EcommerceVideoAssetAnalysis.completed_at: datetime.utcnow(),
            }, synchronize_session=False)
            asset.status = "queued_for_analysis"
            db.commit()
            recovered += 1
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception:
                    logger.warning("Recovery lease for ecommerce video %s was lost", asset_id)
    return recovered


def start_video_analysis_worker(*, include_legacy: bool = True, include_ecommerce: bool = True) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    if not include_legacy and not include_ecommerce:
        return
    db = SessionLocal()
    try:
        # Shared databases may still contain tasks owned by another live instance.
        if include_legacy:
            recover_interrupted_video_analyses(db)
        if include_ecommerce:
            recover_interrupted_ecommerce_video_analyses(db)
    finally:
        db.close()

    _stop.clear()
    _thread = threading.Thread(target=_loop, name="video-analysis-queue", daemon=True,
                               kwargs={"include_legacy": include_legacy, "include_ecommerce": include_ecommerce})
    _thread.start()
    enqueue_video_analysis()


def stop_video_analysis_worker() -> None:
    _stop.set()
    notify_video_analysis_queue()
