"""Run branch code behind the server-managed test resource and startup checks."""
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path.cwd()))

from isolation_guard import (
    INTEGRATION_FIELDS, IsolationError, check_database, load_resources, require,
    verify_test_environment,
)


STARTUP_FLAGS = ("run_database_init", "run_startup_recovery", "run_background_workers")


def branch_workers(module):
    original = getattr(module, "on_startup", None)
    require(callable(original), "Branch has no recognized startup hook")
    tree = ast.parse(inspect.getsource(original))
    names = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id.startswith("start_") and node.func.id.endswith("worker")}
    require({"start_worker", "start_video_analysis_worker"} <= names,
            "Branch has no recognized queue workers")
    workers = {}
    for name in sorted(names):
        function = getattr(module, name, None)
        require(callable(function), f"Branch worker missing: {name}")
        owner = importlib.import_module(function.__module__)
        require(hasattr(owner, "_thread"), f"Branch worker has no thread status: {name}")
        workers[name] = function
    return workers


def prepare_health(module, settings, workers):
    routes = [route for route in module.app.routes if getattr(route, "path", None) == "/api/health"]
    require(len(routes) == 1 and not inspect.iscoroutinefunction(routes[0].endpoint),
            "Branch health endpoint is unsupported")
    original = routes[0].endpoint
    labels = {"start_worker": "production_queue", "start_video_analysis_worker": "video_analysis",
              "start_qianchuan_worker": "qianchuan", "start_video_repair_worker": "video_repair"}

    def health():
        result = original()
        states = {}
        required = []
        for name, function in workers.items():
            owner = importlib.import_module(function.__module__)
            thread = owner._thread
            label = labels.get(name, name)
            states[label] = bool(thread and thread.is_alive())
            enabled = settings.run_background_workers
            if name == "start_qianchuan_worker":
                enabled = enabled and result.get("qianchuan", {}).get("status") == "ready"
            elif name == "start_video_repair_worker":
                enabled = enabled and getattr(settings, "video_repair_v4_enabled", False) and getattr(
                    settings, "video_repair_async_worker_enabled", False)
            if enabled:
                required.append(label)
        result.update(deployment_environment="test", background_workers=states,
                      background_workers_enabled=settings.run_background_workers,
                      startup_recovery_enabled=settings.run_startup_recovery)
        if any(not states[name] for name in required):
            result["status"] = "degraded"
        return result

    routes[0].endpoint = health
    routes[0].dependant.call = health


def deployment_settings(settings):
    values = dict(settings.model_dump())
    for field in (*INTEGRATION_FIELDS, "app_public_base_url"):
        values.setdefault(field, os.environ.get(field.upper(), ""))
    for field in STARTUP_FLAGS:
        raw = os.environ.get(field.upper(), "").lower()
        require(raw in ("true", "false"), f"Set {field.upper()} explicitly to true or false")
        require(field not in values or values[field] == (raw == "true"),
                f"Branch configuration differs from {field.upper()}")
        values[field] = raw == "true"
    require(os.environ.get("DEPLOYMENT_ENVIRONMENT") == "test", "Test deployment identity is required")
    values["deployment_environment"] = "test"
    values["test_resource_manifest"] = os.environ.get("TEST_RESOURCE_MANIFEST", "")
    return SimpleNamespace(**values)


def prepare_startup(module, branch_settings, settings) -> None:
    """Support the original single-hook startup without editing checked-out code."""
    if all(hasattr(branch_settings, field) for field in STARTUP_FLAGS):
        return
    handlers = module.app.router.on_startup
    original = getattr(module, "on_startup", None)
    require(original is not None and handlers == [original]
            and not inspect.iscoroutinefunction(original)
            and callable(getattr(module, "init_db", None)),
            "Legacy branch has an unsupported startup lifecycle")
    workers = branch_workers(module)
    if settings.run_background_workers and not settings.run_startup_recovery:
        require(all("recover" in inspect.signature(worker).parameters for worker in workers.values()),
                "This legacy branch requires RUN_STARTUP_RECOVERY=true when workers are enabled")

    def no_schema_init():
        # Explicit deployment migration already ran with the separate migration role.
        return None

    def guarded_startup():
        module.init_db = no_schema_init
        if not settings.run_background_workers:
            for name in workers:
                setattr(module, name, lambda *args, **kwargs: None)
        if settings.run_startup_recovery:
            original()
        elif settings.run_background_workers:
            for worker in workers.values():
                worker(recover=False)

    handlers[:] = [guarded_startup]


def run(mode: str, *, before_migration: bool = False) -> None:
    from app.config import get_settings
    from app.database import Base, engine, init_db

    branch_settings = get_settings()
    settings = deployment_settings(branch_settings)
    if mode == "migrate":
        check_database(settings, engine, load_resources(settings), migration=True)
        init_db()
        print(f"Test schema migrated: {len(Base.metadata.tables)} branch tables")
        return
    verify_test_environment(settings, engine, verify_schema=not before_migration)
    module = importlib.import_module("app.main")
    workers = branch_workers(module)
    prepare_startup(module, branch_settings, settings)
    prepare_health(module, settings, workers)
    if mode == "check":
        print("Independent test resources and branch startup verified")
        return
    import uvicorn

    uvicorn.run(module.app, host="0.0.0.0", port=8000,
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "migrate", "serve"))
    parser.add_argument("--before-migration", action="store_true")
    args = parser.parse_args()
    try:
        run(args.mode, before_migration=args.before_migration)
    except Exception as exc:
        # Database and provider errors can contain credentials or signed URLs.
        message = str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
        raise SystemExit(f"Test runtime rejected: {message}") from None
