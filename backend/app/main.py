from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api import admin, auth, auto_edit, brands, creative, dashboard, director, ecommerce_products, ecommerce_video_library, ecommerce_video_taxonomy, knowledge, products, quality, skills, templates, operations, production, storage, video_library, video_taxonomy, video_refinement, videos
from app.config import get_settings
from app.database import SessionLocal, engine, init_db
from app.models import AuditLog, CreativeBatchJob, CreativeGeneration
from app.services.production_queue import start_worker, stop_worker
from app.services.video_analysis_queue import start_video_analysis_worker, stop_video_analysis_worker
from app.services.refinement_queue import start_refinement_worker, stop_refinement_worker, refinement_worker_state
from app.services.redis_client import get_redis
from app.services.feishu_logging import RequestTraceMiddleware, trace_fields
from app.api import qianchuan, qianchuan_plans, video_repair
from app.api import video_lineage
from app.services.qianchuan.service import get_qianchuan_service, install_log_filters
from app.services.qianchuan.worker import start_qianchuan_worker, stop_qianchuan_worker
from app.services.video_repair.worker_v4 import start_video_repair_worker, stop_video_repair_worker

install_log_filters()

settings = get_settings()

app = FastAPI(
    title="Enrui AI Commerce Agent",
    description="企业级 AI 商品详情页生成助手 — 多模态 Agent Workflow + RAG",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Notification-Outcome"],
)


def write_request_audit(request_id, method, path, status_code, duration_ms):
    db = SessionLocal()
    try:
        db.add(AuditLog(request_id=request_id, method=method, path=path, status_code=status_code, duration_ms=duration_ms))
        db.commit()
    finally:
        db.close()


@app.middleware("http")
async def audit_requests(request, call_next):
    request_id = trace_fields()["request_id"]
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id
    if request.url.path.startswith("/api"):
        # 等待数据库连接时不能阻塞事件循环，否则其他请求无法完成并归还连接。
        await run_in_threadpool(write_request_audit, request_id, request.method, request.url.path, response.status_code, duration_ms)
    return response

app.add_middleware(RequestTraceMiddleware)

app.include_router(products.router, prefix="/api")
app.include_router(ecommerce_products.router, prefix="/api")
app.include_router(ecommerce_video_library.router, prefix="/api")
app.include_router(ecommerce_video_taxonomy.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(admin.platform_router, prefix="/api")
app.include_router(admin.tenant_router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(skills.router, prefix="/api")
app.include_router(brands.router, prefix="/api")
app.include_router(creative.router, prefix="/api")
app.include_router(templates.router, prefix="/api")
app.include_router(operations.router, prefix="/api")
app.include_router(production.router, prefix="/api")
app.include_router(quality.router, prefix="/api")
app.include_router(storage.router, prefix="/api")
app.include_router(videos.router, prefix="/api")
app.include_router(director.router, prefix="/api")
app.include_router(auto_edit.router, prefix="/api")
app.include_router(qianchuan.router, prefix="/api")
app.include_router(qianchuan_plans.router, prefix="/api")
app.include_router(video_library.router, prefix="/api")
app.include_router(video_refinement.router, prefix="/api")
app.include_router(video_taxonomy.router, prefix="/api")
app.include_router(video_lineage.router, prefix="/api")
app.include_router(video_repair.router, prefix="/api")

@app.on_event("startup")
def on_startup() -> None:
    if settings.run_database_init:
        init_db()
    if settings.run_startup_recovery:
        recover_startup_tasks()
    if settings.run_background_workers:
        start_worker()
        start_qianchuan_worker()
        start_video_repair_worker()
    if settings.run_background_workers or settings.ecommerce_video_analysis_worker_enabled:
        start_video_analysis_worker(
            include_legacy=settings.run_background_workers,
            include_ecommerce=settings.ecommerce_video_analysis_worker_enabled,
        )
    if settings.refinement_worker_enabled:
        start_refinement_worker()
    else:
        logging.getLogger(__name__).warning("Refinement worker disabled; refinement generation requests will return 503")


def recover_startup_tasks() -> None:
    db = SessionLocal()
    try:
        interrupted_batches = db.query(CreativeBatchJob).filter(CreativeBatchJob.status.in_(["pending", "running"])).all()
        for batch in interrupted_batches:
            batch.status = "interrupted"
            batch.current_module_id = None
        stale_before = datetime.utcnow() - timedelta(minutes=30)
        interrupted_creative = db.query(CreativeGeneration).filter(
            CreativeGeneration.status == "running",
            CreativeGeneration.updated_at < stale_before,
        ).all()
        for generation in interrupted_creative:
            generation.status = "failed"
            generation.error_message = "任务超过 30 分钟未更新，已标记为中断，可点击重试"
            generation.context_snapshot = {
                **(generation.context_snapshot or {}),
                "diagnostic": {
                    "code": "interrupted",
                    "title": "生成任务已中断",
                    "suggestion": "进入项目重新生成该页面",
                    "retryable": True,
                },
            }
        db.commit()
    finally:
        db.close()

@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_worker()
    stop_video_analysis_worker()
    stop_refinement_worker()
    stop_qianchuan_worker()
    stop_video_repair_worker()


@app.get("/api/health")
def health() -> dict:
    key = settings.llm_api_key or ""
    configured = bool(key) and not key.startswith("sk-your-")
    redis_client = get_redis()
    redis_status = "not_configured"
    if redis_client is not None:
        try:
            redis_status = "ok" if redis_client.ping() else "error"
        except Exception:
            redis_status = "error"
    return {
        "status": "ok",
        "llm_model": settings.llm_model,
        "llm_configured": configured,
        "llm_mock_mode": settings.llm_mock_mode,
        "embedding_model": settings.embedding_model,
        "embedding_mode": settings.embedding_mode,
        "database": engine.dialect.name,
        "redis": redis_status,
        "storage": settings.storage_provider,
        "refinement_worker": refinement_worker_state(),
        "qianchuan": get_qianchuan_service().configuration(),
    }
