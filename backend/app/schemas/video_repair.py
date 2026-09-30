from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TimeRange(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("时间段结束时间必须大于开始时间")
        self.start = round(self.start, 3)
        self.end = round(self.end, 3)
        return self


class VideoRepairInputCreate(BaseModel):
    source_kind: Literal["MANUAL", "INTERNAL_REVIEW"] = "MANUAL"
    audit_platform: Literal["INTERNAL", "AD", "CONTENT", "UNKNOWN"] = "INTERNAL"
    raw_reason: str = Field(min_length=1, max_length=4000)
    expected_result: str = Field(default="", max_length=4000)
    suggestions: list[str] = Field(default_factory=list, max_length=20)
    time_ranges: list[TimeRange] = Field(default_factory=list, max_length=20)
    category_hint: Literal["VISUAL", "COPY", "AV", "PROCESS", "FULL", "UNKNOWN"] = "UNKNOWN"


class VideoRepairTaskCreate(BaseModel):
    source_asset_id: int = Field(gt=0)
    input: VideoRepairInputCreate


class VideoRepairInputRead(BaseModel):
    id: int
    source_kind: str
    audit_platform: str
    raw_reason: str
    expected_result: str = ""
    suggestions: list[str]
    time_ranges: list[dict[str, float]]
    occurred_at: datetime
    created_at: datetime


class CandidateSelect(BaseModel):
    action_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=256)
    override_reason: str | None = Field(default=None, max_length=1000)


class RepairApprove(BaseModel):
    updated_at: datetime


class DraftTargetRange(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("时间段结束时间必须大于开始时间")
        return self


class DraftPlanUpdate(BaseModel):
    updated_at: datetime
    action_type: Literal["VISUAL_REPLACE", "AV_SEGMENT_REPLACE", "PROCESS_INSERT"]
    target_range: DraftTargetRange
    selected_candidate_id: str | None = Field(default=None, max_length=256)


class RepairTaskRead(BaseModel):
    id: int
    status: str
    schema_version: str = "video_repair_task.v1"
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
