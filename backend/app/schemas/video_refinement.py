from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Focus = Literal["hook", "pace", "product_visibility", "voice", "subtitle"]
ItemType = Literal["VISUAL_REPLACE", "PACE_RECUT", "SEGMENT_INSERT", "VOICE_REPLACE", "SUBTITLE_EDIT"]


def validate_tts_speaker(value):
    if value is not None:
        from app.services.refinement_speakers import speaker_by_id
        speaker_by_id(value)
    return value


class RefinementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_asset_id: int = Field(gt=0)
    user_brief: str = Field(min_length=1, max_length=2000)
    expected_result: str = Field(default="", max_length=1000)
    focus: list[Focus] = Field(default_factory=list, max_length=5)

    duration_preference: Literal["preserve", "shorten", "ai_recommend"] = "ai_recommend"
    target_duration_ms: int | None = Field(default=None, gt=0, strict=True)
    tts_speaker: str | None = Field(default=None, max_length=120)

    _speaker = field_validator("tts_speaker")(validate_tts_speaker)

    @field_validator("user_brief")
    @classmethod
    def require_brief(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请填写精修目标")
        return value


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(ge=0, strict=True)


class MaterialSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: int = Field(gt=0)
    source_range: TimeRange
    candidate_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def nonempty(self):
        if self.source_range.end_ms <= self.source_range.start_ms:
            raise ValueError("素材结束时间必须大于开始时间")
        return self


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    text: str = Field(min_length=1, max_length=2000)
    request_quote: str = Field(default="", max_length=2000)
    status: Literal["EXECUTABLE", "ALREADY_SATISFIED", "NEEDS_CLARIFICATION", "CONFLICT", "UNSUPPORTED", "AWAITING_MATERIAL"]
    reason: str = Field(default="", max_length=2000)
    question: str = Field(default="", max_length=2000)
    item_ids: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    decision: Literal["pending", "waived", "answered"] = "pending"
    answer: str = Field(default="", max_length=2000)


class RefinementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=64)
    type: ItemType
    range: TimeRange
    operation: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    confidence_level: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    enabled: bool = True
    duration_delta_ms: int = Field(default=0, strict=True)
    material_asset_id: int | None = Field(default=None, gt=0)
    material_start_ms: int = Field(default=0, ge=0, strict=True)
    audio_strategy: Literal["keep_source", "use_material"] = "use_material"
    cut_from: Literal["start", "end"] = "end"
    script: str = Field(default="", max_length=4000)
    subtitle_text: str = Field(default="", max_length=4000)
    risk: str = Field(default="", max_length=2000)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    status: Literal["PROPOSED", "HUMAN_EDITED", "CONFIRMED"] = "PROPOSED"

    edit_intent: Literal["replace_visual", "reduce_shot_share", "insert_content", "remove_pause", "remove_content", "compress_expression"] | None = None
    edit_group_id: str | None = Field(default=None, max_length=64)
    requirement_ids: list[str] = Field(default_factory=list, max_length=30)
    material_segments: list[MaterialSegment] = Field(default_factory=list, max_length=30)
    material_audio_policy: Literal["mute"] = "mute"
    narration_strategy: Literal["keep_source", "cut_source", "tts", "none"] = "keep_source"
    removed_ranges: list[TimeRange] = Field(default_factory=list, max_length=30)
    affected_cue_ids: list[str] = Field(default_factory=list, max_length=100)
    voiceover_id: int | None = Field(default=None, gt=0)
    tts_speaker: str | None = Field(default=None, max_length=120)
    subtitle_strategy: Literal["keep_visible", "remap", "regenerate", "none", "human_required"] = "keep_visible"
    background_strategy: Literal["keep_mixed", "keep_independent", "music_only", "drop_replaced_interval", "none"] = "keep_mixed"
    boundary_reviewed: bool = False
    narration_padding_reason: str = Field(default="", max_length=500)

    @field_validator("tts_speaker")
    @classmethod
    def require_chinese_speaker(cls, value):
        return validate_tts_speaker(value)

    @field_validator("operation", "reason", "item_id")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)


class DraftInput(RevisionInput):
    items: list[RefinementItem] = Field(default_factory=list, max_length=30)


class ConfirmInput(RevisionInput):
    plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_risks: bool = False


class PreviewInput(RevisionInput):
    plan_hash: str = Field(min_length=64, max_length=64)


class RequirementDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    decision: Literal["waived", "answered"]
    answer: str = Field(default="", max_length=2000)


class RequirementsInput(RevisionInput):
    user_brief: str | None = Field(default=None, min_length=1, max_length=2000)
    decisions: list[RequirementDecision] = Field(default_factory=list, max_length=30)
    accept_partial_evaluation: bool | None = None
    duration_preference: Literal["preserve", "shorten", "ai_recommend"] | None = None
    target_duration_ms: int | None = Field(default=None, gt=0, strict=True)
    tts_speaker: str | None = Field(default=None, max_length=120)

    _speaker = field_validator("tts_speaker")(validate_tts_speaker)

    @field_validator("user_brief")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("精修目标不能为空")
        return value.strip() if value else value


class VoiceoverInput(PreviewInput):
    item_id: str = Field(min_length=1, max_length=64)
