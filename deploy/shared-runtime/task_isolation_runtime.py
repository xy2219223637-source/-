"""Run each deployment's branch workers against service-owned durable tasks."""
from __future__ import annotations

import ast
import hashlib
import importlib.abc
import importlib.machinery
import os
from pathlib import Path
import sys

SERVICE = os.environ.get("DEPLOYMENT_SERVICE_ID", "")
SERVICES = {"production", "artinjecter"}
OWNER = "deployment_service"
MODEL_NAMES = {
    "ProductionQueueTask", "CreativeBatchJob", "CreativeGeneration",
    "VideoAsset", "VideoAssetAnalysis", "QianchuanConnection",
    "QianchuanOAuthSession", "QianchuanUpload",
}
RULES = {
    "app.main": {"on_startup": {"CreativeBatchJob", "CreativeGeneration"}},
    "app.services.production_queue": {"*": {"ProductionQueueTask"}},
    "app.services.video_analysis_queue": {"*": {"VideoAsset", "VideoAssetAnalysis"}},
    "app.services.qianchuan.worker": {"*": {"QianchuanConnection"}},
    "app.services.qianchuan.uploads": {
        "run_next": {"Upload"}, "recover": {"Upload"},
    },
    "app.services.qianchuan.service": {"recover": {"Connection", "OAuthSession"}},
}
RUNNABLE = {
    "production_queue_tasks": {"pending"},
    "creative_batch_jobs": {"pending", "running"},
    "creative_generations": {"pending", "running"},
    "video_assets": {"queued_for_analysis", "validating"},
    "video_asset_analyses": {"analyzing"},
    "qianchuan_connections": {"sync_pending"},
    "qianchuan_oauth_sessions": {"pending", "processing"},
    "qianchuan_uploads": {"queued"},
}
BUSY = {
    "production_queue_tasks": {"pending", "running"},
    "creative_batch_jobs": {"pending", "running"},
    "creative_generations": {"pending", "running"},
    "video_assets": {"queued_for_analysis", "analyzing", "validating"},
    "video_asset_analyses": {"analyzing"},
    "qianchuan_connections": {"sync_pending", "syncing"},
    "qianchuan_oauth_sessions": {"pending", "processing"},
    "qianchuan_uploads": {"queued", "running", "uploaded", "unknown"},
}
TRANSFORMED = {}
MODELS = {}
INSTALLED = False
SUPPORTED_SOURCES = {'app.main': ['70871789600b1f67a05683893a6ec2b0f2f64c5367cf5307e72f175f5235231e'], 'app.services.production_queue': ['f9e248a8174ae4c08e82d248ffca9898f9981be315c30e4fae65650a96a7e0a0'], 'app.services.video_analysis_queue': ['7e2e807a0b72a0f93a49de45d96ab7562751e13b8e7b5387c1a49efd45a290f1', '8570dc2d08737731f3c4494a0426efb068dffe5bd09b6d8d365e8cd80aeb115c'], 'app.services.qianchuan.worker': ['279a3674d48e8da831c1c9301f74a662b21b62c5c25b5c65d53906c0df995c76'], 'app.services.qianchuan.uploads': ['d5045c55b573cb8d6c2f0f1ae4731048130a916e44dfc492c8398d43d4a85e58'], 'app.services.qianchuan.service': ['62be2423daa95a061f77c382b699b6cd782dc0acbfe7cbfb4eb5bbc6fccaa085']}


def source_fingerprint(module, tree):
    if module == "app.main":
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in {"on_startup", "on_shutdown"}]
        tree = ast.Module(body=nodes, type_ignores=[])
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


