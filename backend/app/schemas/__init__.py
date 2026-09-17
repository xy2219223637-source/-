from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Product ──────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    category: str = ""
    price: float = 0.0
    description: str = ""
    target_users: str = ""
    brand_name: str = ""
    ingredients: str = ""
    usage_method: str = ""
    specifications: str = ""
    image_urls: list[str] = Field(default_factory=list)
    detail_image_urls: list[str] = Field(default_factory=list)
    learned_profile_enabled: bool = True


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None
    target_users: Optional[str] = None
    brand_name: Optional[str] = None
    ingredients: Optional[str] = None
    usage_method: Optional[str] = None
    specifications: Optional[str] = None
    image_urls: Optional[list[str]] = None
    detail_image_urls: Optional[list[str]] = None
    learned_profile_enabled: Optional[bool] = None


class ProductOut(BaseModel):
    id: int
    name: str
    category: str
    price: float
    description: str
    target_users: str
    brand_name: str = ""
    ingredients: str = ""
    usage_method: str = ""
    specifications: str = ""
    image_urls: list[str] = []
    detail_image_urls: list[str] = []
    created_at: datetime
    updated_at: datetime
    learned_profile_enabled: bool = True

    model_config = {"from_attributes": True}


class BrandVisualProfileCreate(BaseModel):
    brand_name: str
    logo_url: str = ""
    primary_color: str = "#1C6F56"
    accent_color: str = "#E2EFE8"
    typography: str = "现代中文黑体，标题克制醒目"
    visual_keywords: list[str] = Field(default_factory=list)
    forbidden_elements: list[str] = Field(default_factory=list)
    tone_notes: str = ""


class BrandVisualProfileOut(BrandVisualProfileCreate):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


# ── Knowledge ────────────────────────────────────────────

class KnowledgeDocCreate(BaseModel):
    title: str
    doc_type: str = "general"
    content: str
    product_id: Optional[int] = None
    brand_name: str = ""


class KnowledgeDocOut(BaseModel):
    id: int
    product_id: Optional[int] = None
    brand_name: str = ""
    title: str
    doc_type: str
    filename: Optional[str] = None
    content: str
    chunk_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DesignSkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    scope: str = "general"
    category: str = ""
    brand_name: str = ""
    product_id: Optional[int] = None
    description: str = ""
    design_principles: str = ""
    module_guidance: str = ""
    visual_rules: str = ""
    copy_rules: str = ""
    negative_rules: str = ""
    primary_color: str = "#1f7258"
    accent_color: str = "#dceee5"
    enabled: bool = True


class DesignSkillOut(DesignSkillCreate):
    id: int
    version: int = 1
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LearnedDesignProfileOut(BaseModel):
    id: int
    brand_name: str
    category: str
    sample_count: int
    positive_count: int
    negative_count: int
    confidence: float
    learned_rules: dict[str, Any] = Field(default_factory=dict)
    status: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillCandidateOut(BaseModel):
    id: int
    profile_id: int
    name: str
    brand_name: str
    category: str
    confidence: float
    sample_count: int
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    published_skill_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class DetailPageTemplateCreate(BaseModel):
    project_id: int
    name: str = Field(..., min_length=1, max_length=256)
    description: str = ""


class DetailPageTemplateApply(BaseModel):
    product_id: int
    project_name: str = ""


class DetailPageTemplateOut(BaseModel):
    id: int
    name: str
    description: str
    category: str
    brand_name: str
    platform: str
    output_width: int
    output_height: int
    source_project_id: Optional[int] = None
    modules: list[dict[str, Any]] = Field(default_factory=list)
    variables: list[dict[str, Any]] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)
    usage_count: int
    enabled: bool
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class CreativeProjectCreate(BaseModel):
    product_id: int
    name: str = Field(..., min_length=1, max_length=256)
    brief: str = ""
    platform: str = "天猫"
    output_width: int = 750
    output_height: int = 1000


class CreativeProjectOut(CreativeProjectCreate):
    id: int
    status: str
    review_status: str = "draft"
    review_round: int = 0
    viewport: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class StoryboardModuleCreate(BaseModel):
    sort_order: int = 0
    module_type: str
    title: str
    objective: str = ""
    content_guidance: str = ""
    visual_direction: str = ""
    production_method: str = "ai_image"
    required: bool = False


