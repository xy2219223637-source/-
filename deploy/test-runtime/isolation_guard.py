"""Fail closed against a separately mounted, provisioned test resource inventory."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url


INTEGRATION_FIELDS = (
    "llm_api_key", "llm_api_base", "image_generation_api_key", "image_generation_base_url",
    "ark_api_key", "ark_base_url", "embedding_api_key", "embedding_base_url",
    "director_asr_api_key", "director_asr_api_base", "doubao_tts_app_id",
    "doubao_tts_access_token", "feishu_app_id", "feishu_app_secret", "approval_webhook_url",
    "volc_billing_api_url", "volc_billing_api_token", "volc_billing_account_id",
    "private_video_tenants", "private_video_s3_env_file", "private_video_s3_endpoint",
    "private_video_s3_public_endpoint", "private_video_s3_access_key_id",
    "private_video_s3_access_key_secret", "private_video_s3_bucket_name", "auth_secret",
)


class IsolationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IsolationError(message)


def is_test_environment(settings) -> bool:
    return (getattr(settings, "deployment_environment", "production") == "test"
            or bool(getattr(settings, "test_resource_manifest", ""))
            or getattr(settings, "app_public_base_url", "") == "https://test.waynepartners.xyz")


def uses_admin_runtime(resources: dict) -> bool:
    database = resources["database"]
    return (database.get("allow_admin_runtime") is True
            and database["app_user"] == "postgres")


def load_resources(settings) -> dict:
    path = getattr(settings, "test_resource_manifest", "")
    require(bool(path), "TEST_RESOURCE_MANIFEST is required")
    try:
        resources = json.loads(Path(path).read_text(encoding="utf-8"))
        require(resources["version"] == 1, "Unsupported test resource inventory")
        require(resources["database"]["host"] == "postgres-test", "Invalid test database host")
        require(resources["database"]["name"] == "enrui_ai_commerce_agent_test", "Invalid test database name")
        require(resources["database"]["app_user"] == "enrui_test_app"
                or uses_admin_runtime(resources), "Invalid test runtime role")
        require(resources["database"]["migration_user"] == "enrui_test_migrator", "Invalid test migration role")
        require(str(resources["database"]["system_identifier"]).isdigit(), "Missing PostgreSQL instance identity")
        require(resources["redis"]["host"] == "redis-test", "Invalid test Redis host")
        require(bool(resources["redis"]["instance_id"]), "Missing Redis instance identity")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IsolationError("Test resource inventory is missing or incomplete") from exc
    return resources


def check_database(settings, engine, resources: dict, *, migration: bool = False) -> dict:
    expected = resources["database"]
    url = make_url(settings.database_url)
    role = expected["migration_user" if migration else "app_user"]
    require(url.get_backend_name() == "postgresql", "Test database must be PostgreSQL")
    require(url.host == expected["host"] and (url.port or 5432) == 5432
            and url.database == expected["name"] and url.username == role
            and bool(url.password) and not url.query,
            "Database URL does not match provisioned test resources")
    require(engine.url == url, "Database engine differs from checked configuration")
    require(settings.require_postgres and not settings.run_database_init,
            "Test runtime must disable automatic schema initialization")
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        identity = connection.execute(text("""
            SELECT current_database() AS database, current_user AS username,
                   (pg_control_system()).system_identifier::text AS system_identifier
        """)).mappings().one()
        require(identity["database"] == expected["name"] and identity["username"] == role
                and identity["system_identifier"] == str(expected["system_identifier"]),
                "Connected PostgreSQL instance or role differs from test inventory")
        if not migration and uses_admin_runtime(resources):
            # Explicit server inventory approval applies only to test postgres.
            superuser = connection.execute(text(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            )).scalar_one()
            require(superuser, "Approved test admin runtime must use a superuser")
            return dict(identity)
        flags = connection.execute(text("""
            SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
            FROM pg_roles WHERE rolname = current_user
        """)).scalar_one()
        require(not flags, "Test database role has administrative privileges")
        memberships = connection.execute(text("""
            SELECT rolname FROM pg_roles
            WHERE rolname <> current_user AND pg_has_role(current_user, oid, 'MEMBER')
        """)).scalars().all()
        require(not memberships, "Test database role must not inherit other roles")
        create_schema = connection.execute(text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")).scalar_one()
        create_database_objects = connection.execute(text("""
            SELECT has_database_privilege(current_user, current_database(), 'CREATE')
                OR has_database_privilege(current_user, current_database(), 'TEMP')
        """)).scalar_one()
        require(not create_database_objects, "Test role must not create schemas or temporary objects")
        require(bool(create_schema) == migration, "Test schema privileges do not match role purpose")
    return dict(identity)


def check_schema(engine, metadata, *, allow_admin_runtime: bool = False) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names(schema="public"))
    require(set(metadata.tables) <= tables, "Test database schema has missing tables; run explicit migration")
    for name, table in metadata.tables.items():
        actual = {column["name"] for column in inspector.get_columns(name, schema="public")}
        require(set(table.columns.keys()) <= actual, "Test database schema has missing columns")
    with engine.connect() as connection:
        missing = connection.execute(text("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND NOT (has_table_privilege(current_user, c.oid, 'SELECT')
                   AND has_table_privilege(current_user, c.oid, 'INSERT')
                   AND has_table_privilege(current_user, c.oid, 'UPDATE')
                   AND has_table_privilege(current_user, c.oid, 'DELETE'))
        """)).scalars().all()
        sequences = connection.execute(text("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'S'
              AND NOT has_sequence_privilege(current_user, c.oid, 'USAGE')
        """)).scalars().all()
        require(not missing and not sequences, "Test runtime is missing table or sequence permissions")
        if allow_admin_runtime:
            return
        excessive = connection.execute(text("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S')
              AND (c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                   OR CASE WHEN c.relkind IN ('r', 'p') THEN
                       has_table_privilege(current_user, c.oid, 'TRUNCATE')
                       OR has_table_privilege(current_user, c.oid, 'TRIGGER')
                       ELSE false END)
        """)).scalars().all()
        require(not excessive, "Test runtime owns schema objects or has destructive table privileges")


