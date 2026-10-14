"""Shared Redis connection and namespaced queue notifications."""
from __future__ import annotations

from functools import lru_cache

from app.config import get_settings


@lru_cache
def get_redis():
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError("使用 Redis 前请安装 redis 依赖") from exc
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
        health_check_interval=30,
    )


def redis_key(name: str) -> str:
    return f"{get_settings().redis_key_prefix}{name}"


def notify_queue() -> None:
    client = get_redis()
    if client is not None:
        try:
            client.lpush(redis_key("production-queue:wake"), "1")
            client.ltrim(redis_key("production-queue:wake"), 0, 999)
        except Exception:
            pass


def wait_for_queue(timeout: int = 1) -> None:
    client = get_redis()
    if client is not None:
        try:
            client.brpop(redis_key("production-queue:wake"), timeout=timeout)
        except Exception:
            pass


def notify_video_analysis_queue() -> None:
    client = get_redis()
    if client is not None:
        try:
            client.lpush(redis_key("video-analysis:wake"), "1")
            client.ltrim(redis_key("video-analysis:wake"), 0, 999)
        except Exception:
            pass


def wait_for_video_analysis_queue(timeout: int = 1) -> None:
    client = get_redis()
    if client is not None:
        try:
            client.brpop(redis_key("video-analysis:wake"), timeout=timeout)
        except Exception:
            pass


def task_lock(task_id: str, *, timeout: int = 14400, thread_local: bool = True):
    client = get_redis()
    return client.lock(redis_key(f"production-task-lock:{task_id}"), timeout=timeout,
                       blocking_timeout=0, thread_local=thread_local) if client is not None else None
