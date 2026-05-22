#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env.docker" && -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
  OFFLINE_DIR="$SCRIPT_DIR"
else
  OFFLINE_DIR="$(cd "$SCRIPT_DIR/../../dist/offline_bundle" && pwd)"
fi
OUTPUT_DIR="${1:-$(cd "$OFFLINE_DIR/.." && pwd)/offline_backup}"
PROJECT_NAME="jinma_jewelry_system"

mkdir -p "$OUTPUT_DIR"

if [[ ! -f "$OFFLINE_DIR/.env.docker" ]]; then
  echo "Missing $OFFLINE_DIR/.env.docker" >&2
  exit 1
fi

if [[ ! -f "$OFFLINE_DIR/docker-compose.yml" ]]; then
  echo "Missing $OFFLINE_DIR/docker-compose.yml" >&2
  exit 1
fi

MYSQL_ROOT_PASSWORD="$(grep '^MYSQL_ROOT_PASSWORD=' "$OFFLINE_DIR/.env.docker" | cut -d= -f2-)"
MYSQL_DATABASE="$(grep '^MYSQL_DATABASE=' "$OFFLINE_DIR/.env.docker" | cut -d= -f2-)"

if [[ -z "${MYSQL_ROOT_PASSWORD:-}" ]]; then
  echo "MYSQL_ROOT_PASSWORD is empty in $OFFLINE_DIR/.env.docker" >&2
  exit 1
fi

if [[ -z "${MYSQL_DATABASE:-}" ]]; then
  MYSQL_DATABASE="jinma"
fi

docker compose \
  --env-file "$OFFLINE_DIR/.env.docker" \
  -f "$OFFLINE_DIR/docker-compose.yml" \
  exec -T mysql \
  mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" > "$OUTPUT_DIR/jinma.sql"

docker run --rm \
  -v "${PROJECT_NAME}_backend_data:/data" \
  -v "$OUTPUT_DIR:/backup" \
  alpine \
  tar czf /backup/backend_data.tar.gz -C /data .

echo "Backup completed:"
echo "  - $OUTPUT_DIR/jinma.sql"
echo "  - $OUTPUT_DIR/backend_data.tar.gz"
