"""Public model-order evidence; execution and settlement remain server-owned."""
from typing import Literal
from decimal import Decimal

from pydantic import Field, StrictInt, model_validator

from app.schemas.billing import StrictPayload
from app.schemas.model_pricing import PricingInput


class Usage(StrictPayload):
    source: Literal['REAL_TOKENS', 'FOLDED_TOKENS', 'UNKNOWN']
    input_tokens: StrictInt | None = Field(ge=0, le=10**9)
    output_tokens: StrictInt | None = Field(ge=0, le=10**9)
    converted_tokens: str | None = Field(max_length=17, pattern=r'^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$')
    raw_metric: Literal['image_count', 'audio_milliseconds', 'character_count'] | None
    raw_quantity: StrictInt | None = Field(ge=0, le=10**9)

    @model_validator(mode='after')
    def separate_sources(self):
        real = (self.input_tokens, self.output_tokens)
        folded = (self.converted_tokens, self.raw_metric, self.raw_quantity)
        if self.source == 'UNKNOWN' and any(value is not None for value in (*real, *folded)):
            raise ValueError('Unknown usage has no quantities')
        if self.source == 'REAL_TOKENS' and (all(value is None for value in real) or any(value is not None for value in folded)):
            raise ValueError('Real usage requires an applicable direction and no conversion')
        if self.source == 'FOLDED_TOKENS' and (any(value is not None for value in real) or any(value is None for value in folded)):
            raise ValueError('Folded usage requires conversion evidence only')
        if self.converted_tokens is not None and Decimal(self.converted_tokens) > 10**9:
            raise ValueError('Converted tokens exceed the contract bound')
        return self


class SupplierCost(StrictPayload):
    status: Literal['UNKNOWN', 'KNOWN', 'NOT_INCURRED']
    amount: str | None = Field(max_length=32, pattern=r'^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,8})?$')
    currency: str | None = Field(pattern='^[A-Z]{3}$')

    @model_validator(mode='after')
    def evidence_status(self):
        if self.status == 'KNOWN':
            if self.amount is None or self.currency is None:
                raise ValueError('Known cost requires amount and currency')
        elif self.amount is not None or self.currency is not None:
            raise ValueError('Unknown or not incurred cost has no money amount')
        return self


class ModelOrder(StrictPayload):
    id: str = Field(pattern='^[0-9a-f]{32}$')
    request_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    model_key: str = Field(pattern='^[0-9a-f]{32}$')
    provider: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=200)
    execution_mode: Literal['MOCK', 'REAL']
    status: Literal['RESERVED', 'SETTLED', 'CANCELED']
    reconciliation_status: Literal['NONE', 'NEEDS_REVIEW', 'RESOLVED']
    usage: Usage
    supplier_cost: SupplierCost
    charged_credits: str = Field(max_length=24, pattern=r'^(?:0|[1-9][0-9]*)\.[0-9]{4}$')
    reserved_credits: str = Field(max_length=24, pattern=r'^(?:0|[1-9][0-9]*)\.[0-9]{4}$')
    pricing_snapshot: PricingInput
    provider_request_id: str | None = Field(max_length=200)
    created_at: str

    @model_validator(mode='after')
    def mode_cost(self):
        if (self.execution_mode == 'MOCK') != (self.supplier_cost.status == 'NOT_INCURRED'):
            raise ValueError('Mock has no vendor charge; real never means not incurred')
        return self


class ModelOrderPage(StrictPayload):
    items: list[ModelOrder] = Field(max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
