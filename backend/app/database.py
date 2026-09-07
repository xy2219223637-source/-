from __future__ import annotations

from collections.abc import Generator
import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

if settings.require_postgres and not settings.database_url.startswith("postgresql"):
    raise RuntimeError("REQUIRE_POSTGRES=true，但 DATABASE_URL 不是 PostgreSQL，拒绝启动")

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_recycle=settings.database_pool_recycle_seconds,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def migrate_legacy_schema(bind: Engine) -> None:
    """Apply idempotent schema removals for deployments created before migrations existed."""
    inspector = inspect(bind)
    if "director_shots" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("director_shots")}
    if "onscreen_text" not in columns:
        return
    if bind.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError(f"不支持自动迁移的数据库类型: {bind.dialect.name}")
    with bind.begin() as connection:
        connection.execute(text("ALTER TABLE director_shots DROP COLUMN onscreen_text"))
    logger.info("Removed legacy director_shots.onscreen_text column")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    migrate_legacy_schema(engine)