class SourceIsolation(ast.NodeTransformer):
    def __init__(self, module):
        self.module = module
        self.functions = []
        self.filters = 0
        self.models = 0

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        if self.module == "app.models" and node.name in MODEL_NAMES:
            if any(isinstance(child, (ast.Assign, ast.AnnAssign)) and (
                isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name) and child.target.id == OWNER
                or isinstance(child, ast.Assign) and any(isinstance(target, ast.Name) and target.id == OWNER for target in child.targets)
            ) for child in node.body):
                raise RuntimeError("Branch already defines task ownership; review native implementation")
            assignment = ast.parse(
                f"{OWNER} = mapped_column(String(32), nullable=False, default={SERVICE!r}, server_default='production', index=True)"
            ).body[0]
            node.body.append(assignment)
            self.models += 1
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        rules = RULES.get(self.module, {})
        names = rules.get("*", set()) | rules.get(self.functions[-1] if self.functions else "", set())
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "query"
                and len(node.args) == 1 and isinstance(node.args[0], ast.Name)
                and node.args[0].id in names):
            model = node.args[0].id
            criterion = ast.Compare(
                left=ast.Attribute(value=ast.Name(id=model, ctx=ast.Load()), attr=OWNER, ctx=ast.Load()),
                ops=[ast.Eq()], comparators=[ast.Constant(value=SERVICE)],
            )
            self.filters += 1
            return ast.copy_location(ast.Call(
                func=ast.Attribute(value=node, attr="filter", ctx=ast.Load()),
                args=[criterion], keywords=[],
            ), node)
        return node


def transformed_code(module, source, filename):
    tree = ast.parse(source, filename=filename)
    if module in RULES and source_fingerprint(module, tree) not in SUPPORTED_SOURCES.get(module, []):
        raise RuntimeError("Branch queue/lifecycle implementation changed; review isolation before deployment: " + module)
    transform = SourceIsolation(module)
    tree = transform.visit(tree)
    ast.fix_missing_locations(tree)
    if module in RULES and transform.filters == 0:
        raise RuntimeError("Unrecognized task queries in branch module: " + module)
    if module == "app.models" and transform.models != len(MODEL_NAMES):
        raise RuntimeError("Branch task model set changed; review isolation before deployment")
    TRANSFORMED[module] = {"filters": transform.filters, "models": transform.models}
    return compile(tree, filename, "exec")


class IsolationLoader(importlib.abc.Loader):
    def __init__(self, wrapped, module):
        self.wrapped, self.module = wrapped, module

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source = self.wrapped.get_source(self.module)
        filename = self.wrapped.get_filename(self.module)
        exec(transformed_code(self.module, source, filename), module.__dict__)


class IsolationFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "app.models" and fullname not in RULES:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or not hasattr(spec.loader, "get_source"):
            raise RuntimeError("Branch module unavailable: " + fullname)
        spec.loader = IsolationLoader(spec.loader, fullname)
        return spec


def install():
    global INSTALLED
    if INSTALLED:
        return
    if SERVICE not in SERVICES:
        raise RuntimeError("Set DEPLOYMENT_SERVICE_ID to production or artinjecter")
    if "app.models" in sys.modules or "app.main" in sys.modules:
        raise RuntimeError("Install task isolation before importing branch application")
    sys.meta_path.insert(0, IsolationFinder())
    from sqlalchemy import event, inspect, or_, select
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.elements import BindParameter
    from app import models
    for name in MODEL_NAMES:
        model = getattr(models, name)
        MODELS[model.__tablename__] = model

    @event.listens_for(Session, "before_flush")
    def assign_task_owner(session, context, instances):
        for item in session.new:
            if type(item) in MODELS.values():
                setattr(item, OWNER, SERVICE)
        for item in session.dirty:
            table_name = getattr(item, "__tablename__", "")
            if table_name not in MODELS:
                continue
            history = inspect(item).attrs.status.history
            verify_retry = (table_name == "qianchuan_uploads" and item.status in {"uploaded", "unknown"}
                            and inspect(item).attrs.next_attempt_at.history.has_changes()
                            and inspect(item).attrs.verify_attempts.history.has_changes()
                            and item.verify_attempts == 0 and item.next_attempt_at is not None)
            if not verify_retry and (not history.has_changes() or getattr(item, "status") not in RUNNABLE[table_name]):
                continue
            table = item.__table__
            identity = inspect(item).identity
            if identity is None:
                continue
            criteria = [column == value for column, value in zip(table.primary_key.columns, identity)]
            existing = session.connection().execute(
                select(table).where(*criteria).with_for_update()
            ).first()
            active = existing and existing.status in BUSY[table_name]
            if verify_retry and existing and existing.status in {"uploaded", "unknown"}:
                active = existing.next_attempt_at is not None or existing.verify_attempts < 3
            if existing and existing.deployment_service != SERVICE and active:
                from fastapi import HTTPException
                raise HTTPException(409, "Task is active in the other deployment; wait for it to finish")
            setattr(item, OWNER, SERVICE)

    @event.listens_for(Session, "do_orm_execute")
    def assign_bulk_task_owner(state):
        if not state.is_update or state.bind_mapper is None:
            return
        model = state.bind_mapper.class_
        table_name = getattr(model, "__tablename__", "")
        if table_name not in MODELS:
            return
        values = state.statement._values or {}
        status = None
        for key, value in values.items():
            if getattr(key, "key", key) == "status" and isinstance(value, BindParameter):
                status = value.value
        if status not in RUNNABLE[table_name]:
            return
        state.statement = state.statement.where(or_(
            model.deployment_service == SERVICE,
            model.status.not_in(BUSY[table_name]),
        )).values(deployment_service=SERVICE)

    from app.services import redis_client
    shared_key = redis_client.redis_key

    def isolated_key(name):
        if name in {"production-queue:wake", "video-analysis:wake"} or name.startswith("production-task-lock:"):
            return shared_key("deployment:" + SERVICE + ":" + name)
        return shared_key(name)

    redis_client.redis_key = isolated_key
    INSTALLED = True


