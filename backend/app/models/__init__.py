from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, JSON, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_users: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brand_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    ingredients: Mapped[str] = mapped_column(Text, nullable=False, default="")
    usage_method: Mapped[str] = mapped_column(Text, nullable=False, default="")
    specifications: Mapped[str] = mapped_column(Text, nullable=False, default="")
    learned_profile_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    image_urls: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    detail_image_urls: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    knowledge_docs: Mapped[list["KnowledgeDocument"]] = relationship(
        "KnowledgeDocument", back_populates="product", cascade="all, delete-orphan"
    )
    assets: Mapped[list["ProductAsset"]] = relationship(
        "ProductAsset", back_populates="product", cascade="all, delete-orphan"
    )


class VideoProject(Base):
    """Independent talking-video workspace; existing creative flows never depend on it."""

    __tablename__ = "video_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="小红书")
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    style: Mapped[str] = mapped_column(String(64), nullable=False, default="真人种草")
    presenter_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="数字人口播")
    voice: Mapped[str] = mapped_column(String(64), nullable=False, default="自然女声")
    selling_points: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extra_requirements: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DirectorProject(Base):
    """Reference-led scripting and shot-planning workspace."""

    __tablename__ = "director_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="reference")
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    source_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="中文")
    product_service: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    selling_points: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    progress_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="created")
    analysis: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sections: Mapped[list["DirectorSection"]] = relationship(
        "DirectorSection", back_populates="project", cascade="all, delete-orphan"
    )


class DirectorCaptureSession(Base):
    """Short-lived authority for a browser extension to upload one source video."""

    __tablename__ = "director_capture_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("director_projects.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    source_page_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_platform: Mapped[str] = mapped_column(String(32), nullable=False, default="generic")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="waiting", index=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ShootingTask(Base):
    """A script assignment sent to an external shooting team."""

    __tablename__ = "shooting_tasks"
    __table_args__ = (
        Index("uq_shooting_notification_uuid", "tenant_id", "notification_uuid", unique=True),
        Index("uq_shooting_active_recipient", "tenant_id", "project_id", "recipient_type", "recipient_id", unique=True,
              postgresql_where=text("recipient_id <> '' AND status IN ('pending', 'in_progress')"),
              sqlite_where=text("recipient_id <> '' AND status IN ('pending', 'in_progress')")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("director_projects.id"), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_name: Mapped[str] = mapped_column(String(128), nullable=False)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False)
    department_name: Mapped[str] = mapped_column(String(128), nullable=False)
    assignee_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    assignee_name: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    recipient_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    recipient_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    notification_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_sent")
    notification_uuid: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    notification_app_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    notification_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    feishu_message_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    notification_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notification_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DirectorSection(Base):
    __tablename__ = "director_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("director_projects.id"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    script_lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    shooting_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped["DirectorProject"] = relationship("DirectorProject", back_populates="sections")
    assets: Mapped[list["DirectorAsset"]] = relationship(
        "DirectorAsset", back_populates="section", cascade="all, delete-orphan"
    )
    shots: Mapped[list["DirectorShot"]] = relationship(
        "DirectorShot", back_populates="section", cascade="all, delete-orphan"
    )


class DirectorShot(Base):
    """A scene-detected sub-shot enriched by ASR and multimodal analysis."""

    __tablename__ = "director_shots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("director_projects.id"), nullable=False, index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("director_sections.id"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transcript: Mapped[str] = mapped_column(Text, nullable=False, default="")
    story_role: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scene: Mapped[str] = mapped_column(Text, nullable=False, default="")
    shot_size: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    camera: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    clip_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    thumbnail_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    analysis: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    section: Mapped["DirectorSection"] = relationship("DirectorSection", back_populates="shots")


class DirectorAsset(Base):
    __tablename__ = "director_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("director_projects.id"), nullable=False, index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("director_sections.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="reference")
    media_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="image")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    thumbnail_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    section: Mapped["DirectorSection"] = relationship("DirectorSection", back_populates="assets")


