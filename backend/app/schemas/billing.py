from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GrantRequest(StrictPayload):
    amount_credits: StrictStr
    reason: StrictStr = ""


class QuotaRequest(StrictPayload):
    total_limit_credits: StrictStr
    reason: StrictStr = ""


class MetricRate(StrictPayload):
    metric: Literal["input_tokens", "output_tokens"]
    unit_size: StrictInt = Field(ge=1, le=10**9)
    credits_per_unit: StrictStr


class PriceRequest(StrictPayload):
    feature_code: Literal["mock_fixed", "mock_text"]
    billing_mode: Literal["FIXED", "USAGE"]
    version: StrictInt = Field(ge=1, le=1_000_000)
    fixed_credits: StrictStr | None = None
    metrics: list[MetricRate] = Field(default_factory=list, max_length=2)


class MockRequest(StrictPayload):
    text: StrictStr = Field(default="测试", min_length=1, max_length=2000)
