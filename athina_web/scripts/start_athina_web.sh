#!/usr/bin/env bash
# Helper script to build and bring up the athina-web docker service
# Usage: start_athina_web.sh [--detach|-d] [--rebuild|-b] [--sqlite]
#   --detach / -d    Run `docker-compose up` in detached mode
#   --rebuild / -b   Rebuild the image before bringing containers up
#   --sqlite         Export ATHINA_USE_SQLITE_FOR_TESTS=1 for the container environment

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

DETACH=0
REBUILD=0
USE_SQLITE=0

print_usage() {
  cat <<EOF
Usage: $(basename "$0") [--detach|-d] [--rebuild|-b] [--sqlite] [--help]

Options:
  --detach, -d    Start in detached mode (background)
  --rebuild, -b   Rebuild the Docker image before starting
  --sqlite        Set ATHINA_USE_SQLITE_FOR_TESTS=1 for the container
  --help          Show this help

Examples:
  $(basename "$0") --rebuild --detach         # rebuild image and start in background
  $(basename "$0") --sqlite                    # start with SQLite test DB environment
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--detach) DETACH=1; shift ;;
    -b|--rebuild) REBUILD=1; shift ;;
    --sqlite) USE_SQLITE=1; shift ;;
    -h|--help) print_usage; exit 0 ;;
    *) echo "Unknown option: $1"; print_usage; exit 2 ;;
  esac
done

# Detect docker-compose command (support both `docker compose` and `docker-compose`)
if command -v docker-compose >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker-compose"
elif docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker compose"
else
  echo "ERROR: docker-compose or 'docker compose' not found in PATH." >&2
  exit 2
fi

SERVICE_NAME="athina-web"

if [[ $REBUILD -eq 1 ]]; then
  echo "Rebuilding Docker image for service: $SERVICE_NAME"
  # Use compose build to respect docker-compose configuration
  $DOCKER_COMPOSE build --no-cache "$SERVICE_NAME"
fi

COMPOSE_ENV=( )
if [[ $USE_SQLITE -eq 1 ]]; then
  echo "Enabling SQLite test DB inside container (ATHINA_USE_SQLITE_FOR_TESTS=1)"
  COMPOSE_ENV+=("ATHINA_USE_SQLITE_FOR_TESTS=1")
fi

UP_CMD=( $DOCKER_COMPOSE up )
if [[ $DETACH -eq 1 ]]; then
  UP_CMD+=( -d )
fi
UP_CMD+=( "$SERVICE_NAME" )

echo "Starting service: $SERVICE_NAME"
if [[ ${#COMPOSE_ENV[@]} -gt 0 ]]; then
  # Export vars for this invocation
  env "${COMPOSE_ENV[@]}" ${UP_CMD[@]}
else
  ${UP_CMD[@]}
fi

echo "Done. Use '$DOCKER_COMPOSE ps' to inspect running containers." 
