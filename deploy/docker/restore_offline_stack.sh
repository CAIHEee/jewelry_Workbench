#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env.docker" && -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
  OFFLINE_DIR="$SCRIPT_DIR"
else
  OFFLINE_DIR="$(cd "$SCRIPT_DIR/../../dist/offline_bundle" && pwd)"
fi
if [[ $# -ge 2 ]]; then
  OFFLINE_DIR="$2"
fi
BACKUP_DIR="${1:-$(cd "$OFFLINE_DIR/.." && pwd)/offline_backup}"
PROJECT_NAME="jinma_jewelry_system"

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

if [[ ! -f "$BACKUP_DIR/jinma.sql" ]]; then
  echo "Missing $BACKUP_DIR/jinma.sql" >&2
  exit 1
fi

if [[ ! -f "$BACKUP_DIR/backend_data.tar.gz" ]]; then
  echo "Missing $BACKUP_DIR/backend_data.tar.gz" >&2
  exit 1
fi

docker compose --env-file "$OFFLINE_DIR/.env.docker" -f "$OFFLINE_DIR/docker-compose.yml" up -d

docker compose \
  --env-file "$OFFLINE_DIR/.env.docker" \
  -f "$OFFLINE_DIR/docker-compose.yml" \
  exec -T mysql \
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" < "$BACKUP_DIR/jinma.sql"

docker run --rm \
  -v "${PROJECT_NAME}_backend_data:/data" \
  -v "$BACKUP_DIR:/backup" \
  alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/backend_data.tar.gz -C /data"

docker compose --env-file "$OFFLINE_DIR/.env.docker" -f "$OFFLINE_DIR/docker-compose.yml" restart

echo "Restore completed."
