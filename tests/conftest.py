"""
Pytest configuration and fixtures for athina tests.

This module manages Docker containers for testing, including MySQL database.
When running tests, if required services (like MySQL) are not available locally,
they will be automatically started using Docker.

For local development, tests can use SQLite by setting ATHINA_DB=sqlite.
In Docker/production environments, MySQL will be used with automatic container management.
"""

import os
import time
import docker
import pytest
import subprocess
from typing import Generator


# Determine if we should use Docker-based MySQL or SQLite
USE_DOCKER_MYSQL = os.environ.get('ATHINA_USE_DOCKER_MYSQL', '').lower() in ('1', 'true', 'yes')
USE_SQLITE = os.environ.get('ATHINA_DB', '').lower() == 'sqlite' or not USE_DOCKER_MYSQL


def is_mysql_available() -> bool:
    """Check if MySQL is available on the configured host and port."""
    import socket

    host = os.environ.get('ATHINA_MYSQL_HOST', 'localhost')
    port = int(os.environ.get('ATHINA_MYSQL_PORT', '3306'))

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False


def wait_for_mysql(host: str, port: int, timeout: int = 30) -> bool:
    """Wait for MySQL to become available."""
    import socket

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                # Give it a bit more time to fully initialize
                time.sleep(2)
                return True
        except:
            pass
        time.sleep(0.5)
    return False


@pytest.fixture(scope="session")
def mysql_container() -> Generator[dict, None, None]:
    """
    Fixture that ensures a database is available for tests.

    For local development: Uses SQLite (no Docker required)
    For Docker environments: Uses MySQL with automatic container management

    Set ATHINA_USE_DOCKER_MYSQL=1 to force MySQL with Docker container
    """
    # If using SQLite mode, skip MySQL setup entirely
    if USE_SQLITE:
        print("Using SQLite for tests (local development mode)")
        os.environ['ATHINA_DB'] = 'sqlite'
        os.environ['ATHINA_TEST_MODE'] = '1'
        # Use a unique SQLite DB per test session to avoid conflicts
        sqlite_db = f"/tmp/athina_test_{os.getpid()}.db"
        os.environ['ATHINA_SQLITE_DB'] = sqlite_db

        yield {
            'backend': 'sqlite',
            'database': sqlite_db,
            'container': None
        }

        # Cleanup SQLite database after tests
        try:
            if os.path.exists(sqlite_db):
                os.remove(sqlite_db)
        except:
            pass
        return

    # Check if MySQL is already available
    if is_mysql_available():
        print("Using existing MySQL instance")
        yield {
            'backend': 'mysql',
            'host': os.environ.get('ATHINA_MYSQL_HOST', 'localhost'),
            'port': int(os.environ.get('ATHINA_MYSQL_PORT', '3306')),
            'username': os.environ.get('ATHINA_MYSQL_USERNAME', 'athina'),
            'password': os.environ.get('ATHINA_MYSQL_PASSWORD', 'password'),
            'container': None
        }
        return

    # MySQL not available, start a Docker container
    print("Starting MySQL container for tests...")

    client = docker.from_env()
    container_name = f"athina_test_mysql_{os.getpid()}"

    # Remove any existing container with the same name
    try:
        old_container = client.containers.get(container_name)
        old_container.remove(force=True)
    except docker.errors.NotFound:
        pass

    # Start MySQL container
    container = client.containers.run(
        "mysql:8.0",
        name=container_name,
        environment={
            'MYSQL_ROOT_PASSWORD': 'rootpassword',
            'MYSQL_DATABASE': 'athina_test',
            'MYSQL_USER': 'athina',
            'MYSQL_PASSWORD': 'password'
        },
        ports={'3306/tcp': None},  # Let Docker assign a random port
        detach=True,
        remove=True
    )

    # Get the assigned port
    container.reload()
    port = int(container.attrs['NetworkSettings']['Ports']['3306/tcp'][0]['HostPort'])

    # Update environment variables
    os.environ['ATHINA_MYSQL_HOST'] = 'localhost'
    os.environ['ATHINA_MYSQL_PORT'] = str(port)
    os.environ['ATHINA_MYSQL_USERNAME'] = 'athina'
    os.environ['ATHINA_MYSQL_PASSWORD'] = 'password'

    # Wait for MySQL to be ready
    if not wait_for_mysql('localhost', port):
        container.stop()
        raise RuntimeError("MySQL container failed to start in time")

    print(f"MySQL container started on port {port}")

    yield {
        'backend': 'mysql',
        'host': 'localhost',
        'port': port,
        'username': 'athina',
        'password': 'password',
        'container': container
    }

    # Cleanup
    print("Stopping MySQL container...")
    try:
        container.stop()
    except:
        pass


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment(mysql_container):
    """
    Auto-use fixture that sets up the complete test environment.

    This ensures a database backend (SQLite or MySQL) is available before any tests run.
    """
    # Set environment variables based on backend
    if mysql_container['backend'] == 'sqlite':
        # SQLite mode - environment variables already set in mysql_container fixture
        pass
    else:
        # MySQL mode - set connection parameters
        if os.environ.get('ATHINA_MYSQL_HOST', 0) == 0:
            os.environ['ATHINA_MYSQL_HOST'] = mysql_container['host']
        if os.environ.get('ATHINA_MYSQL_PORT', 0) == 0:
            os.environ['ATHINA_MYSQL_PORT'] = str(mysql_container['port'])
        if os.environ.get('ATHINA_MYSQL_USERNAME', 0) == 0:
            os.environ['ATHINA_MYSQL_USERNAME'] = mysql_container['username']
        if os.environ.get('ATHINA_MYSQL_PASSWORD', 0) == 0:
            os.environ['ATHINA_MYSQL_PASSWORD'] = mysql_container['password']

    yield

    # Cleanup is handled by the mysql_container fixture


def pytest_configure(config):
    """
    Called after command line options have been parsed but BEFORE test collection.

    This is the right place to set environment variables that affect module imports.
    """
    # Set database backend early, before any test modules are imported
    # This ensures the DB object in users.py is created with the right backend
    use_docker_mysql = os.environ.get('ATHINA_USE_DOCKER_MYSQL', '').lower() in ('1', 'true', 'yes')

    if not use_docker_mysql:
        # Use SQLite for local development testing
        os.environ['ATHINA_DB'] = 'sqlite'
        os.environ['ATHINA_TEST_MODE'] = '1'
        sqlite_db = f"/tmp/athina_test_{os.getpid()}.db"
        os.environ['ATHINA_SQLITE_DB'] = sqlite_db
        print(f"[pytest] Configured to use SQLite backend: {sqlite_db}")
    else:
        # Ensure Docker is available for MySQL containers
        try:
            subprocess.run(['docker', '--version'],
                          check=True,
                          capture_output=True,
                          text=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError(
                "Docker is required for running tests with MySQL but is not available. "
                "Please install Docker and ensure the Docker daemon is running, "
                "or run tests with SQLite by not setting ATHINA_USE_DOCKER_MYSQL."
            )
        print("[pytest] Configured to use MySQL backend with Docker")
