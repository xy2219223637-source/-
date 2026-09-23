"""Isolated browser demo. Uses only synthetic data, a temporary DB and MockTransport.

Run from backend: uv run --no-project --python .venv/Scripts/python.exe python scripts/qianchuan_auth_demo.py
Requires frontend/dist (npm run build). No production app startup or external services.
"""
from __future__ import annotations

import html
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def build_demo(directory: str):
    # Set these before importing application modules; never connect to the configured production DB.
    os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(directory) / "demo.db")
    os.environ["REQUIRE_POSTGRES"] = "false"
    os.environ["AUTH_SECRET"] = "qianchuan-isolated-demo-only"
    os.environ["QIANCHUAN_ENABLED"] = "false"
    import httpx
    from cryptography.fernet import Fernet
    from fastapi import Depends, FastAPI, Query
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from app.api import auth, qianchuan
    from app.auth import current_auth, create_token, hash_password
    from app.config import Settings
    from app.database import Base, engine, SessionLocal
    from app.models import Product, Tenant, TenantMember, User, VideoAsset
    from app.services.qianchuan.client import QianchuanClient, SHOP_ROLE, AGENT_ROLE
    from app.services.qianchuan.service import QianchuanService, get_qianchuan_service, install_log_filters
    from app.services.qianchuan.worker import start_qianchuan_worker, stop_qianchuan_worker

    settings = Settings(_env_file=None, debug=True, qianchuan_enabled=True,
        qianchuan_app_id="12345", qianchuan_app_secret="synthetic-demo-secret",
        qianchuan_authorization_url="https://ad.oceanengine.com/openapi/audit/oauth.html",
        qianchuan_redirect_uri="http://127.0.0.1:5173/api/qianchuan/oauth/callback",
        qianchuan_token_encryption_key=Fernet.generate_key().decode())

    def provider(request):
        path = request.url.path
        if path.endswith(("/access_token/", "/refresh_token/")):
            import json
            body = json.loads(request.content)
            mode = body.get("auth_code", body.get("refresh_token", "demo-shop")).split("-")[-1]
            data = {"access_token": "demo-access-" + mode, "refresh_token": "demo-refresh-" + mode,
                    "expires_in": 7200, "refresh_token_expires_in": 86400}
        elif path == "/open_api/oauth2/advertiser/get/":
            mode = request.url.params.get("access_token", "").split("-")[-1]
            role = {"shop": SHOP_ROLE, "agent": AGENT_ROLE, "advertiser": "ADVERTISER"}.get(mode)
            if not role:
                raise AssertionError("Unknown synthetic grant")
            data = {"list": [{"account_id": 100, "account_name": "Mock 授权来源", "account_type": role,
                             "is_valid": True, "company_list": [{"customer_company_id": 123}]}]}
        elif path.endswith("/shop/advertiser/list/"):
            data = {"adv_id_list": [{"adv_id": 9007199254740993, "adv_name": "Mock 千川账户 A"},
                                    {"adv_id": 9007199254740994, "adv_name": "Mock 千川账户 B"}],
                    "page_info": {"page": 1, "total_page": 1}}
        elif path == "/open_api/2/agent/advertiser/select/":
            data = {"advertiser_ids": [9007199254740993], "page_info": {"page": 1, "total_page": 1}}
        elif path.endswith("/advertiser/type/get/"):
            data = {"list": [{"advertiser_id": value, "ecp_type": "SHOP"}
                             for value in json.loads(request.url.params["advertiser_ids"])]}
        else:
            raise AssertionError("Demo attempted an unexpected platform operation")
        return httpx.Response(200, json={"code": 0, "data": data, "request_id": "mock-request"})

    class DemoService(QianchuanService):
        def create_session(self, *args, **kwargs):
            result = super().create_session(*args, **kwargs)
            result["authorization_url"] = self.origin + "/mock/authorize?" + urlsplit(result["authorization_url"]).query
            return result

    client = QianchuanClient(settings, httpx.MockTransport(provider))
    service = DemoService(settings, client)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(Tenant(id="demo-tenant", name="Mock 授权测试", code="qianchuan-demo"))
        db.add(User(id="demo-owner", email="owner@example.test", name="Mock 管理员",
                    password_hash=hash_password("DemoOnly123!")))
        db.flush()
        db.add(TenantMember(tenant_id="demo-tenant", user_id="demo-owner", role="owner"))
        db.add(Product(id=1, tenant_id="demo-tenant", name="Mock 授权测试成片"))
        db.flush()
        db.add(VideoAsset(id=1, tenant_id="demo-tenant", product_id=1, asset_id="demo-video", filename="Mock 授权测试.mp4",
                          object_url="", content_hash="a" * 64, asset_kind="final_output", library_type="finished",
                          duration_seconds=10, width=1080, height=1920))
        db.commit()
    install_log_filters()

    @asynccontextmanager
    async def lifespan(app):
        start_qianchuan_worker(service)
        yield
        stop_qianchuan_worker()
        client.close()
        engine.dispose()

    app = FastAPI(title="千川授权 Mock 验证（临时数据）", lifespan=lifespan)
    app.include_router(auth.router, prefix="/api")
    app.include_router(qianchuan.router, prefix="/api")
    app.dependency_overrides[get_qianchuan_service] = lambda: service

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "qianchuan_mock", "database": "temporary_sqlite",
                "qianchuan": service.configuration()}

    @app.get("/api/products")
    def products(context=Depends(current_auth)):
        return [{"id": 1, "name": "Mock 授权测试成片", "created_at": datetime.utcnow().isoformat()}]

    @app.get("/api/video-assets")
    def assets(context=Depends(current_auth)):
        return [{"id": 1, "product_id": 1, "asset_id": "demo-video", "filename": "Mock 授权测试.mp4",
                 "object_url": "", "asset_kind": "final_output", "library_type": "finished", "duration_seconds": 10,
                 "width": 1080, "height": 1920, "created_at": datetime.utcnow().isoformat()}]

    @app.get("/mock/start", response_class=HTMLResponse)
    def start():
        token = create_token("demo-owner", "demo-tenant")
        # The user explicitly enters a labeled demo; this token can access only the disposable DB.
        return '<!doctype html><html lang="zh"><meta charset="utf-8"><title>千川授权 Mock 测试</title>' + (
            '<body style="font-family:system-ui;max-width:650px;margin:80px auto;padding:24px">'
            '<h1>千川授权 Mock 测试</h1><p>使用临时数据库和模拟平台，不访问真实账户，不上传素材。</p>'
            '<p>点击下方按钮会使用临时测试账号登录本地址。服务停止后测试数据删除。</p>'
            '<button id="start" style="padding:12px 24px">进入成片并验证授权</button>'
            '<script>document.getElementById("start").onclick=()=>{localStorage.setItem("access_token","'
            + token + '");location.href="/final-videos/1";};</script></body></html>')

    @app.get("/mock/authorize", response_class=HTMLResponse)
    def authorize(state: str = Query(min_length=32, max_length=256)):
        safe_state = html.escape(state, quote=True)
        return '<!doctype html><html lang="zh"><meta charset="utf-8"><title>模拟官方授权</title>' + (
            '<body style="font-family:system-ui;max-width:650px;margin:80px auto;padding:24px">'
            '<h1>模拟官方授权页</h1><p>仅验证本系统流程；以下账户均为虚构测试数据。</p>'
            '<form action="/api/qianchuan/oauth/callback" method="get">'
            '<input type="hidden" name="state" value="' + safe_state + '">'
            '<label><input type="radio" name="auth_code" value="demo-shop" checked>店铺授权</label><br>'
            '<label><input type="radio" name="auth_code" value="demo-agent">千川代理商授权</label><br>'
            '<label><input type="radio" name="auth_code" value="demo-advertiser">直接广告账户</label><br><br>'
            '<button type="submit">同意模拟授权</button></form>'
            '<form action="/api/qianchuan/oauth/callback" method="get" style="margin-top:20px">'
            '<input type="hidden" name="state" value="' + safe_state + '">'
            '<input type="hidden" name="error" value="access_denied"><button type="submit">取消授权</button>'
            '</form></body></html>')

    class SPA(StaticFiles):
        async def get_response(self, path, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404 or path.startswith(("api/", "mock/")):
                    raise
                return await super().get_response("index.html", scope)

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise RuntimeError("请先在 frontend 执行 npm run build")
    app.mount("/", SPA(directory=dist, html=True), name="frontend")
    return app


if __name__ == "__main__":
    import uvicorn
    with tempfile.TemporaryDirectory(prefix="qianchuan-auth-demo-") as directory:
        uvicorn.run(build_demo(directory), host="127.0.0.1", port=5173, access_log=False)
