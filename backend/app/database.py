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


def migrate_feishu_notifications(bind: Engine) -> None:
    """Keep old simulated assignments unsent; nullable UUIDs allow legacy rows."""
    with bind.begin() as connection:
        columns = _table_columns(connection, "shooting_tasks")
        if not columns:
            return
        json_type = "JSONB NOT NULL DEFAULT '{}'::jsonb" if bind.dialect.name == "postgresql" else "JSON NOT NULL DEFAULT '{}'"
        additions = {
            "recipient_type": "VARCHAR(16) NOT NULL DEFAULT ''",
            "recipient_id": "VARCHAR(128) NOT NULL DEFAULT ''",
            "recipient_name": "VARCHAR(256) NOT NULL DEFAULT ''",
            "notification_status": "VARCHAR(16) NOT NULL DEFAULT 'not_sent'",
            "notification_uuid": "VARCHAR(36) NULL",
            "notification_app_id": "VARCHAR(128) NOT NULL DEFAULT ''",
            "notification_payload": json_type,
            "feishu_message_id": "VARCHAR(128) NOT NULL DEFAULT ''",
            "notification_error": "TEXT NOT NULL DEFAULT ''",
            "notification_started_at": "TIMESTAMP NULL",
            "notified_at": "TIMESTAMP NULL",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE shooting_tasks ADD COLUMN {name} {definition}"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_shooting_notification_uuid ON shooting_tasks (tenant_id, notification_uuid)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_shooting_active_recipient ON shooting_tasks (tenant_id, project_id, recipient_type, recipient_id) WHERE recipient_id <> '' AND status IN ('pending', 'in_progress')"))


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
        user_columns = _table_columns(connection, "users")
        user_additions = {
            "phone": "VARCHAR(20) NULL",
            "is_platform_admin": "BOOLEAN NOT NULL DEFAULT FALSE",
            "must_change_password": "BOOLEAN NOT NULL DEFAULT FALSE",
            "job_title": "VARCHAR(120) NOT NULL DEFAULT ''",
            "employee_no": "VARCHAR(100) NOT NULL DEFAULT ''",
        }
        for name, definition in user_additions.items():
            if name not in user_columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))
        if user_columns:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_phone ON users (phone) WHERE phone IS NOT NULL"))
        member_columns = _table_columns(connection, "tenant_members")
        for name in ("team_id", "group_id", "role_id"):
            if member_columns and name not in member_columns:
                connection.execute(text(f"ALTER TABLE tenant_members ADD COLUMN {name} INTEGER NULL"))
                connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_tenant_members_{name} ON tenant_members ({name})"))
        # Preserve the original user's identity and tenant ownership while switching
        # the login identifier to a phone number. This migration is idempotent.
        if user_columns:
            connection.execute(text("""UPDATE users SET phone = '15271933010', is_platform_admin = TRUE
                WHERE lower(email) = '345983950@qq.com' AND (phone IS NULL OR phone = '15271933010')
                AND NOT EXISTS (SELECT 1 FROM users other WHERE other.phone = '15271933010' AND other.id <> users.id)"""))
            connection.execute(text("""UPDATE tenant_members SET role = 'owner', status = 'active'
                WHERE user_id IN (SELECT id FROM users WHERE lower(email) = '345983950@qq.com')"""))
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
                "storage_provider": "VARCHAR(32) NOT NULL DEFAULT 'aliyun_oss'",
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
        if upload_columns and "storage_provider" not in upload_columns:
            connection.execute(text("ALTER TABLE video_upload_sessions ADD COLUMN storage_provider VARCHAR(32) NOT NULL DEFAULT 'aliyun_oss'"))
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
    migrate_feishu_notifications(engine)
    migrate_director_edit_time(engine)
    _ensure_default_tenant_roles()


def migrate_director_edit_time(bind: Engine) -> None:
    """Preserve unknown edit times on existing projects; new projects opt in via ORM."""
    with bind.begin() as connection:
        columns = _table_columns(connection, "director_projects")
        if not columns:
            return
        if "last_edited_at" not in columns:
            connection.execute(text("ALTER TABLE director_projects ADD COLUMN last_edited_at TIMESTAMP NULL"))
        if "edit_time_tracked" not in columns:
            connection.execute(text("ALTER TABLE director_projects ADD COLUMN edit_time_tracked BOOLEAN NOT NULL DEFAULT FALSE"))


def _ensure_default_tenant_roles() -> None:
    from app.models import Tenant, TenantMember, TenantRole
    from app.permissions import ALL_ADMIN_PERMISSIONS, ALL_USER_PERMISSIONS

    db = SessionLocal()
    try:
        for tenant in db.query(Tenant).all():
            employee = db.query(TenantRole).filter_by(tenant_id=tenant.id, code="employee", status="active").first()
            if not employee:
                employee = TenantRole(tenant_id=tenant.id, name="普通员工", code="employee", permissions=ALL_USER_PERMISSIONS, is_system=True)
                db.add(employee); db.flush()
            administrator = db.query(TenantRole).filter_by(tenant_id=tenant.id, code="administrator", status="active").first()
            if not administrator:
                administrator = TenantRole(tenant_id=tenant.id, name="企业管理员", code="administrator",
                                           permissions=ALL_USER_PERMISSIONS + ALL_ADMIN_PERMISSIONS, is_system=True)
                db.add(administrator); db.flush()
            for member in db.query(TenantMember).filter_by(tenant_id=tenant.id).all():
                if member.role_id is None and member.role != "owner":
                    member.role_id = administrator.id if member.role == "admin" else employee.id
        db.commit()
    finally:
        db.close()
