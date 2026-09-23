"""Local real OAuth integration: real auth/DB/API, with only the Qianchuan worker.

Run from backend: uv run --no-project --python .venv/Scripts/python.exe python scripts/qianchuan_auth_live.py
Existing business schema must already be present. No general migrations or media jobs run.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from app.main import app
from app.database import Base, engine
from app.models import QianchuanConnection, QianchuanOAuthSession, QianchuanAccountBinding, QianchuanUpload
from app.services.qianchuan.service import get_qianchuan_service
from app.services.qianchuan.worker import start_qianchuan_worker, stop_qianchuan_worker


@asynccontextmanager
async def live_lifespan(_app):
    service = get_qianchuan_service()
    service.require_ready()
    Base.metadata.create_all(engine, tables=[QianchuanConnection.__table__,
        QianchuanOAuthSession.__table__, QianchuanAccountBinding.__table__, QianchuanUpload.__table__])
    start_qianchuan_worker()
    try:
        yield
    finally:
        stop_qianchuan_worker()


if __name__ == "__main__":
    app.router.lifespan_context = live_lifespan
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)
