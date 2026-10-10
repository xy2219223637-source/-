from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


EvidenceSource = Literal[
    "REVIEW_TEXT", "USER_INPUT", "ASR", "OCR", "FRAME", "TIMELINE", "PRODUCT_FACT",
]
IssueCategory = Literal[
    "VISUAL_MISMATCH",
    "COPY_AUDIO_CAPTION_RISK",
    "AUDIO_VISUAL_RISK",
    "MISSING_PROCESS_EVIDENCE",
    "NON_VIDEO_COMPLIANCE",
    "UNKNOWN",
]
AffectedModality = Literal["visual", "audio", "caption", "full_video"]
RepairAction = Literal[
    "VISUAL_REPLACE", "AV_SEGMENT_REPLACE", "PROCESS_INSERT", "UNSUPPORTED",
]


class Evidence(BaseModel):
    source: EvidenceSource
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    description: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_range(self):
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("证据时间范围必须同时包含开始和结束时间")
        if self.start_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("证据结束时间必须大于开始时间")
        return self


class IssueRange(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    precision: Literal["EXACT", "APPROXIMATE"]
    source: Literal["INPUT", "TEXT_PARSE", "ASR", "OCR", "MULTIMODAL"]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("问题结束时间必须大于开始时间")
        return self


class DiagnosedIssue(BaseModel):
    issue_id: str = Field(min_length=1, max_length=128)
    category: IssueCategory
    description: str = Field(min_length=1, max_length=2000)
    affected_modalities: list[AffectedModality] = Field(min_length=1)
    ranges: list[IssueRange] = Field(default_factory=list)
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    suggested_action: RepairAction

    @model_validator(mode="after")
    def validate_action(self):
        if self.category == "NON_VIDEO_COMPLIANCE" and self.suggested_action != "UNSUPPORTED":
            raise ValueError("非视频合规问题只能标记为 UNSUPPORTED")
        if ({"audio", "caption"} & set(self.affected_modalities)
                and self.suggested_action == "VISUAL_REPLACE"):
            raise ValueError("音频或字幕问题不能只替换画面")
        return self


class DiagnosisMeta(BaseModel):
    mode: Literal["MULTIMODAL", "RULE_FALLBACK", "MANUAL"]
    model: str | None = None
    prompt_version: str = "video-repair-diagnosis-v2"
    latency_ms: int | None = Field(default=None, ge=0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None


class VideoRepairDiagnosisV2(BaseModel):
    schema_version: Literal["video_repair_diagnosis.v2"] = "video_repair_diagnosis.v2"
    summary: str = Field(min_length=1, max_length=4000)
    issues: list[DiagnosedIssue] = Field(default_factory=list)
    primary_issue_id: str | None = None
    requires_human_range_confirmation: bool
    overall_confidence: float = Field(ge=0, le=1)
    meta: DiagnosisMeta

    @model_validator(mode="after")
    def validate_primary_issue(self):
        identifiers = [issue.issue_id for issue in self.issues]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("issue_id 必须唯一")
        if self.primary_issue_id is not None and self.primary_issue_id not in identifiers:
            raise ValueError("primary_issue_id 不存在")
        return self

    def validate_duration(self, duration_ms: int) -> "VideoRepairDiagnosisV2":
        if duration_ms <= 0:
            raise ValueError("视频时长无效")
        for issue in self.issues:
            for issue_range in issue.ranges:
                if issue_range.end_ms > duration_ms:
                    raise ValueError("问题时间段超出视频时长")
            for evidence in issue.evidence:
                if evidence.end_ms is not None and evidence.end_ms > duration_ms:
                    raise ValueError("证据时间段超出视频时长")
        primary = next(
            (issue for issue in self.issues if issue.issue_id == self.primary_issue_id), None)
        if primary and primary.suggested_action != "UNSUPPORTED" and not primary.ranges:
            self.requires_human_range_confirmation = True
        return self
