#!/usr/bin/env bash
set -euo pipefail

# dev-run.sh - bring up a development instance of athina + athina-web
# This script uses the provided one-click docker-compose file but generates
# a small override compose file to remap host ports and network so it can
# run alongside a production instance on the same host.

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ONE_CLICK_COMPOSE="$ROOT_DIR/athina-one-click-run/docker-compose.yml"
# Also support the one-click folder being a sibling of this repo root
ONE_CLICK_COMPOSE_ALT="$ROOT_DIR/../athina-one-click-run/docker-compose.yml"
ONE_CLICK_COMPOSE_OVERRIDE="$ROOT_DIR/athina-one-click-run/docker-compose.override.yml"
DEV_COMPOSE="$ROOT_DIR/docker-compose.dev.yml"

# Defaults (can be overridden via environment)
HOST_HTTP=${HOST_HTTP:-8080}
HOST_HTTPS=${HOST_HTTPS:-8443}
HOST_WEB=${HOST_WEB:-8002}
NETWORK_SUBNET=${NETWORK_SUBNET:-172.29.20.0/24}
PROJECT_NAME=${PROJECT_NAME:-athina_dev}

function usage() {
  cat <<EOF
Usage: $0 {start|stop|status|logs}

Environment variables:
  HOST_HTTP      host port to map to nginx:80 (default: ${HOST_HTTP})
  HOST_HTTPS     host port to map to nginx:443 (default: ${HOST_HTTPS})
  HOST_WEB       host port to map to athina-web (default: ${HOST_WEB})
  NETWORK_SUBNET docker network subnet for the dev network (default: ${NETWORK_SUBNET})
  PROJECT_NAME   docker-compose project name (default: ${PROJECT_NAME})

Examples:
  HOST_HTTP=8081 HOST_WEB=8003 $0 start
  $0 stop
EOF
}

if [ "$#" -lt 1 ]; then
  usage
  exit 1
fi

COMMAND=$1

function check_docker_compose() {
  # Prefer the legacy `docker-compose` binary, otherwise try the v2 `docker compose` CLI
  if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
    return 0
  fi

  if command -v docker >/dev/null 2>&1; then
    # Test if `docker compose` is available
    if docker compose version >/dev/null 2>&1; then
      COMPOSE_CMD="docker compose"
      return 0
    fi
  fi

  echo "docker-compose (or 'docker compose') is required but not found in PATH" >&2
  exit 2
}

function port_in_use() {
  local port=$1
  if ss -ltn | awk '{print $4}' | grep -E ":${port}$" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

function generate_dev_compose() {
  # Generate a small dev compose that remaps host ports and mounts local dev settings/db
  cat > "$DEV_COMPOSE" <<YAML
version: '3'
services:
  nginx:
    ports:
      - "${HOST_HTTP}:80"
      - "${HOST_HTTPS}:443"
    networks:
      - athina_dev_net

  athina-web:
    ports:
      - "${HOST_WEB}:8001"
    volumes:
      # Ensure dev settings and DB are mounted into the container (absolute paths)
      - "$ROOT_DIR/athina-web/athinaweb/settings_secret_local.py:/code/athinaweb/settings_secret.py:ro"
      - "$ROOT_DIR/athina-web/db.sqlite3:/code/db.sqlite3"
    networks:
      - athina_dev_net

  athina:
    networks:
      - athina_dev_net

  db:
    networks:
      - athina_dev_net

networks:
  athina_dev_net:
    driver: bridge
YAML
}

check_docker_compose

case "$COMMAND" in
  start)
    echo "Starting dev stack (project=${PROJECT_NAME})"
    # Simple port checks to avoid clobbering existing services
    for p in "${HOST_HTTP}" "${HOST_HTTPS}" "${HOST_WEB}"; do
      if port_in_use "$p"; then
        echo "ERROR: port $p is already in use on the host. Pick different HOST_* values or stop the service using the port." >&2
        exit 3
      fi
    done

    if [ ! -f "$ONE_CLICK_COMPOSE" ]; then
      if [ -f "$ONE_CLICK_COMPOSE_ALT" ]; then
        ONE_CLICK_COMPOSE="$ONE_CLICK_COMPOSE_ALT"
        echo "Using one-click compose from $ONE_CLICK_COMPOSE"
      else
        echo "Cannot find one-click docker-compose at $ONE_CLICK_COMPOSE or $ONE_CLICK_COMPOSE_ALT" >&2
        exit 4
      fi
    fi

    generate_dev_compose

    # If the one-click compose declares a fixed subnet that overlaps existing networks,
    # try to substitute a free subnet in a temporary copy so we can run side-by-side with prod.
    TEMP_ONE_CLICK_COMPOSE="${ONE_CLICK_COMPOSE}.tmp"
    cp "$ONE_CLICK_COMPOSE" "$TEMP_ONE_CLICK_COMPOSE"
    if grep -q "subnet:" "$ONE_CLICK_COMPOSE" 2>/dev/null; then
      echo "One-click compose declares a subnet; attempting to pick a non-overlapping subnet..."
      # Gather existing subnets
      existing_subnets="$(docker network ls -q 2>/dev/null | xargs -r docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | tr ' ' '\n' | sed '/^$/d' || true)"
      found=""
      for i in $(seq 16 254); do
        candidate="172.${i}.0.0/16"
        if ! echo "$existing_subnets" | grep -Fq "$candidate"; then
          found="$candidate"
          break
        fi
      done
      if [ -n "$found" ]; then
        echo "Using subnet $found for dev stack (temporary override)."
        # Use Python (PyYAML) to safely replace the subnet and strip static addresses/ports
        python3 - <<PY > "$TEMP_ONE_CLICK_COMPOSE"
