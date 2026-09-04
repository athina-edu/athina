#!/bin/bash
# =============================================================================
# Athina Development Server Startup Script
# =============================================================================
# Starts MySQL (Django's web DB) and the grading engine MySQL, then launches
# the Django dev server. Both databases use MySQL to match production.
#
# Usage:
#   ./dev-run.sh          Start everything
#   ./dev-run.sh stop     Stop the dev MySQL container
#   ./dev-run.sh status   Show container status
# =============================================================================
set -e

MYSQL_CONTAINER="athina-mysql-dev"
MYSQL_PORT=3307
MYSQL_ROOT_PASS="athina_dev_root"
MYSQL_USER="athina"
MYSQL_PASS="${ATHINA_MYSQL_PASSWORD:-changeme}"
MYSQL_DB="athina"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

cd "$(dirname "$0")"

# ── Stop ──────────────────────────────────────────────────────────
if [ "$1" = "stop" ]; then
    echo -e "${YELLOW}Stopping MySQL container...${NC}"
    docker rm -f "$MYSQL_CONTAINER" 2>/dev/null || true
    echo -e "${GREEN}Stopped.${NC}"
    exit 0
fi

# ── Status ────────────────────────────────────────────────────────
if [ "$1" = "status" ]; then
    docker ps -a --filter "name=$MYSQL_CONTAINER" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    exit 0
fi

# ── Start MySQL ───────────────────────────────────────────────────
if docker ps --filter "name=$MYSQL_CONTAINER" --format '{{.Names}}' | grep -q "$MYSQL_CONTAINER"; then
    echo -e "${GREEN}MySQL container already running.${NC}"
else
    # Remove any stopped container with the same name
    docker rm -f "$MYSQL_CONTAINER" 2>/dev/null || true

    echo -e "${YELLOW}Starting MySQL 8.0 container...${NC}"
    docker run -d --name "$MYSQL_CONTAINER" \
        -e MYSQL_ROOT_PASSWORD="$MYSQL_ROOT_PASS" \
        -e MYSQL_DATABASE="$MYSQL_DB" \
        -e MYSQL_USER="$MYSQL_USER" \
        -e MYSQL_PASSWORD="$MYSQL_PASS" \
        -v athina-mysql-data:/var/lib/mysql \
        -p "$MYSQL_PORT":3306 \
        mysql:8.0

    echo -e "${YELLOW}Waiting for MySQL to be ready...${NC}"
    for i in $(seq 1 60); do
        if docker exec "$MYSQL_CONTAINER" mysqladmin ping -u root -p"$MYSQL_ROOT_PASS" --silent 2>/dev/null; then
            echo -e "${GREEN}MySQL is ready!${NC}"
            break
        fi
        if [ "$i" -eq 60 ]; then
            echo -e "${RED}MySQL failed to start within 60s. Check: docker logs $MYSQL_CONTAINER${NC}"
            exit 1
        fi
        sleep 1
    done

    # Create grading engine tables if they don't exist
    echo -e "${YELLOW}Ensuring grading tables exist...${NC}"
    docker exec "$MYSQL_CONTAINER" mysql -u "$MYSQL_USER" -p"$MYSQL_PASS" "$MYSQL_DB" -e "
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT NOT NULL, course_id BIGINT NOT NULL, assignment_id BIGINT NOT NULL,
        user_fullname VARCHAR(255) DEFAULT '', secondary_id VARCHAR(255) DEFAULT '',
        repository_url VARCHAR(255) DEFAULT NULL,
        url_date DATETIME DEFAULT '0001-01-01 00:00:00', new_url BOOL DEFAULT FALSE,
        commit_date DATETIME DEFAULT '0001-01-01 00:00:00', same_url_flag BOOL DEFAULT FALSE,
        plagiarism_to_grade BOOL DEFAULT FALSE,
        last_plagiarism_check DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_graded DATETIME DEFAULT '0001-01-01 00:00:00', changed_state BOOL DEFAULT FALSE,
        last_grade SMALLINT DEFAULT NULL, last_report BLOB DEFAULT NULL,
        moss_max INT DEFAULT 0, moss_average INT DEFAULT 0,
        tester_active BOOL DEFAULT FALSE,
        tester_date DATETIME DEFAULT '0001-01-01 00:00:00',
        force_test BOOL DEFAULT FALSE, use_webhook BOOL DEFAULT FALSE,
        webhook_event BOOL DEFAULT FALSE, webhook_token VARCHAR(255) DEFAULT '',
        PRIMARY KEY (user_id, course_id, assignment_id),
        INDEX idx_repo_token (repository_url, webhook_token),
        INDEX idx_course_assignment (course_id, assignment_id)
    ) ENGINE=InnoDB;
    CREATE TABLE IF NOT EXISTS assignmentdata (
        course_id BIGINT NOT NULL, assignment_id BIGINT NOT NULL,
        variable VARCHAR(255) NOT NULL, variable_value TEXT DEFAULT NULL,
        PRIMARY KEY (variable, course_id, assignment_id)
    ) ENGINE=InnoDB;
    " 2>/dev/null
    echo -e "${GREEN}Grading tables ready.${NC}"
fi

# ── Set environment variables for Django ──────────────────────────
export ATHINA_MYSQL_HOST=localhost
export ATHINA_MYSQL_PORT="$MYSQL_PORT"
export ATHINA_MYSQL_USERNAME="$MYSQL_USER"
export ATHINA_MYSQL_PASSWORD="$MYSQL_PASS"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Athina Dev Environment${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "  Web UI:          http://localhost:8000"
echo -e "  Grading DB:      localhost:$MYSQL_PORT (MySQL 8.0)"
echo -e "  DB credentials:  $MYSQL_USER / $MYSQL_PASS"
echo -e "  Django DB:       SQLite (db.sqlite3)"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""

# ── Run Django dev server ────────────────────────────────────────
echo -e "${YELLOW}Starting Django dev server...${NC}"
exec python manage.py runserver 0.0.0.0:8000
