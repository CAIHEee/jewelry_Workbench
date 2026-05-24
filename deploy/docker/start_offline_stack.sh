#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$SCRIPT_DIR/runtime"
RUNTIME_CONFIG_FILE="$RUNTIME_DIR/secrets.env"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR"
touch "$RUNTIME_CONFIG_FILE"
chmod 600 "$RUNTIME_CONFIG_FILE" 2>/dev/null || true

docker load -i "$SCRIPT_DIR/jinma-images.tar"
docker compose --env-file "$SCRIPT_DIR/.env.docker" -f "$SCRIPT_DIR/docker-compose.yml" up -d

echo "Stack started."
echo "Check status with:"
echo "  docker compose --env-file $SCRIPT_DIR/.env.docker -f $SCRIPT_DIR/docker-compose.yml ps"
