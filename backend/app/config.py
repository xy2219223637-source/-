from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
    video_library_max_duration_seconds: float = 600.0
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_llm_model: str = ""
    ark_video_model: str = "doubao-seedance-1-0-pro-250528"
    ark_video_resolution: str = "720p"
    video_model_provider: str = "volcengine-ark"
    doubao_tts_app_id: str = ""
    doubao_tts_access_token: str = ""
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
    storage_cache_dir: str = ""
    storage_cache_max_gb: float = 20.0
    storage_cache_ttl_hours: float = 24.0

@lru_cache
def get_settings() -> Settings:
    return Settings()
