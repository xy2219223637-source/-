from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


CapabilityConclusion = Literal[
    "verified", "permission_missing", "account_not_applicable",
    "no_sample_data", "contract_uncertain",
]


class QianchuanCapabilityItem(BaseModel):
    name: str
    conclusion: CapabilityConclusion
    probed_at: datetime
    expires_at: datetime


class QianchuanPlanCapabilities(BaseModel):
    enabled: bool
    write_enabled: bool
    worker_enabled: bool
    binding_id: UUID | None = None
    credential_version: int | None = None
    can_create: bool = False
    capabilities: list[QianchuanCapabilityItem] = Field(default_factory=list)


class QianchuanAwemeOption(BaseModel):
    id: str
    name: str = ""
    show_id: str = ""
    avatar_url: str = ""
    status: str = ""
    has_live_permission: bool | None = None
    has_video_permission: bool | None = None
    has_publish_permission: bool | None = None
    bind_types: list[str] = Field(default_factory=list)


class QianchuanAwemePage(BaseModel):
    items: list[QianchuanAwemeOption]
    page: int
    page_size: int
    total_number: int
    total_page: int


class QianchuanProductOption(BaseModel):
    id: str
    name: str = ""
    image_url: str = ""
    category_name: str = ""
    channel_type: str = ""


class QianchuanProductPage(BaseModel):
    items: list[QianchuanProductOption]
    cursor: int | None = None
    has_more: bool
    page_size: int


class QianchuanPlanDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: UUID | None = None
    video_asset_id: int = Field(gt=0)
    upload_id: UUID
    binding_id: UUID
    product_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    aweme_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    name: str = Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")
    # Local draft safety range only. Platform units remain blocked behind capability validation.
    budget_minor: int = Field(ge=1, le=100_000_000)
    roi_goal: Decimal = Field(gt=0, le=1000, max_digits=12, decimal_places=4)
    marketing_goal: Literal["VIDEO_PROM_GOODS"] = "VIDEO_PROM_GOODS"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("计划名称不能为空")
        return value


class QianchuanDeliveryPlan(BaseModel):
    id: UUID
    video_asset_id: int
    upload_id: UUID
    binding_id: UUID
    advertiser_id: str
    material_id: str
    product_id: str
    product_name: str
    aweme_id: str
    aweme_name: str
    name: str
    marketing_goal: str
    budget_minor: int
    roi_goal: Decimal | None
    platform_ad_id: str | None
    local_status: str
    platform_status: str
    review_status: str
    delivery_status: str
    operation_version: int
    error_message: str
    created_at: datetime
    updated_at: datetime
    last_synced_at: datetime | None


class QianchuanPlanActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    confirmed: StrictBool


class QianchuanPlanStatusRequest(QianchuanPlanActionRequest):
    action: Literal["ENABLE", "PAUSE"]


class QianchuanPlanOperation(BaseModel):
    id: UUID
    plan_id: UUID
    operation_type: str
    status: str
    before_status: str
    after_status: str
    request_version: int
    result_certainty: str
    provider_code: str
    provider_request_id: str
    error_message: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class QianchuanPlanReport(BaseModel):
    plan_id: UUID
    days: Literal[7, 30]
    metrics: dict[str, int | float | str | None]
    queried_at: datetime
    cache_status: str
    scope: Literal["single_plan"] = "single_plan"
