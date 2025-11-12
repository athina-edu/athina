# Athina Development Environment - Complete Guide

## Status: WORKING (as of November 10, 2025)

This document consolidates all dev environment setup, usage, troubleshooting, and testing information for the Athina stack.

---

## Quick Start

```bash
cd /mnt/local_data/athina-dev/athina
./dev-run.sh start
# Wait ~10 seconds, then open your browser:
# http://localhost:8080/accounts/login/
# Username: admin
# Password: adminpass
./dev-run.sh stop  # to stop the dev stack
```

---

## What You Get
- **Web UI** at http://localhost:8080 (default, customizable)
- **SQLite database** at `athina-web/db.sqlite3` (no MySQL needed)
- **Dev settings** with DEBUG=True at `athina-web/athinaweb/settings_secret_local.py`
- **Isolated from production** — different project name, subnet, and ports

---

## Files Involved
- `dev-run.sh` — orchestrator script (generates compose files and starts/stops the stack)
- `docker-compose.dev.yml` — auto-generated; remaps host ports and mounts dev files
- `athina-one-click-run/docker-compose.yml.tmp` — auto-generated; temporary copy with subnet adjustments
- `athina-web/athinaweb/settings_secret_local.py` — dev Django settings (SQLite, DEBUG=True)
- `athina-web/db.sqlite3` — dev SQLite database file

---

## Environment Variables (customize ports/project name)
- `HOST_HTTP` — host port mapped to nginx:80 (default: 8080)
- `HOST_HTTPS` — host port mapped to nginx:443 (default: 8443)
- `HOST_WEB` — host port mapped to athina-web gunicorn:8001 (default: 8002)
- `PROJECT_NAME` — docker-compose project name (default: athina_dev)
- `NETWORK_SUBNET` — docker network subnet (default: auto-picks a non-overlapping 172.*/16)

---

## How the Script Works
1. Locates one-click compose file
2. Picks a non-overlapping subnet if needed
3. Prepares a temporary compose file (subnet/port adjustments)
4. Generates `docker-compose.dev.yml` (host ports, mounts dev settings/db)
5. Starts services with compose

---

## First-Time Setup
1. Start the dev stack:
   ```bash
   ./dev-run.sh start
   ```
2. Wait a few seconds, then test the site:
   ```bash
   curl -I http://localhost:8080/
   # Expect: HTTP/1.1 200 OK
   ```
3. View the login page:
   ```bash
   curl http://localhost:8080/accounts/login/ | head -20
   ```
4. Run migrations and create a superuser:
   ```bash
   docker exec -u root athina_dev-athina-web-1 python manage.py migrate --noinput
   docker exec -u root athina_dev-athina-web-1 python manage.py shell <<'EOF'
   from django.contrib.auth import get_user_model
   User = get_user_model()
   if not User.objects.filter(username='admin').exists():
       User.objects.create_superuser('admin', 'admin@example.com', 'adminpass')
       print("Created superuser: admin / adminpass")
   else:
       print("Superuser already exists")
   EOF
   ```
5. Log in at http://localhost:8080/accounts/login/ (admin/adminpass)

---

## Common Commands
```bash
# Check running containers and ports
docker ps --format "{{.Names}}\t{{.Status}}\t{{.Ports}}" | grep athina
# View logs
./dev-run.sh logs
# Test HTTP response
curl -I http://localhost:8080/
# Check if login works
docker exec -u root athina_dev-athina-web-1 python manage.py shell -c \
  "from django.contrib.auth import authenticate; \
   u=authenticate(username='admin',password='adminpass'); \
   print('OK' if u else 'FAIL')"
# Create another dev superuser (if needed)
docker exec -u root athina_dev-athina-web-1 python manage.py shell <<'PYEOF'
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='yourname').exists():
    User.objects.create_superuser('yourname', 'yourname@example.com', 'password')
    print(f"Created superuser: yourname")
PYEOF
```

---

