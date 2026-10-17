"""Idempotently initialize and verify the deployment database schema."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from app.database import Base, engine, init_db
from app.config import get_settings
from app.services.redis_client import get_redis
from app.services.storage import AliyunOSSProvider, get_storage


def main() -> int:
    if engine.dialect.name != "postgresql":
        print(f"Deployment database must be PostgreSQL, got {engine.dialect.name}", file=sys.stderr)
        return 2

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    init_db()
    expected = {table.name for table in Base.metadata.tables.values() if not table.info.get("explicit_migration_required")}
    if get_settings().billing_enabled:
        from app.services.billing.migration import verify
        with engine.connect() as connection:
            verify(connection)
    actual = set(inspect(engine).get_table_names())
    missing = sorted(expected - actual)
    if missing:
        print("Missing tables: " + ", ".join(missing), file=sys.stderr)
        return 1

    print(f"PostgreSQL schema ready: {len(expected)} application tables")
    settings = get_settings()
    if settings.require_online_services:
        redis_client = get_redis()
        if redis_client is None or not redis_client.ping():
            print("Redis is required but unavailable", file=sys.stderr)
            return 1
        storage = get_storage()
        if not isinstance(storage.provider, AliyunOSSProvider):
            print("Aliyun OSS is required but not configured as primary storage", file=sys.stderr)
            return 1
        storage.provider.bucket.get_bucket_info()
        print("Redis ready: queue wakeups and distributed locks")
        print("Aliyun OSS ready: primary persistent file storage")
    print("Required seed data: none (tenant owner is created through /api/auth/register)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