class VideoEditProject(Base):
    """Material-first automatic editing project used by performance video PoC."""

    __tablename__ = "video_edit_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    variant_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False, default="9:16")
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="投流素材测试")
    edit_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="smart_compose")
    structure: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    constraints: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoAsset(Base):
    __tablename__ = "video_assets"
    __table_args__ = (UniqueConstraint("tenant_id", "asset_id", name="uq_tenant_video_asset"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("video_edit_projects.id"), nullable=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("video_categories.id"), nullable=True, index=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(256), nullable=False, default="manual_upload")
    library_type: Mapped[str] = mapped_column(String(32), nullable=False, default="semi_finished", index=True)
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="material", index=True)
    generation_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    origin_asset_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reusable_status: Mapped[str] = mapped_column(String(32), nullable=False, default="review_required")
    burned_subtitles: Mapped[bool] = mapped_column(nullable=False, default=False)
    has_background_music: Mapped[bool] = mapped_column(nullable=False, default=False)
    object_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fps: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    has_audio: Mapped[bool] = mapped_column(nullable=False, default=False)
    ai_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    manual_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    removed_ai_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    excluded: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoCategory(Base):
    __tablename__ = "video_categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "object_type", "code", name="uq_video_category_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("video_categories.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoTagGroup(Base):
    __tablename__ = "video_tag_groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_video_tag_group_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    applies_to: Mapped[str] = mapped_column(String(32), nullable=False, default="both", index=True)
    inherit_to_final: Mapped[bool] = mapped_column(nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoTag(Base):
    __tablename__ = "video_tags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope", "owner_id", "code", name="uq_video_tag_owner_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("video_tag_groups.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="public", index=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    applies_to: Mapped[str] = mapped_column(String(32), nullable=False, default="both", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoAssetTag(Base):
    __tablename__ = "video_asset_tags"
    __table_args__ = (
        UniqueConstraint("asset_id", "tag_id", name="uq_video_asset_tag"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("video_assets.id"), nullable=False, index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("video_tags.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    confirmed: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VideoFilterPreset(Base):
    __tablename__ = "video_filter_presets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_id", "object_type", "name", name="uq_video_filter_preset_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoTaxonomyState(Base):
    __tablename__ = "video_taxonomy_states"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    initialized_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VideoEditProjectAsset(Base):
    __tablename__ = "video_edit_project_assets"
    __table_args__ = (UniqueConstraint("project_id", "asset_id", name="uq_edit_project_asset"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("video_edit_projects.id"), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("video_assets.id"), nullable=False, index=True)
    included: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VideoAssetAnalysis(Base):
    __tablename__ = "video_asset_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("video_assets.id"), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="placeholder")
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="placeholder-v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    analysis_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_video_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class VideoUploadSession(Base):
    __tablename__ = "video_upload_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    video_asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("video_assets.id"), nullable=True, index=True)
    library_type: Mapped[str] = mapped_column(String(32), nullable=False, default="semi_finished")
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    multipart_upload_id: Mapped[str] = mapped_column(String(256), nullable=False)
    part_size: Mapped[int] = mapped_column(Integer, nullable=False)
    part_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploading")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class VideoHighlight(Base):
    __tablename__ = "video_highlights"
    __table_args__ = (UniqueConstraint("tenant_id", "highlight_id", name="uq_tenant_video_highlight"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("video_assets.id"), nullable=False, index=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("video_asset_analyses.id"), nullable=False, index=True)
    highlight_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recommended_roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    content_role: Mapped[str] = mapped_column(String(64), nullable=False, default="core_content")
    track_role: Mapped[str] = mapped_column(String(16), nullable=False, default="b_roll")
    speech_present: Mapped[bool] = mapped_column(nullable=False, default=False)
    lip_visible: Mapped[bool] = mapped_column(nullable=False, default=False)
    speech_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    speaker_id: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    audio_strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="mute")
    shot_type: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    selling_points: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EditingPlanVersion(Base):
    __tablename__ = "editing_plan_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_edit_plan_project_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("video_edit_projects.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="valid")
    validation_errors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VideoRenderJob(Base):
    __tablename__ = "video_render_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("video_edit_projects.id"), nullable=False, index=True)
    plan_version_id: Mapped[int] = mapped_column(ForeignKey("editing_plan_versions.id"), nullable=False, index=True)
    variant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    output_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BrandVisualProfile(Base):
    __tablename__ = "brand_visual_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "brand_name", name="uq_tenant_brand_visual"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    brand_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    logo_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    primary_color: Mapped[str] = mapped_column(String(16), nullable=False, default="#1C6F56")
    accent_color: Mapped[str] = mapped_column(String(16), nullable=False, default="#E2EFE8")
    typography: Mapped[str] = mapped_column(String(256), nullable=False, default="现代中文黑体，标题克制醒目")
    visual_keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    forbidden_elements: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tone_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("products.id"), nullable=True, index=True
    )
    brand_name: Mapped[str] = mapped_column(String(256), nullable=False, default="", index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), default="general")
    # product_manual / brand_material / historical_detail / general
    filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    product: Mapped[Optional["Product"]] = relationship(
        "Product", back_populates="knowledge_docs"
    )
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk", back_populates="document", cascade="all, delete-orphan"
    )


class DesignSkill(Base):
    __tablename__ = "design_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    brand_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    design_principles: Mapped[str] = mapped_column(Text, nullable=False, default="")
    module_guidance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visual_rules: Mapped[str] = mapped_column(Text, nullable=False, default="")
    copy_rules: Mapped[str] = mapped_column(Text, nullable=False, default="")
    negative_rules: Mapped[str] = mapped_column(Text, nullable=False, default="")
    primary_color: Mapped[str] = mapped_column(String(16), nullable=False, default="#1f7258")
    accent_color: Mapped[str] = mapped_column(String(16), nullable=False, default="#dceee5")
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LearnedDesignProfile(Base):
    __tablename__ = "learned_design_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "brand_name", "category", name="uq_learned_brand_category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    brand_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    positive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    negative_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    learned_rules: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="observing")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SkillCandidate(Base):
    __tablename__ = "skill_candidates"
    __table_args__ = (UniqueConstraint("tenant_id", "profile_id", name="uq_candidate_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("learned_design_profiles.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    brand_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    published_skill_id: Mapped[Optional[int]] = mapped_column(ForeignKey("design_skills.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DetailPageTemplate(Base):
    __tablename__ = "detail_page_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    brand_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="天猫")
    output_width: Mapped[int] = mapped_column(Integer, nullable=False, default=750)
    output_height: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    source_project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("creative_projects.id"), nullable=True)
    modules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    variables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    conditions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_revision_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreativeProject(Base):
    __tablename__ = "creative_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    brief: Mapped[str] = mapped_column(Text, nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="天猫")
    output_width: Mapped[int] = mapped_column(Integer, nullable=False, default=750)
    output_height: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    review_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    viewport: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreativePlan(Base):
    __tablename__ = "creative_plans"
    __table_args__ = (UniqueConstraint("project_id", name="uq_creative_plan_project"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    product_understanding: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    strategy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StoryboardModule(Base):
    __tablename__ = "storyboard_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    module_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_guidance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visual_direction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    production_method: Mapped[str] = mapped_column(String(32), nullable=False, default="ai_image")
    required: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    preview_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    final_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CanvasNode(Base):
    __tablename__ = "canvas_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    position_x: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    position_y: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    width: Mapped[float] = mapped_column(Float, nullable=False, default=260)
    height: Mapped[float] = mapped_column(Float, nullable=False, default=320)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreativeGeneration(Base):
    __tablename__ = "creative_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    parent_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False, default="generate")
    selected_node_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="local_demo")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    result_node_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreativeBatchJob(Base):
    __tablename__ = "creative_batch_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    module_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    module_results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_module_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stop_requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreativeFeedback(Base):
    __tablename__ = "creative_feedback"
    __table_args__ = (UniqueConstraint("project_id", "node_id", name="uq_project_node_feedback"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("canvas_nodes.id"), nullable=False, index=True)
    image_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    visual_analysis: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    learning_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    document: Mapped["KnowledgeDocument"] = relationship(
        "KnowledgeDocument", back_populates="chunks"
    )


class ProductAsset(Base):
    __tablename__ = "product_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("products.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False, default="product_image")
    file_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    material_role: Mapped[str] = mapped_column(String(64), nullable=False, default="auto")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked: Mapped[bool] = mapped_column(nullable=False, default=False)
    excluded: Mapped[bool] = mapped_column(nullable=False, default=False)
    benchmark_role: Mapped[str] = mapped_column(String(64), nullable=False, default="none")
    protection: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    product: Mapped[Optional["Product"]] = relationship("Product", back_populates="assets")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class DesignSkillVersion(Base):
    __tablename__ = "design_skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("design_skills.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    performance_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProviderBillingRecord(Base):
    __tablename__ = "provider_billing_records"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "external_id", name="uq_provider_bill"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="volcengine_ark")
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    billing_date: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    amount_cny: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProjectReview(Base):
    __tablename__ = "project_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProductFact(Base):
    __tablename__ = "product_facts"
    __table_args__ = (UniqueConstraint("tenant_id", "product_id", "fact_key", name="uq_product_fact_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    fact_key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    source_ref: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    conflict_values: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confirmed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProjectSnapshot(Base):
    __tablename__ = "project_snapshots"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_project_snapshot_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="submit")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    diff_from_previous: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProductionQueueTask(Base):
    __tablename__ = "production_queue_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(64), nullable=False, default="member")
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    tenant_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SkuBatch(Base):
    __tablename__ = "sku_batches"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    template_id: Mapped[Optional[int]] = mapped_column(ForeignKey("detail_page_templates.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="天猫")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SkuBatchItem(Base):
    __tablename__ = "sku_batch_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("sku_batches.id"), nullable=False, index=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sku: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), nullable=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("creative_projects.id"), nullable=True)
    queue_task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("production_queue_tasks.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApprovalIssue(Base):
    __tablename__ = "approval_issues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("storyboard_modules.id"), nullable=False, index=True)
    source_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resolved_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    action: Mapped[str] = mapped_column(String(32), nullable=False, default="regenerate")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    region: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    assignee_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    blocks_finalize: Mapped[bool] = mapped_column(nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class QualityRegressionRun(Base):
    __tablename__ = "quality_regression_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    suite_version: Mapped[str] = mapped_column(String(32), nullable=False, default="commerce-v1")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QualityRuleSet(Base):
    __tablename__ = "quality_rule_sets"
    __table_args__ = (UniqueConstraint("tenant_id", "category", name="uq_quality_rules_category"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="general")
    thresholds: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class QualityRuleVersion(Base):
    __tablename__ = "quality_rule_versions"
    __table_args__ = (UniqueConstraint("rule_set_id", "version", name="uq_quality_rule_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_set_id: Mapped[int] = mapped_column(ForeignKey("quality_rule_sets.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QualityFeedback(Base):
    __tablename__ = "quality_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("creative_projects.id"), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    feedback_type: Mapped[str] = mapped_column(String(32), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RegressionSample(Base):
    __tablename__ = "regression_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="general")
    case_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    input_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    accepted_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    failure_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(nullable=False, default=False)
    must_change_password: Mapped[bool] = mapped_column(nullable=False, default=False)
    job_title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    employee_no: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TenantMember(Base):
    __tablename__ = "tenant_members"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("member_groups.id"), nullable=True, index=True)
    role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tenant_roles.id"), nullable=True, index=True)


class TenantRole(Base):
    __tablename__ = "tenant_roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tenant_role_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, default="custom")
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_system: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tenant_team_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MemberGroup(Base):
    __tablename__ = "member_groups"
    __table_args__ = (UniqueConstraint("team_id", "name", name="uq_team_group_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
