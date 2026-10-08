from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_api_base: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"
    llm_vision_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dimension: int = 1024
    embedding_mode: str = "standard"
    llm_mock_mode: bool = True
    llm_disable_thinking: bool = True
    llm_timeout_seconds: float = 90.0
    image_generation_base_url: str = ""
    image_generation_api_key: str = ""
    image_generation_model: str = ""
    # Image generation is deliberately isolated from the text/video providers.
    # Supported values: ark_seedream, apilio.  An empty value preserves the
    # previous local-demo behaviour when no image model is configured.
    image_generation_provider: str = ""
    apilio_image_base_url: str = "https://api.apilio.ai/v1"
    apilio_image_api_key: str = ""
    apilio_image_model: str = ""
    apilio_image_quality: str = "high"
    image_product_model: str = ""
    image_portrait_model: str = ""
    image_edit_model: str = ""
    image_upscale_model: str = ""
    image_generation_timeout_seconds: float = 240.0
    image_generation_watermark: bool = False
    image_generation_unit_cost_cny: float = 0.2
    generation_parameter_version: str = "commerce-image-v2"
    product_protection_enabled: bool = False
    approval_webhook_url: str = ""
    volc_billing_api_url: str = ""
    volc_billing_api_token: str = ""
    volc_billing_account_id: str = ""
    tenant_monthly_budget_cny: float = 1000.0
    tenant_max_concurrent_generations: int = 2
    generation_max_attempts: int = 3
    max_upload_mb: int = 25
    director_video_max_upload_mb: int = 300
    director_capture_session_minutes: int = 10
    director_scene_threshold: float = 0.36
    director_min_shot_seconds: float = 0.65
    director_vision_batch_size: int = 4
    director_ffmpeg_path: str = ""
    director_ffprobe_path: str = ""
    director_keyframe_min_count: int = 3
    director_keyframe_max_count: int = 5
    director_asr_provider: str = "auto"
    director_asr_required: bool = False
    director_asr_api_base: str = ""
    director_asr_api_key: str = ""
    director_asr_model: str = "whisper-1"
    director_asr_language: str = "zh"
    director_asr_resource_id: str = "volc.seedasr.auc"
    director_asr_poll_interval_seconds: float = 1.5
    director_asr_poll_timeout_seconds: float = 180.0
    director_asr_local_device: str = "auto"
    director_asr_local_compute_type: str = "int8"
    video_library_max_duration_seconds: float = 600.0
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_llm_model: str = ""
    ark_video_model: str = "doubao-seedance-1-0-pro-250528"
    ark_video_resolution: str = "720p"
    video_model_provider: str = "volcengine-ark"
    doubao_tts_api_key: str = ""
    doubao_tts_resource_id: str = "seed-tts-2.0"
    doubao_tts_speaker: str = "zh_female_shuangkuaisisi_uranus_bigtts"
    doubao_tts_speaker_male: str = "zh_male_m191_uranus_bigtts"
    auto_edit_audio_mode: str = "smart_mix"
    whisper_enabled: bool = True
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    auth_secret: str = "change-this-secret-in-production"
    access_token_hours: int = 24

    # Unset means enabled when credentials are present; False is an explicit off switch.
    feishu_enabled: Optional[bool] = None
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_system_tenant_id: str = ""
    app_public_base_url: str = ""
    feishu_log_dir: str = ""
    feishu_log_raw: bool = True

    qianchuan_enabled: bool = False
    qianchuan_app_id: str = ""
    qianchuan_app_secret: str = ""
    qianchuan_authorization_url: str = ""
    qianchuan_redirect_uri: str = ""
    qianchuan_scope: str = ""
    qianchuan_token_encryption_key: str = ""
    qianchuan_log_material_response: bool = True
    # Account/material totals may include campaigns run by other operators.
    qianchuan_allow_account_material_aggregate: bool = False
    # Full-domain delivery is a sidecar flow. Read, write and worker gates are
    # deliberately independent and default off.
    qianchuan_plan_enabled: bool = False
    qianchuan_plan_write_enabled: bool = False
    qianchuan_plan_worker_enabled: bool = False
    video_repair_ai_enabled: bool = False
    video_repair_ai_provider: str = "openai_compatible"
    video_repair_ai_model: str = ""
    video_repair_ai_timeout_seconds: float = 45.0
    video_repair_ai_max_keyframes: int = 12
    video_repair_ai_prompt_version: str = "video-repair-diagnosis-v2"
    video_repair_context_pre_ms: int = 4000
    video_repair_context_post_ms: int = 4000
    video_repair_context_max_ms: int = 8000
    video_repair_recommend_score: float = 0.75
    video_repair_selectable_score: float = 0.60
    video_repair_v4_enabled: bool = False
    video_repair_async_worker_enabled: bool = False
    video_repair_multimodal_enabled: bool = False
    video_repair_audio_replace_enabled: bool = False
    video_repair_content_insert_enabled: bool = False
    video_repair_image_motion_enabled: bool = False

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    database_url: str = "postgresql+psycopg://localhost/enrui_ai_commerce_agent"
    require_postgres: bool = True
    require_online_services: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_recycle_seconds: int = 1800

    redis_url: str = ""
    redis_key_prefix: str = "enrui-ai-commerce-agent:"

    storage_provider: str = "aliyun_oss"
    aliyun_oss_region: str = ""
    aliyun_oss_endpoint: str = ""
    aliyun_oss_access_key_id: str = ""
    aliyun_oss_access_key_secret: str = ""
    aliyun_oss_bucket_name: str = ""
    aliyun_oss_prefix: str = "enrui-ai-commerce-agent/"
    private_video_tenants: str = ""
    private_video_s3_env_file: str = ""
    private_video_s3_endpoint: str = ""
    private_video_s3_public_endpoint: str = ""
    private_video_s3_access_key_id: str = ""
    private_video_s3_access_key_secret: str = ""
    private_video_s3_bucket_name: str = ""
    private_video_s3_region: str = "us-east-1"
    private_video_s3_prefix: str = "enrui-ai-commerce-agent/"
    storage_cache_dir: str = ""
    storage_cache_max_gb: float = 20.0
    storage_cache_ttl_hours: float = 24.0

@lru_cache
def get_settings() -> Settings:
    return Settings()
