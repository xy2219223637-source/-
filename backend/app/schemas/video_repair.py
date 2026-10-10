from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class VideoRepairInputCreate(BaseModel):
    raw_reason: str = Field(min_length=1, max_length=4000)
    suggestions: list[str] = Field(default_factory=list, max_length=20)


class VideoRepairTaskCreate(BaseModel):
    source_asset_id: int = Field(gt=0)
    ecommerce_product_id: int = Field(gt=0)
    input: VideoRepairInputCreate


class VideoRepairInputRead(BaseModel):
    id: int
    source_kind: str
    audit_platform: str
    raw_reason: str
    suggestions: list[str]
    time_ranges: list[dict[str, float]]
    occurred_at: datetime
    created_at: datetime


class RepairTaskRead(BaseModel):
    id: int
    status: str
    schema_version: str = "video_repair_task.v2"
    product_id: int | None = None
    draft_revision: int = 1
    accepted_preview_id: int | None = None
    cancel_reason: str = ""
    capabilities: list[str] = Field(default_factory=list)
    tts_available: bool = False
    input_id: int
    input: VideoRepairInputRead
    review_feedback_id: int | None
    source_asset_id: int
    base_plan_version_id: int | None
    diagnosis: dict[str, Any]
    repair_plan: dict[str, Any]
    selected_candidates: dict[str, str]
    output_asset_id: int | None
    ecommerce_output_asset_id: int | None = None
    output_plan_version_id: int | None
    source_asset: dict[str, Any]
    output_asset: dict[str, Any] | None
    error_code: str
    error_message: str
    approved_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    previews: list[dict[str, Any]] = Field(default_factory=list)


class RepairTaskPage(BaseModel):
    items: list[RepairTaskRead]
    page: int
    page_size: int
    total: int


class V4DraftUpdate(BaseModel):
    draft_revision: int = Field(ge=1)
    action: dict[str, Any]
    override_reason: str = Field(default="", max_length=1000)


class PreviewAccept(BaseModel):
    draft_revision: int = Field(ge=1)


class TTSCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str = Field(default="", max_length=128)
