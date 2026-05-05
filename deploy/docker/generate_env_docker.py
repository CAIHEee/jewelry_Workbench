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
            "AGENT_LLM_BASE_URL": merged.get("AGENT_LLM_BASE_URL", "https://api.deepseek.com"),
            "AGENT_LLM_API_KEY": merged.get("AGENT_LLM_API_KEY", ""),
            "AGENT_LLM_MODEL": merged.get("AGENT_LLM_MODEL", "deepseek-v4-flash"),
            "AGENT_LLM_TIMEOUT_SECONDS": merged.get("AGENT_LLM_TIMEOUT_SECONDS", "60"),
            "AGENT_LLM_STRICT_TOOLS": merged.get("AGENT_LLM_STRICT_TOOLS", "true"),
            "AUTH_SECRET_KEY": merged.get("AUTH_SECRET_KEY", ""),
            "ROOT_USERNAME": merged.get("ROOT_USERNAME", "root"),
            "ROOT_DISPLAY_NAME": merged.get("ROOT_DISPLAY_NAME", "系统管理员"),
            "ROOT_EMAIL": merged.get("ROOT_EMAIL", "root@example.com"),
            "ROOT_DEFAULT_PASSWORD": merged.get("ROOT_DEFAULT_PASSWORD", ""),
            "AI_DEFAULT_PROVIDER": merged.get("AI_DEFAULT_PROVIDER", "flux"),
            "AI_UPSTREAM_PLATFORM": merged.get("AI_UPSTREAM_PLATFORM", "apiyi"),
            "AIAPIS_API_KEY": merged.get("AIAPIS_API_KEY", ""),
            "AIAPIS_BASE_URL": merged.get("AIAPIS_BASE_URL", "https://img.aiapis.help/v1"),
            "AIAPIS_TIMEOUT_SECONDS": merged.get("AIAPIS_TIMEOUT_SECONDS", "300"),
            "APIYI_API_KEY": merged.get("APIYI_API_KEY", ""),
            "APIYI_BASE_URL": merged.get("APIYI_BASE_URL", ""),
            "APIYI_OPENAI_BASE_URL": merged.get("APIYI_OPENAI_BASE_URL", ""),
            "APIYI_GEMINI_BASE_URL": merged.get("APIYI_GEMINI_BASE_URL", ""),
            "APIYI_TIMEOUT_SECONDS": merged.get("APIYI_TIMEOUT_SECONDS", "600"),
            "DMXAPI_API_KEY": merged.get("DMXAPI_API_KEY", ""),
            "DMXAPI_BASE_URL": merged.get("DMXAPI_BASE_URL", "https://www.dmxapi.cn/v1"),
            "DMXAPI_TIMEOUT_SECONDS": merged.get("DMXAPI_TIMEOUT_SECONDS", "600"),
            "TTAPI_API_KEY": merged.get("TTAPI_API_KEY", ""),
            "TTAPI_FLUX_BASE_URL": merged.get("TTAPI_FLUX_BASE_URL", ""),
            "TTAPI_OPENAI_BASE_URL": merged.get("TTAPI_OPENAI_BASE_URL", ""),
            "TTAPI_TIMEOUT_SECONDS": merged.get("TTAPI_TIMEOUT_SECONDS", "120"),
            "TTAPI_POLL_INTERVAL_SECONDS": merged.get("TTAPI_POLL_INTERVAL_SECONDS", "2.5"),
            "TTAPI_POLL_ATTEMPTS": merged.get("TTAPI_POLL_ATTEMPTS", "24"),
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
        "# Agent 独立服务与对话模型配置。AGENT_LLM_API_KEY 留空时使用规则兜底回复。",
        f"AGENT_SERVICE_ALLOWED_ORIGINS={merged['AGENT_SERVICE_ALLOWED_ORIGINS']}",
        f"AGENT_LLM_BASE_URL={merged['AGENT_LLM_BASE_URL']}",
        f"AGENT_LLM_API_KEY={merged['AGENT_LLM_API_KEY']}",
        f"AGENT_LLM_MODEL={merged['AGENT_LLM_MODEL']}",
        f"AGENT_LLM_TIMEOUT_SECONDS={merged['AGENT_LLM_TIMEOUT_SECONDS']}",
        f"AGENT_LLM_STRICT_TOOLS={merged['AGENT_LLM_STRICT_TOOLS']}",
        "",
        "# 应用鉴权密钥。生产环境必须修改。",
        f"AUTH_SECRET_KEY={merged['AUTH_SECRET_KEY']}",
        "",
        "# root 默认账号信息。首次启动时会按这组配置初始化。",
        f"ROOT_USERNAME={merged['ROOT_USERNAME']}",
        f"ROOT_DISPLAY_NAME={merged['ROOT_DISPLAY_NAME']}",
        f"ROOT_EMAIL={merged['ROOT_EMAIL']}",
        f"ROOT_DEFAULT_PASSWORD={merged['ROOT_DEFAULT_PASSWORD']}",
        "",
        "# AI 平台配置。GPT Image 2 默认走 AIAPIS；Nano Banana 2 仍走 APIYI/TTAPI。",
        f"AI_DEFAULT_PROVIDER={merged['AI_DEFAULT_PROVIDER']}",
        f"AI_UPSTREAM_PLATFORM={merged['AI_UPSTREAM_PLATFORM']}",
        f"AIAPIS_API_KEY={merged['AIAPIS_API_KEY']}",
        f"AIAPIS_BASE_URL={merged['AIAPIS_BASE_URL']}",
        f"AIAPIS_TIMEOUT_SECONDS={merged['AIAPIS_TIMEOUT_SECONDS']}",
        f"APIYI_API_KEY={merged['APIYI_API_KEY']}",
        f"APIYI_BASE_URL={merged['APIYI_BASE_URL']}",
        f"APIYI_OPENAI_BASE_URL={merged['APIYI_OPENAI_BASE_URL']}",
        f"APIYI_GEMINI_BASE_URL={merged['APIYI_GEMINI_BASE_URL']}",
        f"APIYI_TIMEOUT_SECONDS={merged['APIYI_TIMEOUT_SECONDS']}",
        f"DMXAPI_API_KEY={merged['DMXAPI_API_KEY']}",
        f"DMXAPI_BASE_URL={merged['DMXAPI_BASE_URL']}",
        f"DMXAPI_TIMEOUT_SECONDS={merged['DMXAPI_TIMEOUT_SECONDS']}",
        f"TTAPI_API_KEY={merged['TTAPI_API_KEY']}",
        f"TTAPI_FLUX_BASE_URL={merged['TTAPI_FLUX_BASE_URL']}",
        f"TTAPI_OPENAI_BASE_URL={merged['TTAPI_OPENAI_BASE_URL']}",
        "",
        "# 上游调用与队列参数。",
        f"TTAPI_TIMEOUT_SECONDS={merged['TTAPI_TIMEOUT_SECONDS']}",
        f"TTAPI_POLL_INTERVAL_SECONDS={merged['TTAPI_POLL_INTERVAL_SECONDS']}",
        f"TTAPI_POLL_ATTEMPTS={merged['TTAPI_POLL_ATTEMPTS']}",
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
