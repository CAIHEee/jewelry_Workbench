from pathlib import Path
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = Field(default="Flux Jewelry Design System", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    host: str = Field(default="0.0.0.0", alias="APP_HOST")
    port: int = Field(default=8000, alias="APP_PORT")
    debug: bool = Field(default=True, alias="APP_DEBUG")
    allowed_origins_raw: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        alias="APP_ALLOWED_ORIGINS",
    )
    public_base_url: str | None = Field(default=None, alias="APP_PUBLIC_BASE_URL")
    allowed_origin_regex: str | None = Field(default=None, alias="APP_ALLOWED_ORIGIN_REGEX")
    ai_default_provider: str = Field(default="flux", alias="AI_DEFAULT_PROVIDER")
    ai_upstream_platform: str = Field(default="apiyi", alias="AI_UPSTREAM_PLATFORM")
    ai_max_fusion_images: int = Field(default=6, alias="AI_MAX_FUSION_IMAGES")
    apiyi_api_key: str | None = Field(default=None, alias="APIYI_API_KEY")
    apiyi_base_url: str = Field(default="https://api.apiyi.com", alias="APIYI_BASE_URL")
    apiyi_openai_base_url: str = Field(default="https://api.apiyi.com/v1", alias="APIYI_OPENAI_BASE_URL")
    apiyi_gemini_base_url: str = Field(default="https://api.apiyi.com/v1beta", alias="APIYI_GEMINI_BASE_URL")
    aiapis_api_key: str | None = Field(default=None, alias="AIAPIS_API_KEY")
    aiapis_base_url: str = Field(default="https://aiapis.help/v1", alias="AIAPIS_BASE_URL")
    aiapis_timeout_seconds: float = Field(default=300.0, alias="AIAPIS_TIMEOUT_SECONDS")
    dmxapi_api_key: str | None = Field(default=None, alias="DMXAPI_API_KEY")
    dmxapi_base_url: str = Field(default="https://www.dmxapi.cn/v1", alias="DMXAPI_BASE_URL")
    dmxapi_timeout_seconds: float = Field(default=180.0, alias="DMXAPI_TIMEOUT_SECONDS")
    wuyin_api_key: str | None = Field(default=None, alias="WUYIN_API_KEY")
    wuyin_base_url: str = Field(default="https://api.wuyinkeji.com", alias="WUYIN_BASE_URL")
    wuyin_timeout_seconds: float = Field(default=120.0, alias="WUYIN_TIMEOUT_SECONDS")
    wuyin_poll_interval_seconds: float = Field(default=3.0, alias="WUYIN_POLL_INTERVAL_SECONDS")
    wuyin_poll_attempts: int = Field(default=40, alias="WUYIN_POLL_ATTEMPTS")
    ttapi_api_key: str | None = Field(default=None, alias="TTAPI_API_KEY")
    ttapi_flux_base_url: str = Field(default="https://api.ttapi.io", alias="TTAPI_FLUX_BASE_URL")
    ttapi_openai_base_url: str = Field(default="https://api.ttapi.org", alias="TTAPI_OPENAI_BASE_URL")
    ttapi_timeout_seconds: float = Field(default=120.0, alias="TTAPI_TIMEOUT_SECONDS")
    ttapi_poll_interval_seconds: float = Field(default=2.5, alias="TTAPI_POLL_INTERVAL_SECONDS")
    ttapi_poll_attempts: int = Field(default=24, alias="TTAPI_POLL_ATTEMPTS")
    database_url: str = Field(default="sqlite:///./data/app.db", alias="DATABASE_URL")
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_recycle_seconds: int = Field(default=3600, alias="DB_POOL_RECYCLE_SECONDS")
    auth_secret_key: str = Field(default="change-me-in-production", alias="AUTH_SECRET_KEY")
    auth_token_expire_hours: int = Field(default=24, alias="AUTH_TOKEN_EXPIRE_HOURS")
    auth_cookie_name: str = Field(default="jinma_auth_token", alias="AUTH_COOKIE_NAME")
    root_user_id: str = Field(default="00000000-0000-0000-0000-000000000001", alias="ROOT_USER_ID")
    root_username: str = Field(default="root", alias="ROOT_USERNAME")
    root_display_name: str = Field(default="系统管理员", alias="ROOT_DISPLAY_NAME")
    root_email: str = Field(default="root@example.com", alias="ROOT_EMAIL")
    root_default_password: str = Field(default="root123456", alias="ROOT_DEFAULT_PASSWORD")
    oss_provider: str = Field(default="aliyun", alias="OSS_PROVIDER")
    oss_bucket: str = Field(default="your_bucket_name", alias="OSS_BUCKET")
    oss_endpoint: str = Field(default="oss-cn-guangzhou.aliyuncs.com", alias="OSS_ENDPOINT")
    oss_region: str = Field(default="oss-cn-guangzhou", alias="OSS_REGION")
    oss_access_key_id: str | None = Field(default=None, alias="OSS_ACCESS_KEY_ID")
    oss_access_key_secret: str | None = Field(default=None, alias="OSS_ACCESS_KEY_SECRET")
    oss_signed_url_expire_seconds: int = Field(default=3600, alias="OSS_SIGNED_URL_EXPIRE_SECONDS")
    queue_redis_url: str = Field(default="redis://127.0.0.1:6379/0", alias="QUEUE_REDIS_URL")
    queue_name: str = Field(default="jinma-ai", alias="QUEUE_NAME")
    queue_job_timeout_seconds: int = Field(default=900, alias="QUEUE_JOB_TIMEOUT_SECONDS")
    queue_result_ttl_seconds: int = Field(default=3600, alias="QUEUE_RESULT_TTL_SECONDS")
    queue_user_max_active_jobs: int = Field(default=1, alias="QUEUE_USER_MAX_ACTIVE_JOBS")
    queue_root_max_active_jobs: int = Field(default=3, alias="QUEUE_ROOT_MAX_ACTIVE_JOBS")
    cache_job_status_ttl_seconds: int = Field(default=21600, alias="CACHE_JOB_STATUS_TTL_SECONDS")
    cache_job_dedupe_ttl_seconds: int = Field(default=180, alias="CACHE_JOB_DEDUPE_TTL_SECONDS")
    cache_model_catalog_ttl_seconds: int = Field(default=300, alias="CACHE_MODEL_CATALOG_TTL_SECONDS")
    cache_auth_me_ttl_seconds: int = Field(default=60, alias="CACHE_AUTH_ME_TTL_SECONDS")
    agent_llm_base_url: str = Field(default="https://api.deepseek.com", alias="AGENT_LLM_BASE_URL")
    agent_llm_api_key: str | None = Field(default=None, alias="AGENT_LLM_API_KEY")
    agent_llm_model: str = Field(default="deepseek-v4-flash", alias="AGENT_LLM_MODEL")
    agent_llm_timeout_seconds: float = Field(default=60.0, alias="AGENT_LLM_TIMEOUT_SECONDS")
    agent_llm_strict_tools: bool = Field(default=True, alias="AGENT_LLM_STRICT_TOOLS")
    agent_vision_llm_base_url: str | None = Field(default=None, alias="AGENT_VISION_LLM_BASE_URL")
    agent_vision_llm_api_key: str | None = Field(default=None, alias="AGENT_VISION_LLM_API_KEY")
    agent_vision_llm_model: str | None = Field(default=None, alias="AGENT_VISION_LLM_MODEL")
    agent_service_allowed_origins_raw: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        alias="AGENT_SERVICE_ALLOWED_ORIGINS",
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins_raw.split(",") if item.strip()]

    @property
    def cors_origin_regex(self) -> str | None:
        if self.allowed_origin_regex:
            return self.allowed_origin_regex.strip()
        if self.debug:
            return r"^https?://(?:localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+|192\.168\.\d+\.\d+)(?::\d+)?$"
        return None

    @property
    def oss_enabled(self) -> bool:
        return bool(
            self.oss_bucket
            and self.oss_endpoint
            and self.oss_region
            and self.oss_access_key_id
            and self.oss_access_key_secret
        )

    @property
    def agent_service_allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.agent_service_allowed_origins_raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