import sys, yaml
f = open('$ONE_CLICK_COMPOSE')
data = yaml.safe_load(f)
f.close()
# Replace subnet for any network ipam config
for net_name, net_conf in (data.get('networks') or {}).items():
    ipam = net_conf.get('ipam') if isinstance(net_conf, dict) else None
    if ipam and isinstance(ipam, dict):
        cfg = ipam.get('config')
        if isinstance(cfg, list) and cfg:
            cfg[0]['subnet'] = '$found'
            ipam['config'] = cfg
            net_conf['ipam'] = ipam
            data['networks'][net_name] = net_conf
# Remove any hard-coded ipv4_address from services
for svc_name, svc in (data.get('services') or {}).items():
    nets = svc.get('networks')
    if isinstance(nets, dict):
        for k,v in list(nets.items()):
            if isinstance(v, dict) and 'ipv4_address' in v:
                v.pop('ipv4_address', None)
                nets[k] = v
    # Remove ports mapping from nginx service to avoid binding host ports
    if svc_name == 'nginx' and 'ports' in svc:
        svc.pop('ports', None)
    data['services'][svc_name] = svc
yaml.safe_dump(data, sys.stdout, default_flow_style=False)
PY
      else
        echo "Could not automatically find a free 172.*.0.0/16 subnet; proceeding with original compose (may fail)."
        cp "$ONE_CLICK_COMPOSE" "$TEMP_ONE_CLICK_COMPOSE"
      fi
    fi

    echo "Bringing up containers..."
    # Build compose -f arguments. If the one-click dir provides a docker-compose.override.yml
    # (we add this file for dev mounts like settings_secret_local.py), include it so mounts
    # are applied to the temporary compose used for the dev run.
    COMPOSE_FILES=( -f "$TEMP_ONE_CLICK_COMPOSE" )
    if [ -f "$ONE_CLICK_COMPOSE_OVERRIDE" ]; then
      echo "Including one-click override $ONE_CLICK_COMPOSE_OVERRIDE"
      COMPOSE_FILES+=( -f "$ONE_CLICK_COMPOSE_OVERRIDE" )
    fi
    COMPOSE_FILES+=( -f "$DEV_COMPOSE" )

    $COMPOSE_CMD -p "$PROJECT_NAME" "${COMPOSE_FILES[@]}" up -d
    echo "Dev stack started. nginx should be available at http://localhost:${HOST_HTTP} (or https on ${HOST_HTTPS})."
    ;;
  stop)
    echo "Stopping dev stack (project=${PROJECT_NAME})"
    # Determine which compose file to use
    if [ ! -f "$ONE_CLICK_COMPOSE" ] && [ -f "$ONE_CLICK_COMPOSE_ALT" ]; then
      ONE_CLICK_COMPOSE="$ONE_CLICK_COMPOSE_ALT"
    fi
    if [ -f "$DEV_COMPOSE" ]; then
  $COMPOSE_CMD -p "$PROJECT_NAME" -f "$ONE_CLICK_COMPOSE" -f "$DEV_COMPOSE" down
      rm -f "$DEV_COMPOSE"
    else
  $COMPOSE_CMD -p "$PROJECT_NAME" -f "$ONE_CLICK_COMPOSE" down || true
    fi
    ;;
  status)
    # Determine which compose file to use
    if [ ! -f "$ONE_CLICK_COMPOSE" ] && [ -f "$ONE_CLICK_COMPOSE_ALT" ]; then
      ONE_CLICK_COMPOSE="$ONE_CLICK_COMPOSE_ALT"
    fi
  $COMPOSE_CMD -p "$PROJECT_NAME" -f "$ONE_CLICK_COMPOSE" -f "$DEV_COMPOSE" ps
    ;;
  logs)
    # Determine which compose file to use
    if [ ! -f "$ONE_CLICK_COMPOSE" ] && [ -f "$ONE_CLICK_COMPOSE_ALT" ]; then
      ONE_CLICK_COMPOSE="$ONE_CLICK_COMPOSE_ALT"
    fi
  $COMPOSE_CMD -p "$PROJECT_NAME" -f "$ONE_CLICK_COMPOSE" -f "$DEV_COMPOSE" logs -f
    ;;
  *)
    usage
    exit 1
    ;;
esac

exit 0
