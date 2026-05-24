import os
import re
from pathlib import Path
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env"
RUNTIME_CONFIG_FILE_PATH = Path(os.environ["RUNTIME_CONFIG_FILE"]).expanduser() if os.environ.get("RUNTIME_CONFIG_FILE") else None


RUNTIME_CONFIG_KEYS = frozenset(
    {
        "AI_DEFAULT_PROVIDER",
        "AI_UPSTREAM_PLATFORM",
        "AI_MAX_FUSION_IMAGES",
        "APIYI_API_KEY",
        "APIYI_ACTIVE",
        "APIYI_BASE_URL",
        "APIYI_OPENAI_BASE_URL",
        "APIYI_GEMINI_BASE_URL",
        "APIYI_TIMEOUT_SECONDS",
        "CLOSEAI_API_KEY",
        "CLOSEAI_ACTIVE",
        "CLOSEAI_BASE_URL",
        "CLOSEAI_TIMEOUT_SECONDS",
        "TTAPI_API_KEY",
        "TTAPI_ACTIVE",
        "TTAPI_OPENAI_BASE_URL",
        "TTAPI_TIMEOUT_SECONDS",
        "TTAPI_POLL_INTERVAL_SECONDS",
        "TTAPI_POLL_ATTEMPTS",
        "AGENT_LLM_BASE_URL",
        "AGENT_LLM_API_KEY",
        "AGENT_LLM_MODEL",
        "AGENT_LLM_TIMEOUT_SECONDS",
        "AGENT_LLM_STRICT_TOOLS",
        "DASHSCOPE_API_KEY",
        "MULTI_VIEW_PROMPT_MODEL",
        "MULTI_VIEW_PROMPT_THINKING_BUDGET",
        "AGENT_VISION_LLM_BASE_URL",
        "AGENT_VISION_LLM_API_KEY",
        "AGENT_VISION_LLM_MODEL",
    }
)


def is_runtime_config_key(key: str) -> bool:
    return key in RUNTIME_CONFIG_KEYS or key.startswith("CUSTOM_GROUP_")


def get_runtime_config_file_path() -> Path | None:
    return RUNTIME_CONFIG_FILE_PATH


def _parse_runtime_config_file() -> dict[str, str]:
    runtime_path = get_runtime_config_file_path()
    if runtime_path is None or not runtime_path.exists():
        return {}

    result: dict[str, str] = {}
    with open(runtime_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
            if match:
                key = match.group(1)
                if is_runtime_config_key(key):
                    result[key] = match.group(2).strip().strip('"').strip("'")
    return result


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 允许 .env 中存在未定义的字段（如 *_ACTIVE 标记）
    )

    app_name: str = Field(default="Jinma Jewelry Design System", alias="APP_NAME")
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
    ai_default_provider: str = Field(default="apiyi", alias="AI_DEFAULT_PROVIDER")
    ai_upstream_platform: str = Field(default="apiyi", alias="AI_UPSTREAM_PLATFORM")
    ai_max_fusion_images: int = Field(default=6, alias="AI_MAX_FUSION_IMAGES")
    apiyi_api_key: str | None = Field(default=None, alias="APIYI_API_KEY")
    apiyi_active: str | None = Field(default=None, alias="APIYI_ACTIVE")  # 密钥激活状态
    apiyi_base_url: str = Field(default="https://api.apiyi.com", alias="APIYI_BASE_URL")
    apiyi_openai_base_url: str = Field(default="https://api.apiyi.com/v1", alias="APIYI_OPENAI_BASE_URL")
    apiyi_gemini_base_url: str = Field(default="https://api.apiyi.com/v1beta", alias="APIYI_GEMINI_BASE_URL")
    apiyi_timeout_seconds: float = Field(default=600.0, alias="APIYI_TIMEOUT_SECONDS")
    closeai_api_key: str | None = Field(default=None, alias="CLOSEAI_API_KEY")
    closeai_active: str | None = Field(default=None, alias="CLOSEAI_ACTIVE")  # 密钥激活状态
    closeai_base_url: str = Field(default="https://api.openai-proxy.org/v1", alias="CLOSEAI_BASE_URL")
    closeai_timeout_seconds: float = Field(default=1200.0, alias="CLOSEAI_TIMEOUT_SECONDS")
    ttapi_api_key: str | None = Field(default=None, alias="TTAPI_API_KEY")
    ttapi_active: str | None = Field(default=None, alias="TTAPI_ACTIVE")  # 密钥激活状态
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
    queue_job_timeout_seconds: int = Field(default=1500, alias="QUEUE_JOB_TIMEOUT_SECONDS")
    queue_result_ttl_seconds: int = Field(default=3600, alias="QUEUE_RESULT_TTL_SECONDS")
    queue_user_max_active_jobs: int = Field(default=1, alias="QUEUE_USER_MAX_ACTIVE_JOBS")
    queue_root_max_active_jobs: int = Field(default=3, alias="QUEUE_ROOT_MAX_ACTIVE_JOBS")
    cache_job_status_ttl_seconds: int = Field(default=21600, alias="CACHE_JOB_STATUS_TTL_SECONDS")
    cache_job_dedupe_ttl_seconds: int = Field(default=180, alias="CACHE_JOB_DEDUPE_TTL_SECONDS")
    cache_model_catalog_ttl_seconds: int = Field(default=300, alias="CACHE_MODEL_CATALOG_TTL_SECONDS")
    cache_auth_me_ttl_seconds: int = Field(default=60, alias="CACHE_AUTH_ME_TTL_SECONDS")
    agent_llm_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode", alias="AGENT_LLM_BASE_URL")
    agent_llm_api_key: str | None = Field(default=None, alias="AGENT_LLM_API_KEY")
    agent_llm_model: str = Field(default="qwen3.6-flash", alias="AGENT_LLM_MODEL")
    agent_llm_timeout_seconds: float = Field(default=60.0, alias="AGENT_LLM_TIMEOUT_SECONDS")
    agent_llm_strict_tools: bool = Field(default=True, alias="AGENT_LLM_STRICT_TOOLS")
    dashscope_api_key: str | None = Field(default=None, alias="DASHSCOPE_API_KEY")
    multi_view_prompt_model: str = Field(default="qwen3-vl-plus", alias="MULTI_VIEW_PROMPT_MODEL")
    multi_view_prompt_thinking_budget: int = Field(default=32768, alias="MULTI_VIEW_PROMPT_THINKING_BUDGET")
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

    # ==================== 密钥激活状态辅助方法 ====================

    def is_provider_active(self, provider: str) -> bool:
        """检查指定供应商是否激活"""
        active_key = f"{provider.lower()}_active"
        active_value = getattr(self, active_key, None)
        if active_value is not None:
            return active_value.strip().lower() == "true"
        # 如果没有 ACTIVE 标记，默认激活（向后兼容）
        return True

    @property
    def is_apiyi_active(self) -> bool:
        return self.is_provider_active("apiyi")

    @property
    def is_closeai_active(self) -> bool:
        return self.is_provider_active("closeai")

    @property
    def is_ttapi_active(self) -> bool:
        return self.is_provider_active("ttapi")


@lru_cache
def get_settings() -> Settings:
    return Settings(**_parse_runtime_config_file())
