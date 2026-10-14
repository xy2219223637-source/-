"""Ecommerce refinement records are isolated from legacy product video workspaces."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EcommerceVideoRefinementTask(Base):
    __tablename__ = "ecommerce_video_refinement_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_asset_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_assets.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_products.id"), nullable=False, index=True)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    user_brief: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    focus: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED", index=True)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmed_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EcommerceVideoRefinementSnapshot(Base):
    __tablename__ = "ecommerce_video_refinement_snapshots"
    __table_args__ = (UniqueConstraint("task_id", name="ecommerce_uq_refinement_workspace_task_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_tasks.id"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    is_mock: Mapped[bool] = mapped_column(nullable=False, default=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class EcommerceVideoRefinementPlan(Base):
    __tablename__ = "ecommerce_video_refinement_plans"
    __table_args__ = (UniqueConstraint("task_id", "revision", name="ecommerce_uq_refinement_workspace_plan_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_tasks.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_snapshots.id"), nullable=False)
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EcommerceVideoRefinementPreview(Base):
    __tablename__ = "ecommerce_video_refinement_previews"
    __table_args__ = (UniqueConstraint("task_id", "revision", name="ecommerce_uq_refinement_workspace_preview_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_tasks.id"), nullable=False, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_plans.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    output_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    validation_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    recipe_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class EcommerceVideoRefinementVersion(Base):
    __tablename__ = "ecommerce_video_refinement_versions"
    __table_args__ = (UniqueConstraint("preview_id", name="ecommerce_uq_refinement_workspace_accepted_preview"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_tasks.id"), nullable=False)
    preview_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_previews.id"), nullable=False)
    parent_asset_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_assets.id"), nullable=False)
    output_asset_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_assets.id"), nullable=False, unique=True)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_snapshots.id"), nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_plans.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EcommerceVideoRefinementRequest(Base):
    """Immutable requirements and product material snapshots."""
    __tablename__ = "ecommerce_video_refinement_requests"
    __table_args__ = (UniqueConstraint("task_id", "revision", name="ecommerce_uq_refinement_request_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_tasks.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    options_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EcommerceVideoRefinementAnalysisJob(Base):
    __tablename__ = "ecommerce_video_refinement_analysis_jobs"
    __table_args__ = (UniqueConstraint("task_id", "revision", name="ecommerce_uq_refinement_analysis_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_tasks.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    options_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    progress_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    batches_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EcommerceVideoRefinementVoiceover(Base):
    __tablename__ = "ecommerce_video_refinement_voiceovers"
    __table_args__ = (UniqueConstraint("tenant_id", "task_id", "fingerprint", name="ecommerce_uq_refinement_voiceover_content"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_refinement_tasks.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    script: Mapped[str] = mapped_column(Text, nullable=False)
    parameters_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    object_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timing_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EcommerceVideoLineageSegment(Base):
    __tablename__ = "ecommerce_video_lineage_segments"
    __table_args__ = (UniqueConstraint("target_asset_id", "segment_index", name="ecommerce_uq_video_lineage_segment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_asset_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_assets.id"), nullable=False, index=True)
    original_asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ecommerce_video_assets.id"), nullable=True, index=True)
    target_asset_id: Mapped[int] = mapped_column(ForeignKey("ecommerce_video_assets.id"), nullable=False, index=True)
    # Snapshot identifiers intentionally do not depend on replaceable render jobs.
    plan_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    variant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start: Mapped[float] = mapped_column(Float, nullable=False)
    source_end: Mapped[float] = mapped_column(Float, nullable=False)
    target_start: Mapped[float] = mapped_column(Float, nullable=False)
    target_end: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
