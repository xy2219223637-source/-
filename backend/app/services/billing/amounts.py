from __future__ import annotations

from fractions import Fraction
import logging
import re

from fastapi import HTTPException

MAX_INT = 2**63 - 1
SCALE = 10_000


def fail(code: str, message: str, status: int = 409):
    from app.services.feishu_logging import trace_fields
    logging.getLogger("app.billing").warning("Billing request rejected: %s", code, extra={"request_id": trace_fields().get("request_id", "")})
    raise HTTPException(status, detail={"code": code, "message": message, "request_id": trace_fields().get("request_id", "")})


def amount(value: str, *, zero: bool = False, maximum: int = MAX_INT, code="INVALID_GRANT_AMOUNT") -> int:
    if not isinstance(value, str) or len(value) > 24 or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,4})?", value):
        fail(code, "积分必须为最多四位小数的非负十进制字符串", 422)
    whole, _, fraction = value.partition(".")
    result = int(whole) * SCALE + int((fraction + "0000")[:4])
    if result > maximum or result < (0 if zero else 1):
        fail(code, "积分金额超出允许范围", 422)
    return result


def credits(value: int) -> str:
    sign = "-" if value < 0 else ""
    whole, fraction = divmod(abs(value), SCALE)
    return f"{sign}{whole}.{fraction:04d}"


def calculate(snapshot: dict, usage: dict) -> int:
    if snapshot["billing_mode"] == "FIXED":
        if usage:
            fail("INVALID_USAGE", "按次功能不接受用量", 422)
        return snapshot["fixed_microcredits"]
    rates = snapshot["metrics"]
    if set(usage) != {rate["metric"] for rate in rates}:
        fail("INVALID_USAGE", "计量项缺失或未知", 422)
    total = Fraction(0)
    for rate in rates:
        quantity = usage[rate["metric"]]
        if type(quantity) is not int or not 0 <= quantity <= 10**9:
            fail("INVALID_USAGE", "用量必须为受限非负整数", 422)
        total += Fraction(quantity * amount(rate["credits_per_unit"]), rate["unit_size"])
    result = (total.numerator + total.denominator - 1) // total.denominator
    if result > MAX_INT:
        fail("INVALID_USAGE", "计费金额超出范围", 422)
    return result
