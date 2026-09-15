from __future__ import annotations

import logging
import threading

from app.models import QianchuanConnection
from .service import get_qianchuan_service
from .uploads import UploadService

_thread: threading.Thread | None = None
_stop = threading.Event()
logger = logging.getLogger(__name__)


def _loop(service) -> None:
    uploads = UploadService(service)
    while not _stop.is_set():
        try:
            with service.session_factory() as db:
                row = db.query(QianchuanConnection).filter_by(status="sync_pending").order_by(
                    QianchuanConnection.created_at).first()
                connection_id = row.id if row else None
            if connection_id:
                service.sync_connection(connection_id)
            if uploads.run_next():
                continue
        except Exception:
            # SQL/HTTP exceptions may contain credentials; expose only a fixed diagnostic.
            logger.error("千川任务处理暂不可用，将稍后重试")
        _stop.wait(2)


def start_qianchuan_worker(service=None) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    service = service or get_qianchuan_service()
    if service.configuration()["status"] != "ready":
        return
    service.recover()
    UploadService(service).recover()
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(service,), name="qianchuan-worker", daemon=True)
    _thread.start()


def stop_qianchuan_worker() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=12)
