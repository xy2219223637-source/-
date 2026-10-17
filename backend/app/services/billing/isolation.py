"""Restrict test billing deployments to authenticated management and mock routes."""
from starlette.responses import JSONResponse


def allowed_path(path: str, method: str) -> bool:
    # Exact segment boundaries: /auth/login/evil and prefix tricks stay closed.
    if path in {"/api/health", "/api/auth/login", "/api/auth/me", "/api/auth/change-password"}:
        return True
    if path in {"/docs", "/openapi.json", "/docs/oauth2-redirect"}:
        return True
    if path.startswith(("/api/platform-admin/", "/api/tenant-admin/", "/api/me/billing/")):
        return True
    return method == "POST" and path in {"/api/ai/mock/fixed", "/api/ai/mock/text"}


class BillingIsolationMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.settings.billing_test_isolation:
            length = next((v for k, v in scope["headers"] if k.lower() == b"content-length"), b"0")
            try:
                too_large = int(length) > 16384
            except ValueError:
                too_large = True
            if too_large:
                await JSONResponse({"detail": {"code": "REQUEST_TOO_LARGE", "message": "请求体过大"}}, 413)(scope, receive, send)
                return
            if not allowed_path(scope["path"], scope["method"]):
                await JSONResponse({"detail": {"code": "TEST_ISOLATION", "message": "测试环境未开放此功能"}}, 403)(scope, receive, send)
                return
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 16384:
                    await JSONResponse({"detail": {"code": "REQUEST_TOO_LARGE", "message": "请求体过大"}}, 413)(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            delivered = False
            async def buffered_receive():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            await self.app(scope, buffered_receive, send)
            return
        await self.app(scope, receive, send)
