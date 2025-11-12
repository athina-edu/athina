#!/bin/bash
# Test runner script for athina
# Usage: ./run_tests.sh [options]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
USE_DOCKER_MYSQL=0
VERBOSE=0
TEST_PATH=""

# Help message
show_help() {
    cat << EOF
Athina Test Runner

Usage: ./run_tests.sh [OPTIONS] [TEST_PATH]

Options:
    -h, --help              Show this help message
    -d, --docker-mysql      Use MySQL in Docker (default: SQLite)
    -v, --verbose           Verbose output
    -c, --coverage          Run with coverage report

Examples:
    ./run_tests.sh                              # Run all tests with SQLite
    ./run_tests.sh -d                           # Run all tests with Docker MySQL
    ./run_tests.sh -v tests/test_athina.py      # Run specific test file verbosely
    ./run_tests.sh -c                           # Run with coverage report

Environment:
    - Local Development: Uses SQLite (no database setup needed)
    - Docker/Production: Uses MySQL with automatic container management

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -d|--docker-mysql)
            USE_DOCKER_MYSQL=1
            shift
            ;;
        -v|--verbose)
            VERBOSE=1
            shift
            ;;
        -c|--coverage)
            USE_COVERAGE=1
            shift
            ;;
        -*)
            echo -e "${RED}Unknown option: $1${NC}"
            show_help
            exit 1
            ;;
        *)
            TEST_PATH="$1"
            shift
            ;;
    esac
done

# Check if pipenv is installed
if ! command -v pipenv &> /dev/null; then
    echo -e "${RED}Error: pipenv is not installed${NC}"
    echo "Install it with: pip install pipenv"
    exit 1
fi

# Check if dependencies are installed
if [ ! -d "$(pipenv --venv 2>/dev/null)" ]; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pipenv install --dev
fi

# Build pytest command
PYTEST_CMD="pipenv run pytest"

if [ $VERBOSE -eq 1 ]; then
    PYTEST_CMD="$PYTEST_CMD -v -s"
fi

if [ ! -z "$TEST_PATH" ]; then
    PYTEST_CMD="$PYTEST_CMD $TEST_PATH"
fi

if [ ! -z "$USE_COVERAGE" ]; then
    PYTEST_CMD="$PYTEST_CMD --cov=athina --cov-report=html --cov-report=term"
fi

# Set environment variables
if [ $USE_DOCKER_MYSQL -eq 1 ]; then
    echo -e "${YELLOW}Running tests with Docker MySQL...${NC}"
    export ATHINA_USE_DOCKER_MYSQL=1
else
    echo -e "${YELLOW}Running tests with SQLite (local development mode)...${NC}"
    unset ATHINA_USE_DOCKER_MYSQL
fi

# Run tests
echo -e "${GREEN}Executing: $PYTEST_CMD${NC}"
echo ""

if $PYTEST_CMD; then
    echo ""
    echo -e "${GREEN}✓ Tests passed!${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}✗ Tests failed!${NC}"
    exit 1
fi