def check_services(settings, resources, redis_client, storage) -> dict:
    from app.services.storage import AliyunOSSProvider

    require(settings.require_online_services, "Test environment requires online dependencies")
    require(settings.redis_key_prefix == "enrui-ai-commerce-agent:test:"
            and settings.aliyun_oss_prefix == "enrui-ai-commerce-agent/test/",
            "Test resource prefixes are invalid")
    require(settings.app_public_base_url == "https://test.waynepartners.xyz"
            and len(settings.auth_secret) >= 32,
            "Test public URL or signing secret is invalid")
    expected = resources["redis"]
    url = urlsplit(settings.redis_url)
    require(url.scheme == "redis" and url.hostname == expected["host"]
            and (url.port or 6379) == 6379 and url.path == "/0" and bool(url.password)
            and not url.query and not url.fragment and not url.username,
            "Redis URL does not match provisioned test resources")
    require(redis_client is not None and redis_client.ping(), "Test Redis is unavailable")
    value = redis_client.get("enrui-ai-commerce-agent:test:isolation-id")
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    require(value == expected["instance_id"], "Connected Redis differs from test inventory")
    expected_oss = resources.get("oss", {})
    require(bool(expected_oss.get("bucket")) and bool(expected_oss.get("access_key_id")),
            "Test OSS has not been provisioned")
    require(settings.storage_provider == "aliyun_oss"
            and settings.aliyun_oss_bucket_name == expected_oss["bucket"]
            and settings.aliyun_oss_access_key_id == expected_oss["access_key_id"]
            and settings.aliyun_oss_endpoint == expected_oss["endpoint"]
            and settings.aliyun_oss_region == expected_oss["region"],
            "OSS configuration differs from test inventory")
    require(isinstance(storage.provider, AliyunOSSProvider), "Test storage must use OSS")
    info = storage.provider.bucket.get_bucket_info()
    require(info.name == expected_oss["bucket"] and info.acl.grant == "private",
            "Test OSS bucket identity or private ACL is invalid")
    # Integrations are explicitly approved in the separately managed inventory.
    # Fingerprints pin credentials and destinations without copying secrets into it.
    import hashlib

    approved = resources.get("integration_fingerprints", {})
    for field in INTEGRATION_FIELDS:
        value = getattr(settings, field)
        if value:
            require(approved.get(field) == hashlib.sha256(value.encode()).hexdigest(),
                    f"Unapproved test integration configuration: {field}")
    require(not settings.llm_mock_mode and bool(settings.llm_api_key),
            "Usable test environment requires a real test LLM credential")
    if settings.private_video_s3_env_file:
        require(resources.get("private_video_config_sha256") == hashlib.sha256(
            Path(settings.private_video_s3_env_file).read_bytes()).hexdigest(),
            "Private video credentials file differs from test inventory")
    return {"redis": "ok", "oss": expected_oss["bucket"]}


def verify_test_environment(settings, engine, *, verify_schema: bool = True) -> dict:
    from app.database import Base
    from app import models  # noqa: F401
    from app.services.redis_client import get_redis
    from app.services.storage import get_storage

    resources = load_resources(settings)
    identity = check_database(settings, engine, resources)
    services = check_services(settings, resources, get_redis(), get_storage())
    if verify_schema:
        check_schema(engine, Base.metadata, allow_admin_runtime=uses_admin_runtime(resources))
    return {"database": identity, **services}
