from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from .operations import QianchuanPlanOperationService


@dataclass(frozen=True)
class MockExecutionResult:
    """Explicit scripted result; never inferred and never sent to a provider."""

    outcome: str
    platform_ad_id: str | None = None
    provider_request_id: str = "mock-request"
    error_code: str = ""
    error_message: str = ""


class MockQianchuanPlanExecutor:
    """Synchronous test adapter. It has no HTTP client, loop, queue, or scheduler."""

    def __init__(self, auth_service, *, allow_mock: bool = False):
        if not allow_mock or not auth_service.settings.debug:
            raise RuntimeError("mock executor 仅允许在显式启用的 debug 测试环境运行")
        self.operations = QianchuanPlanOperationService(auth_service)

    def execute(self, db, auth, operation_id: str, result: MockExecutionResult) -> dict:
        if not isinstance(result, MockExecutionResult):
            raise TypeError("mock executor 只接受 MockExecutionResult")
        operation = self.operations._owned_operation(db, auth, str(operation_id))
        kwargs = {
            "outcome": result.outcome,
            "platform_ad_id": result.platform_ad_id,
            "provider_request_id": result.provider_request_id,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }
        if operation.operation_type == "create":
            if result.outcome not in {"created", "not_created", "uncertain"}:
                raise HTTPException(422, "mock create 结果不合法")
            response = self.operations.record_submit_result(db, auth, operation.id, **kwargs)
        elif operation.operation_type == "reconcile_create":
            if result.outcome not in {"found", "confirmed_absent", "inconclusive"}:
                raise HTTPException(422, "mock 回查结果不合法")
            response = self.operations.record_recheck_result(db, auth, operation.id, **kwargs)
        else:
            raise HTTPException(409, "mock executor 不支持该操作类型")
        return {**response, "executor": "mock"}