## Customizing Ports & Multiple Dev Stacks
- Use `HOST_HTTP`, `HOST_HTTPS`, `HOST_WEB`, and `PROJECT_NAME` to run multiple dev stacks or avoid port conflicts.
- Example:
  ```bash
  HOST_HTTP=18080 HOST_WEB=18002 PROJECT_NAME=athina_dev_alt ./dev-run.sh start
  # Now access http://localhost:18080/
  ```

---

## Troubleshooting & Common Issues
- **Connection refused**: Check containers are running and ports are mapped.
- **nginx: host not found in upstream 'athina-web'**: athina-web failed to start or is not in the same network. Check logs and mounts.
- **ModuleNotFoundError: No module named 'athinaweb.settings_secret'**: dev settings file not mounted. Ensure `settings_secret_local.py` exists and is mounted.
- **unable to open database file**: SQLite file missing or permissions issue. Ensure `db.sqlite3` exists and is mounted.
- **Port already in use**: Use custom ports or stop the conflicting service.
- **Resetting the dev DB**:
  ```bash
  rm athina-web/db.sqlite3
  touch athina-web/db.sqlite3
  ./dev-run.sh stop
  ./dev-run.sh start
  docker exec -u root athina_dev-athina-web-1 python manage.py migrate --noinput
  ```

---

## Testing & Verification

### What was tested
- Dev stack startup, port availability, service networking, Django startup, DB initialization, settings, superuser creation, authentication

### Test results
- All containers running (`athina_dev-nginx-1`, `athina_dev-athina-web-1`, `athina_dev-db-1`, `athina_dev-athina-1`)
- HTTP 200 from nginx
- Homepage and login page load
- Migrations run without error
- Superuser login works
- Gunicorn running

### How to verify manually
1. Start the dev stack: `./dev-run.sh start`
2. Check containers: `docker ps --format "{{.Names}}\t{{.Status}}\t{{.Ports}}" | grep athina_dev`
3. Test HTTP: `curl -I http://localhost:8080/`
4. Check logs: `./dev-run.sh logs`
5. Log in at http://localhost:8080/accounts/login/ (admin/adminpass)
6. Access admin at http://localhost:8080/admin/

### Key design decisions
- Automatic subnet selection, no modifications to original one-click compose, mounts via generated compose, project name isolation, SQLite for dev, idempotent migrations/superuser creation

### Production vs. Dev differences
| Aspect | Production | Dev |
|--------|------------|-----|
| Compose file | Original `athina-one-click-run/docker-compose.yml` | Temporary copy + generated `docker-compose.dev.yml` |
| Database | MySQL (persistent) | SQLite file-backed |
| Settings | `athinaweb/settings_secret.py` (must be created by user) | `athinaweb/settings_secret_local.py` (checked into dev repo) |
| DEBUG | False | True |
| Port HTTP | 80 | 8080 (customizable) |
| Port HTTPS | 443 | 8443 (customizable) |
| Port Django | N/A (internal only) | 8002 (customizable) |
| Network | Fixed subnet (e.g., 172.29.0.0/16) | Auto-picked non-overlapping subnet |
| Project name | `athina-one-click-run` | `athina_dev` (customizable) |

---

## If Something Goes Wrong
1. Check logs: `./dev-run.sh logs` or `docker logs -n 100 <container>`
2. Restart: `./dev-run.sh stop && ./dev-run.sh start`
3. Reinitialize DB: `rm athina-web/db.sqlite3 && touch athina-web/db.sqlite3 && (restart stack) && docker exec -u root ... migrate`
4. See this file for troubleshooting

---

## Notes
- All containers in a dev stack share a docker network (automatically created by compose)
- Production and dev are isolated by project name, network, and ports
- Stopping the dev stack preserves the SQLite DB file
- Multiple dev stacks can run simultaneously with different `PROJECT_NAME` and ports

---

**Verified: November 10, 2025 — All tests passing**
