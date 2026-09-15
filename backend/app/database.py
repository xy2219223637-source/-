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


def _table_columns(connection, table_name: str) -> set[str]:
    inspector = inspect(connection)
    try:
        tables = set(inspector.get_table_names())
    except Exception:
        return set()
    if table_name not in tables:
        return set()
    try:
        return {column["name"] for column in inspector.get_columns(table_name)}
    except Exception:
        return set()


def _json_column_sql(connection) -> str:
    if connection.dialect.name == "postgresql":
        return "JSONB NOT NULL DEFAULT '[]'::jsonb"
    return "JSON NOT NULL DEFAULT '[]'"


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # Lightweight compatibility migration for this PoC. Existing installations
    # predate the product-level video library, so create_all alone cannot relax
    # the old project_id constraint or add the new bookkeeping columns.
    # inspect must run after create_all, and must tolerate missing legacy tables.
    with engine.begin() as connection:
        json_default = _json_column_sql(connection)
        columns = _table_columns(connection, "video_assets")
        if columns:
            if connection.dialect.name == "postgresql" and "project_id" in columns:
                try:
                    connection.execute(text("ALTER TABLE video_assets ALTER COLUMN project_id DROP NOT NULL"))
                except Exception:
                    pass
            additions = {
                "category_id": "INTEGER NULL",
                "excluded": "BOOLEAN NOT NULL DEFAULT FALSE",
                "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "ai_tags": json_default,
                "manual_tags": json_default,
                "removed_ai_tags": json_default,
                "library_type": "VARCHAR(32) NOT NULL DEFAULT 'semi_finished'",
                "asset_kind": "VARCHAR(32) NOT NULL DEFAULT 'material'",
                "generation_depth": "INTEGER NOT NULL DEFAULT 0",
                "origin_asset_ids": json_default,
                "reusable_status": "VARCHAR(32) NOT NULL DEFAULT 'review_required'",
                "burned_subtitles": "BOOLEAN NOT NULL DEFAULT FALSE",
                "has_background_music": "BOOLEAN NOT NULL DEFAULT FALSE",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE video_assets ADD COLUMN {name} {definition}"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_video_assets_category_id ON video_assets (category_id)"))
            connection.execute(text("""UPDATE video_assets SET asset_kind = 'final_output'
                WHERE source IN ('smart_compose_output', 'finished_remix_output')"""))
        analysis_columns = _table_columns(connection, "video_asset_analyses")
        analysis_additions = {
            "input_video_seconds": "FLOAT NOT NULL DEFAULT 0",
            "request_count": "INTEGER NOT NULL DEFAULT 0",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "error_message": "TEXT NOT NULL DEFAULT ''",
            "completed_at": "TIMESTAMP NULL",
        }
        for name, definition in analysis_additions.items():
            if name not in analysis_columns:
                connection.execute(text(f"ALTER TABLE video_asset_analyses ADD COLUMN {name} {definition}"))
        project_columns = _table_columns(connection, "video_edit_projects")
        if project_columns and "edit_mode" not in project_columns:
            connection.execute(text("ALTER TABLE video_edit_projects ADD COLUMN edit_mode VARCHAR(32) NOT NULL DEFAULT 'smart_compose'"))
        upload_columns = _table_columns(connection, "video_upload_sessions")
        if upload_columns and "library_type" not in upload_columns:
            connection.execute(text("ALTER TABLE video_upload_sessions ADD COLUMN library_type VARCHAR(32) NOT NULL DEFAULT 'semi_finished'"))
        if upload_columns and "video_asset_id" not in upload_columns:
            connection.execute(text("ALTER TABLE video_upload_sessions ADD COLUMN video_asset_id INTEGER NULL"))
        highlight_columns = _table_columns(connection, "video_highlights")
        highlight_additions = {
            "content_role": "VARCHAR(64) NOT NULL DEFAULT 'core_content'",
            "track_role": "VARCHAR(16) NOT NULL DEFAULT 'b_roll'",
            "speech_present": "BOOLEAN NOT NULL DEFAULT FALSE",
            "lip_visible": "BOOLEAN NOT NULL DEFAULT FALSE",
            "speech_text": "TEXT NOT NULL DEFAULT ''",
            "speaker_id": "VARCHAR(96) NOT NULL DEFAULT ''",
            "audio_strategy": "VARCHAR(32) NOT NULL DEFAULT 'mute'",
            "shot_type": "VARCHAR(64) NOT NULL DEFAULT 'unknown'",
            "selling_points": json_default,
        }
        for name, definition in highlight_additions.items():
            if name not in highlight_columns:
                connection.execute(text(f"ALTER TABLE video_highlights ADD COLUMN {name} {definition}"))
        link_columns = _table_columns(connection, "video_edit_project_assets")
        if columns and link_columns:
            # Preserve assets uploaded by the first PoC implementation.
            connection.execute(text("""INSERT INTO video_edit_project_assets (tenant_id, project_id, asset_id, included, created_at)
                SELECT tenant_id, project_id, id, TRUE, CURRENT_TIMESTAMP FROM video_assets
                WHERE project_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM video_edit_project_assets link
                    WHERE link.project_id = video_assets.project_id AND link.asset_id = video_assets.id)"""))
    migrate_legacy_schema(engine)
