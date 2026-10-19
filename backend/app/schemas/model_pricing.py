from typing import Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from app.schemas.billing import StrictPayload
from app.services.billing.amounts import amount, MAX_INT


class ModelMetricRate(StrictPayload):
    metric: Literal['input_tokens', 'output_tokens', 'converted_tokens']
    unit_size: StrictInt = Field(ge=1, le=10**9)
    credits_per_unit: StrictStr = Field(max_length=24)


class ConversionInput(StrictPayload):
    source_metric: Literal['image_count', 'audio_milliseconds', 'character_count']
    source_unit_size: StrictInt
    tokens_per_unit: StrictInt = Field(ge=1, le=10**9)

    @model_validator(mode='after')
    def exact_scale(self):
        if self.source_unit_size not in (1, 10, 100, 1000, 10000, 100000, 1000000):
            raise ValueError('source_unit_size must be a supported power of ten')
        return self


class PricingInput(StrictPayload):
    usage_mode: Literal['REAL_TOKENS', 'FOLDED_TOKENS']
    metrics: list[ModelMetricRate] = Field(min_length=1, max_length=2)
    conversion: ConversionInput | None

    @model_validator(mode='after')
    def complete_price(self):
        names = [rate.metric for rate in self.metrics]
        if len(set(names)) != len(names):
            raise ValueError('Duplicate metrics')
        if self.usage_mode == 'REAL_TOKENS':
            if self.conversion is not None or 'converted_tokens' in names:
                raise ValueError('Real tokens cannot contain conversion')
        elif names != ['converted_tokens'] or self.conversion is None:
            raise ValueError('Folded tokens require one converted rate and conversion')
        from fractions import Fraction
        maximum = sum(Fraction(10**9 * amount(rate.credits_per_unit, code='INVALID_PRICE'), rate.unit_size) for rate in self.metrics)
        if maximum > MAX_INT:
            raise ValueError('Maximum token charge exceeds int64 microcredits')
        self.metrics.sort(key=lambda rate: rate.metric)
        return self


class Pricing(StrictPayload):
    model_key: str = Field(pattern='^[0-9a-f]{32}$')
    configuration: PricingInput
    updated_at: str


class Model(StrictPayload):
    model_key: str = Field(pattern='^[0-9a-f]{32}$')
    provider: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=200)
    provider_model_name: str | None
    model_types: list[Literal['TEXT', 'VISION', 'IMAGE', 'EMBEDDING', 'TTS', 'ASR', 'VIDEO']]
    connection_status: Literal['NOT_IMPLEMENTED', 'NOT_CONFIGURED', 'NOT_TESTED', 'PASSED', 'FAILED']
    configured: bool
    supported_usage_modes: list[Literal['REAL_TOKENS', 'FOLDED_TOKENS']]
    supported_raw_metrics: list[Literal['image_count', 'audio_milliseconds', 'character_count']]
    pricing: Pricing | None


class ModelPage(StrictPayload):
    items: list[Model]
    total: int
    page: int
    page_size: int


class ErrorDetail(StrictPayload):
    code: str
    message: str
    request_id: str


class ModelPricingError(StrictPayload):
    detail: ErrorDetail


ERROR_RESPONSES = {code: {'model': ModelPricingError} for code in (401, 403, 404, 409, 410, 413, 422, 503)}
