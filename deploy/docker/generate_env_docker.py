from __future__ import annotations

import argparse
from pathlib import Path
import socket


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ENV_PATH = ROOT / "backend" / ".env"
CLOUD_ENV_PATH = ROOT / "cloud_env" / "env.docker"
DOCKER_TEMPLATE_PATH = ROOT / "deploy" / "docker" / ".env.docker.example"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def detect_host_origins() -> str:
    candidates = ["http://localhost", "http://127.0.0.1"]
    try:
        hostname = socket.gethostname()
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            if family != socket.AF_INET:
                continue
            ip = sockaddr[0]
            if ip.startswith(("127.", "172.")) or ip == "0.0.0.0":
                continue
            origin = f"http://{ip}"
            if origin not in candidates:
                candidates.append(origin)
    except OSError:
        pass
    return ",".join(candidates)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate .env.docker from template defaults, backend/.env, and optional cloud_env/env.docker.")
    parser.add_argument("--output", required=True, help="Output .env.docker path")
    parser.add_argument("--backend-image", default="jinma-backend:offline", help="Backend image tag to write")
    parser.add_argument("--nginx-image", default="jinma-nginx:offline", help="Nginx image tag to write")
    args = parser.parse_args()

    template_values = parse_env_file(DOCKER_TEMPLATE_PATH)
    backend_values = parse_env_file(BACKEND_ENV_PATH)
    cloud_values = parse_env_file(CLOUD_ENV_PATH)
    output_path = Path(args.output).resolve()

    merged = dict(template_values)
    merged.update(backend_values)
    merged.update(cloud_values)
    merged.update(
        {
            "BACKEND_IMAGE": args.backend_image,
            "NGINX_IMAGE": args.nginx_image,
            "APP_ALLOWED_ORIGINS": merged.get("APP_ALLOWED_ORIGINS") or detect_host_origins(),
            "APP_PUBLIC_BASE_URL": merged.get("APP_PUBLIC_BASE_URL", ""),
            "APP_ALLOWED_ORIGIN_REGEX": merged.get("APP_ALLOWED_ORIGIN_REGEX", ""),
            "AGENT_SERVICE_ALLOWED_ORIGINS": merged.get("AGENT_SERVICE_ALLOWED_ORIGINS") or merged.get("APP_ALLOWED_ORIGINS") or detect_host_origins(),
            "AGENT_LLM_BASE_URL": merged.get("AGENT_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode"),
            "AGENT_LLM_API_KEY": merged.get("AGENT_LLM_API_KEY", ""),
            "AGENT_LLM_MODEL": merged.get("AGENT_LLM_MODEL", "qwen3.6-flash"),
            "AGENT_LLM_TIMEOUT_SECONDS": merged.get("AGENT_LLM_TIMEOUT_SECONDS", "60"),
            "AGENT_LLM_STRICT_TOOLS": merged.get("AGENT_LLM_STRICT_TOOLS", "true"),
            "AGENT_VISION_LLM_BASE_URL": merged.get("AGENT_VISION_LLM_BASE_URL", ""),
            "AGENT_VISION_LLM_API_KEY": merged.get("AGENT_VISION_LLM_API_KEY", ""),
            "AGENT_VISION_LLM_MODEL": merged.get("AGENT_VISION_LLM_MODEL", ""),
            "AUTH_SECRET_KEY": merged.get("AUTH_SECRET_KEY", ""),
            "AUTH_TOKEN_EXPIRE_HOURS": merged.get("AUTH_TOKEN_EXPIRE_HOURS", "24"),
            "AUTH_COOKIE_NAME": merged.get("AUTH_COOKIE_NAME", "jinma_auth_token"),
            "ROOT_USER_ID": merged.get("ROOT_USER_ID", "00000000-0000-0000-0000-000000000001"),
            "ROOT_USERNAME": merged.get("ROOT_USERNAME", "root"),
            "ROOT_DISPLAY_NAME": merged.get("ROOT_DISPLAY_NAME", "系统管理员"),
            "ROOT_EMAIL": merged.get("ROOT_EMAIL", "root@example.com"),
            "ROOT_DEFAULT_PASSWORD": merged.get("ROOT_DEFAULT_PASSWORD", ""),
            "AI_DEFAULT_PROVIDER": merged.get("AI_DEFAULT_PROVIDER", "apiyi"),
            "AI_UPSTREAM_PLATFORM": merged.get("AI_UPSTREAM_PLATFORM", "apiyi"),
            "APIYI_API_KEY": merged.get("APIYI_API_KEY", ""),
            "APIYI_BASE_URL": merged.get("APIYI_BASE_URL", "https://api.apiyi.com"),
            "APIYI_OPENAI_BASE_URL": merged.get("APIYI_OPENAI_BASE_URL", "https://api.apiyi.com/v1"),
            "APIYI_GEMINI_BASE_URL": merged.get("APIYI_GEMINI_BASE_URL", "https://api.apiyi.com/v1beta"),
            "APIYI_TIMEOUT_SECONDS": merged.get("APIYI_TIMEOUT_SECONDS", "600"),
            "CLOSEAI_API_KEY": merged.get("CLOSEAI_API_KEY", ""),
            "CLOSEAI_BASE_URL": merged.get("CLOSEAI_BASE_URL", "https://api.openai-proxy.org/v1"),
            "CLOSEAI_TIMEOUT_SECONDS": merged.get("CLOSEAI_TIMEOUT_SECONDS", "600"),
            "AI_MAX_FUSION_IMAGES": merged.get("AI_MAX_FUSION_IMAGES", "6"),
            "QUEUE_NAME": merged.get("QUEUE_NAME", "jinma-ai"),
            "QUEUE_JOB_TIMEOUT_SECONDS": merged.get("QUEUE_JOB_TIMEOUT_SECONDS", "900"),
            "QUEUE_RESULT_TTL_SECONDS": merged.get("QUEUE_RESULT_TTL_SECONDS", "3600"),
            "QUEUE_USER_MAX_ACTIVE_JOBS": merged.get("QUEUE_USER_MAX_ACTIVE_JOBS", "1"),
            "QUEUE_ROOT_MAX_ACTIVE_JOBS": merged.get("QUEUE_ROOT_MAX_ACTIVE_JOBS", "3"),
            "CACHE_JOB_STATUS_TTL_SECONDS": merged.get("CACHE_JOB_STATUS_TTL_SECONDS", "21600"),
            "CACHE_JOB_DEDUPE_TTL_SECONDS": merged.get("CACHE_JOB_DEDUPE_TTL_SECONDS", "120"),
            "CACHE_MODEL_CATALOG_TTL_SECONDS": merged.get("CACHE_MODEL_CATALOG_TTL_SECONDS", "300"),
            "CACHE_AUTH_ME_TTL_SECONDS": merged.get("CACHE_AUTH_ME_TTL_SECONDS", "60"),
            "DB_POOL_SIZE": merged.get("DB_POOL_SIZE", "10"),
            "DB_MAX_OVERFLOW": merged.get("DB_MAX_OVERFLOW", "20"),
            "DB_POOL_RECYCLE_SECONDS": merged.get("DB_POOL_RECYCLE_SECONDS", "3600"),
        }
    )

    database_url = backend_values.get("DATABASE_URL", "")
    if "/127.0.0.1:" in database_url or "@127.0.0.1:" in database_url or "@localhost:" in database_url:
        merged["MYSQL_DATABASE"] = database_url.rsplit("/", 1)[-1] if "/" in database_url else merged.get("MYSQL_DATABASE", "jinma")

    lines = [
        "# Docker Compose 运行环境变量。",
        "# 这份文件会被离线包启动脚本直接使用。",
        "",
        "# 时区，影响容器内日志和时间显示。",
        f"TZ={merged['TZ']}",
        "",
        "# nginx 对外暴露端口。当前建议单机局域网直接使用 80。",
        f"NGINX_PORT={merged['NGINX_PORT']}",
        "",
        "# 离线包镜像名。start_offline_stack.sh 会先 docker load，再按这两个名字启动。",
        f"BACKEND_IMAGE={merged['BACKEND_IMAGE']}",
        f"NGINX_IMAGE={merged['NGINX_IMAGE']}",
        "",
        "# MySQL 初始化数据库名与 root 密码。",
        f"MYSQL_DATABASE={merged['MYSQL_DATABASE']}",
        f"MYSQL_ROOT_PASSWORD={merged['MYSQL_ROOT_PASSWORD']}",
        "",
        "# Web API 使用的 gunicorn worker 数量。",
        f"WEB_CONCURRENCY={merged['WEB_CONCURRENCY']}",
        "",
        "# 构建后端镜像时使用的 pip 源。重新打离线包时会用到。",
        f"PIP_INDEX_URL={merged['PIP_INDEX_URL']}",
        "",
        "# 允许访问本服务的前端来源。当前已自动包含 localhost、127.0.0.1 和本机局域网 IP。",
        f"APP_ALLOWED_ORIGINS={merged['APP_ALLOWED_ORIGINS']}",
        f"APP_PUBLIC_BASE_URL={merged['APP_PUBLIC_BASE_URL']}",
        f"APP_ALLOWED_ORIGIN_REGEX={merged['APP_ALLOWED_ORIGIN_REGEX']}",
        "",
        "# Agent 独立服务与 Qwen DashScope OpenAI-compatible 配置。AGENT_LLM_API_KEY 留空时使用规则兜底回复。",
        f"AGENT_SERVICE_ALLOWED_ORIGINS={merged['AGENT_SERVICE_ALLOWED_ORIGINS']}",
        f"AGENT_LLM_BASE_URL={merged['AGENT_LLM_BASE_URL']}",
        f"AGENT_LLM_API_KEY={merged['AGENT_LLM_API_KEY']}",
        f"AGENT_LLM_MODEL={merged['AGENT_LLM_MODEL']}",
        f"AGENT_LLM_TIMEOUT_SECONDS={merged['AGENT_LLM_TIMEOUT_SECONDS']}",
        f"AGENT_LLM_STRICT_TOOLS={merged['AGENT_LLM_STRICT_TOOLS']}",
        "# Agent 视觉模型配置。留空时，qwen3.6 / qwen-vl / qwen-omni / gpt-4o / gemini 会复用 AGENT_LLM_*。",
        f"AGENT_VISION_LLM_BASE_URL={merged['AGENT_VISION_LLM_BASE_URL']}",
        f"AGENT_VISION_LLM_API_KEY={merged['AGENT_VISION_LLM_API_KEY']}",
        f"AGENT_VISION_LLM_MODEL={merged['AGENT_VISION_LLM_MODEL']}",
        "",
        "# 应用鉴权密钥。生产环境必须修改。",
        f"AUTH_SECRET_KEY={merged['AUTH_SECRET_KEY']}",
        f"AUTH_TOKEN_EXPIRE_HOURS={merged['AUTH_TOKEN_EXPIRE_HOURS']}",
        f"AUTH_COOKIE_NAME={merged['AUTH_COOKIE_NAME']}",
        "",
        "# root 默认账号信息。首次启动时会按这组配置初始化。",
        f"ROOT_USER_ID={merged['ROOT_USER_ID']}",
        f"ROOT_USERNAME={merged['ROOT_USERNAME']}",
        f"ROOT_DISPLAY_NAME={merged['ROOT_DISPLAY_NAME']}",
        f"ROOT_EMAIL={merged['ROOT_EMAIL']}",
        f"ROOT_DEFAULT_PASSWORD={merged['ROOT_DEFAULT_PASSWORD']}",
        "",
        "# AI 平台配置。当前默认模型 gpt-image-2-all-apiyi 走 APIYI；Nano Banana 2 也走 APIYI。",
        f"AI_DEFAULT_PROVIDER={merged['AI_DEFAULT_PROVIDER']}",
        f"AI_UPSTREAM_PLATFORM={merged['AI_UPSTREAM_PLATFORM']}",
        f"APIYI_API_KEY={merged['APIYI_API_KEY']}",
        f"APIYI_BASE_URL={merged['APIYI_BASE_URL']}",
        f"APIYI_OPENAI_BASE_URL={merged['APIYI_OPENAI_BASE_URL']}",
        f"APIYI_GEMINI_BASE_URL={merged['APIYI_GEMINI_BASE_URL']}",
        f"APIYI_TIMEOUT_SECONDS={merged['APIYI_TIMEOUT_SECONDS']}",
        f"CLOSEAI_API_KEY={merged['CLOSEAI_API_KEY']}",
        f"CLOSEAI_BASE_URL={merged['CLOSEAI_BASE_URL']}",
        f"CLOSEAI_TIMEOUT_SECONDS={merged['CLOSEAI_TIMEOUT_SECONDS']}",
        "",
        "# 上游调用与队列参数。",
        f"AI_MAX_FUSION_IMAGES={merged['AI_MAX_FUSION_IMAGES']}",
        f"QUEUE_NAME={merged['QUEUE_NAME']}",
        f"QUEUE_JOB_TIMEOUT_SECONDS={merged['QUEUE_JOB_TIMEOUT_SECONDS']}",
        f"QUEUE_RESULT_TTL_SECONDS={merged['QUEUE_RESULT_TTL_SECONDS']}",
        f"QUEUE_USER_MAX_ACTIVE_JOBS={merged['QUEUE_USER_MAX_ACTIVE_JOBS']}",
        f"QUEUE_ROOT_MAX_ACTIVE_JOBS={merged['QUEUE_ROOT_MAX_ACTIVE_JOBS']}",
        "",
        "# Redis 轻量缓存参数。",
        f"CACHE_JOB_STATUS_TTL_SECONDS={merged['CACHE_JOB_STATUS_TTL_SECONDS']}",
        f"CACHE_JOB_DEDUPE_TTL_SECONDS={merged['CACHE_JOB_DEDUPE_TTL_SECONDS']}",
        f"CACHE_MODEL_CATALOG_TTL_SECONDS={merged['CACHE_MODEL_CATALOG_TTL_SECONDS']}",
        f"CACHE_AUTH_ME_TTL_SECONDS={merged['CACHE_AUTH_ME_TTL_SECONDS']}",
        "",
        "# 数据库连接池参数。",
        f"DB_POOL_SIZE={merged['DB_POOL_SIZE']}",
        f"DB_MAX_OVERFLOW={merged['DB_MAX_OVERFLOW']}",
        f"DB_POOL_RECYCLE_SECONDS={merged['DB_POOL_RECYCLE_SECONDS']}",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
