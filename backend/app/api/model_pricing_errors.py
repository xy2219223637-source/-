"""Scoped error contract, leaving unrelated billing interfaces unchanged."""
import uuid

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse


class ModelPricingRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            scoped = ('/platform-admin/billing/price-rules' in request.url.path or '/billing/model-orders' in request.url.path
                      or request.url.path.startswith('/api/ai/mock/'))
            if not scoped:
                return await original(request)
            request_id = uuid.uuid4().hex
            def error(status, code, message):
                return JSONResponse({'detail': {'code': code, 'message': message, 'request_id': request_id}}, status)
            try:
                # Read bounded chunks; do not trust Content-Length or buffer an unlimited body.
                content = bytearray()
                async for chunk in request.stream():
                    content.extend(chunk)
                    if len(content) > 16384:
                        return error(413, 'REQUEST_TOO_LARGE', '请求体超过 16 KiB')
                request._body = bytes(content)
                return await original(request)
            except RequestValidationError:
                return error(422, 'VALIDATION_ERROR', '请求参数不符合接口契约')
            except HTTPException as exc:
                code = exc.detail.get('code') if isinstance(exc.detail, dict) else None
                message = exc.detail.get('message') if isinstance(exc.detail, dict) else str(exc.detail)
                if exc.status_code == 401:
                    code = 'UNAUTHENTICATED'
                elif exc.status_code == 403:
                    code = 'FORBIDDEN'
                elif code == 'INVALID_IDEMPOTENCY_KEY':
                    code = 'VALIDATION_ERROR'
                return error(exc.status_code, code or 'SERVICE_UNAVAILABLE', message)
            except SQLAlchemyError:
                return error(503, 'SERVICE_UNAVAILABLE', '模型费用存储暂不可用')
        return handler