def migrate():
    install()
    from sqlalchemy import inspect, text
    from app.database import engine
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Ownership migration requires PostgreSQL")
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SET LOCAL statement_timeout = '60s'"))
        inspector = inspect(connection)
        available = set(inspector.get_table_names())
        if not set(MODELS) <= available:
            raise RuntimeError("Task tables missing; do not initialize a new business schema")
        for table in sorted(MODELS):
            columns = {column["name"] for column in inspector.get_columns(table)}
            if OWNER not in columns:
                connection.execute(text(
                    f'ALTER TABLE "{table}" ADD COLUMN deployment_service VARCHAR(32) NOT NULL DEFAULT \'production\''
                ))
            connection.execute(text(
                f'CREATE INDEX IF NOT EXISTS "ix_{table}_deployment_status" ON "{table}" (deployment_service, status)'
            ))
    print("Task ownership migration complete; existing rows remain owned by production", flush=True)


def check_schema():
    install()
    from sqlalchemy import inspect, text
    from app.database import Base, engine
    from app.services.redis_client import get_redis
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Shared business database requires PostgreSQL")
    missing = []
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        inspector = inspect(connection)
        available = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in available:
                missing.append(table.name)
                continue
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            missing.extend(table.name + "." + column.name for column in table.columns if column.name not in actual)
    if missing:
        raise RuntimeError("Branch requires explicit shared-schema migration: " + ", ".join(missing))
    redis = get_redis()
    if redis is None or not redis.ping():
        raise RuntimeError("Service-owned queues require Redis")
    print("Branch schema compatible; task service=" + SERVICE, flush=True)


def application():
    install()
    import app.main as original
    for module in RULES:
        __import__(module)
    startup, shutdown = original.app.router.on_startup, original.app.router.on_shutdown
    if startup != [original.on_startup] or shutdown != [original.on_shutdown]:
        raise RuntimeError("Branch lifecycle changed; review task isolation")
    source = Path(original.__file__).read_text()
    start_node = next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == "on_startup")
    worker_calls = {
        node.func.id for node in ast.walk(start_node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith("start_")
    }
    expected = {"start_worker", "start_video_analysis_worker", "start_qianchuan_worker"}
    if worker_calls != expected:
        raise RuntimeError("Branch worker set changed; review ownership for new queues")
    original.init_db = check_schema

    original_health = original.health
    def health():
        result = original_health()
        from app.services import production_queue, video_analysis_queue
        from app.services.qianchuan import worker
        result.update({
            "deployment_service": SERVICE,
            "task_isolation": "deployment_service",
            "background_workers": {
                "production_queue": bool(production_queue._thread and production_queue._thread.is_alive()),
                "video_analysis": bool(video_analysis_queue._thread and video_analysis_queue._thread.is_alive()),
                "qianchuan": bool(worker._thread and worker._thread.is_alive()),
            },
        })
        return result
    for route in original.app.routes:
        if getattr(route, "path", None) == "/api/health":
            route.endpoint = health
            route.dependant.call = health
    return original.app


if __name__ == "__main__":
    sys.path.insert(0, "/app")
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "migrate":
        migrate()
    elif mode == "check":
        application()
        check_schema()
    elif mode == "serve":
        import uvicorn
        uvicorn.run(application(), host="0.0.0.0", port=8000, proxy_headers=True, forwarded_allow_ips="*")
    else:
        raise SystemExit("Usage: task_isolation_runtime.py [migrate|check|serve]")