class StoryboardModuleOut(StoryboardModuleCreate):
    id: int
    project_id: int
    status: str
    preview_node_id: Optional[str] = None
    final_node_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CreativePlanOut(BaseModel):
    id: int
    project_id: int
    product_understanding: dict[str, Any] = Field(default_factory=dict)
    strategy: dict[str, Any] = Field(default_factory=dict)
    status: str
    modules: list[StoryboardModuleOut] = Field(default_factory=list)


class StoryboardUpdateRequest(BaseModel):
    modules: list[StoryboardModuleCreate]


class StoryboardModuleSelectionRequest(BaseModel):
    node_id: str
    approve: bool = False


class StoryboardBatchCreate(BaseModel):
    module_ids: list[int] = Field(default_factory=list)


class CanvasNodeCreate(BaseModel):
    id: Optional[str] = None
    node_type: str
    parent_node_id: Optional[str] = None
    position_x: float = 0
    position_y: float = 0
    width: float = 260
    height: float = 320
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasNodeOut(CanvasNodeCreate):
    id: str
    project_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CanvasSaveRequest(BaseModel):
    nodes: list[CanvasNodeCreate]
    viewport: dict[str, Any] = Field(default_factory=dict)


class CreativeGenerateRequest(BaseModel):
    prompt: str = ""
    action: str = "generate"
    selected_node_ids: list[str] = Field(default_factory=list)
    parent_node_id: Optional[str] = None
    auto_select_materials: bool = True
    module_id: Optional[int] = None
    count: int = Field(default=3, ge=1, le=6)
    product_lock: str = Field(default="strict", pattern="^(strict|balanced|creative)$")
    variation_axis: str = Field(default="composition", pattern="^(composition|scene|color|model|lighting)$")
    generation_stage: str = Field(default="preview", pattern="^(preview|final)$")
    trigger_source: str = Field(default="manual", pattern="^(manual|batch|retry|quality_retry|production_queue)$")


class StoryboardQuickEditRequest(BaseModel):
    node_id: str
    replacement_node_id: Optional[str] = None
    headline: str = ""
    subtitle: str = ""
    zoom: float = Field(default=1.0, ge=1.0, le=2.0)
    offset_x: float = Field(default=0.0, ge=-1.0, le=1.0)
    offset_y: float = Field(default=0.0, ge=-1.0, le=1.0)
    text_x: float = Field(default=0.08, ge=0.0, le=0.9)
    text_y: float = Field(default=0.78, ge=0.0, le=0.95)
    font_size: int = Field(default=42, ge=18, le=96)
    text_color: str = "#183028"
    text_align: str = "left"
    text_background: bool = True


class StoryboardStyleRequest(BaseModel):
    name: str = "整套风格调整"
    primary_color: str = "#1C6F56"
    accent_color: str = "#E2EFE8"
    typography: str = "克制现代"
    whitespace: int = Field(default=50, ge=0, le=100)
    copy_density: int = Field(default=50, ge=0, le=100)


class CreativeGenerationOut(BaseModel):
    id: int
    project_id: int
    parent_node_id: Optional[str] = None
    prompt: str
    action: str
    selected_node_ids: list[str]
    provider: str
    status: str
    result_node_ids: list[str]
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CreativeFeedbackCreate(BaseModel):
    status: str
    reasons: list[str] = Field(default_factory=list)


class CreativeFeedbackOut(BaseModel):
    id: int
    project_id: int
    product_id: int
    node_id: str
    image_url: str
    status: str
    reasons: list[str] = Field(default_factory=list)
    weight: float
    visual_analysis: Optional[dict[str, Any]] = None
    learning_status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductAssetOut(BaseModel):
    id: int
    product_id: Optional[int] = None
    name: str
    asset_type: str
    file_url: str
    mime_type: str
    description: str
    tags: list[str] = []
    material_role: str = "auto"
    priority: int = 0
    locked: bool = False
    excluded: bool = False
    benchmark_role: str = "none"
    protection: dict[str, Any] = {}
    created_at: datetime

    model_config = {"from_attributes": True}


class ProductAssetUpdate(BaseModel):
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    material_role: str = "auto"
    priority: int = 0
    locked: bool = False
    excluded: bool = False
    benchmark_role: str = "none"
    protection: dict[str, Any] = Field(default_factory=dict)


class RegisterRequest(BaseModel):
    tenant_name: str = Field(..., min_length=2, max_length=200)
    tenant_code: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=5, max_length=320)
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1\d{10}$")
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, str]
    tenant: dict[str, str]
