"""Pytest fixtures for testing."""

import os
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sample_env(tmp_path: Path) -> Path:
    """Create a sample .env file for testing."""
    env_content = """DB_HOST=localhost
DB_PORT=1433
DB_USER=testuser
DB_PASSWORD=testpass
DB_NAME=testdb
"""
    env_file = tmp_path / ".env"
    env_file.write_text(env_content)
    return env_file


@pytest.fixture
def complete_env_vars() -> dict[str, str]:
    """Complete set of environment variables for database configuration."""
    return {
        "DB_HOST": "test-server.example.com",
        "DB_PORT": "1433",
        "DB_USER": "test_user",
        "DB_PASSWORD": "test_password123",
        "DB_NAME": "test_database",
        "DB_DRIVER": "ODBC Driver 17 for SQL Server",
        "DB_ENCRYPT": "true",
        "DB_TRUST_CERT": "true",
        "DB_TIMEOUT": "60",
        "DB_QUERY_TIMEOUT": "180",
    }


@pytest.fixture
def minimal_env_vars() -> dict[str, str]:
    """Minimal required environment variables."""
    return {
        "DB_HOST": "localhost",
        "DB_USER": "sa",
        "DB_PASSWORD": "password",
        "DB_NAME": "master",
    }


@pytest.fixture
def pool_env_vars() -> dict[str, str]:
    """Environment variables for connection pool configuration."""
    return {
        "DB_POOL_MIN_SIZE": "2",
        "DB_POOL_MAX_SIZE": "10",
        "DB_POOL_IDLE_TIMEOUT": "600",
        "DB_POOL_HEALTH_CHECK_INTERVAL": "60",
        "DB_POOL_ACQUIRE_TIMEOUT": "15.0",
        "DB_POOL_MAX_LIFETIME": "7200",
    }


@pytest.fixture
def mock_cursor() -> MagicMock:
    """Create a mock database cursor with common attributes."""
    cursor = MagicMock()
    cursor.description = [
        ("id", int, None, None, None, None, None),
        ("name", str, None, None, None, None, None),
        ("value", float, None, None, None, None, None),
    ]
    cursor.fetchall.return_value = [
        (1, "test1", 10.5),
        (2, "test2", 20.5),
        (3, "test3", 30.5),
    ]
    cursor.rowcount = 3
    cursor.execute = MagicMock()
    cursor.close = MagicMock()
    return cursor


@pytest.fixture
def mock_cursor_empty() -> MagicMock:
    """Create a mock cursor that returns no results."""
    cursor = MagicMock()
    cursor.description = None
    cursor.fetchall.return_value = []
    cursor.rowcount = 0
    cursor.execute = MagicMock()
    cursor.close = MagicMock()
    return cursor


@pytest.fixture
def mock_connection(mock_cursor: MagicMock) -> MagicMock:
    """Create a mock database connection."""
    connection = MagicMock()
    connection.cursor.return_value = mock_cursor
    connection.execute = MagicMock()
    connection.commit = MagicMock()
    connection.rollback = MagicMock()
    connection.close = MagicMock()
    connection.timeout = 30
    return connection


@pytest.fixture
def mock_pyodbc(mock_connection: MagicMock) -> Generator[MagicMock, None, None]:
    """Patch pyodbc.connect to return mock connection."""
    with patch("pyodbc.connect") as mock_connect:
        mock_connect.return_value = mock_connection
        yield mock_connect


@pytest.fixture
def sample_config() -> "DatabaseConfig":
    """Create a sample DatabaseConfig for testing."""
    from sql_playground_mcp.config import DatabaseConfig

    return DatabaseConfig(
        host="test-host",
        port=1433,
        user="test-user",
        password="test-pass",
        database="test-db",
        driver="ODBC Driver 17 for SQL Server",
        connection_timeout=30,
        query_timeout=120,
        encrypt=False,
        trust_cert=False,
    )


@pytest.fixture
def sample_config_with_ssl() -> "DatabaseConfig":
    """Create a DatabaseConfig with SSL enabled."""
    from sql_playground_mcp.config import DatabaseConfig

    return DatabaseConfig(
        host="secure-host",
        port=1433,
        user="secure-user",
        password="secure-pass",
        database="secure-db",
        driver="ODBC Driver 18 for SQL Server",
        connection_timeout=60,
        query_timeout=180,
        encrypt=True,
        trust_cert=True,
    )


@pytest.fixture
def mock_db_manager(mock_pyodbc: MagicMock, sample_config: "DatabaseConfig") -> "DatabaseManager":
    """Create a DatabaseManager with mocked pyodbc."""
    from sql_playground_mcp.database import DatabaseManager

    return DatabaseManager(sample_config)


@pytest.fixture
def mock_get_db(mock_db_manager: "DatabaseManager") -> Generator[MagicMock, None, None]:
    """Patch server.get_db to return mock DatabaseManager."""
    with patch("sql_playground_mcp.server.get_db") as mock:
        mock.side_effect = lambda database="default": mock_db_manager
        yield mock


@pytest.fixture
def env_with_vars(complete_env_vars: dict[str, str]) -> Generator[None, None, None]:
    """Context manager that sets environment variables for testing."""
    original_env = os.environ.copy()
    os.environ.update(complete_env_vars)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original_env)


@pytest.fixture
def env_with_minimal_vars(minimal_env_vars: dict[str, str]) -> Generator[None, None, None]:
    """Context manager with only required environment variables."""
    original_env = os.environ.copy()
    # Clear relevant env vars first
    for key in list(os.environ.keys()):
        if key.startswith("DB_"):
            del os.environ[key]
    os.environ.update(minimal_env_vars)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original_env)


@pytest.fixture
def mock_query_results() -> list[dict[str, Any]]:
    """Sample query results for testing."""
    return [
        {"id": 1, "name": "Alice", "email": "alice@example.com"},
        {"id": 2, "name": "Bob", "email": "bob@example.com"},
        {"id": 3, "name": "Charlie", "email": "charlie@example.com"},
    ]


@pytest.fixture
def mock_table_list() -> list[dict[str, Any]]:
    """Sample table list for testing."""
    return [
        {"schema": "dbo", "name": "Users", "type": "BASE TABLE"},
        {"schema": "dbo", "name": "Orders", "type": "BASE TABLE"},
        {"schema": "dbo", "name": "vwUserOrders", "type": "VIEW"},
        {"schema": "audit", "name": "Logs", "type": "BASE TABLE"},
    ]


@pytest.fixture
def mock_column_definitions() -> list[dict[str, Any]]:
    """Sample column definitions for testing."""
    return [
        {
            "name": "id",
            "type": "int",
            "max_length": None,
            "precision": 10,
            "nullable": "NO",
            "default_value": None,
        },
        {
            "name": "name",
            "type": "nvarchar",
            "max_length": 100,
            "precision": None,
            "nullable": "YES",
            "default_value": None,
        },
        {
            "name": "created_at",
            "type": "datetime2",
            "max_length": None,
            "precision": 27,
            "nullable": "NO",
            "default_value": "(getutcdate())",
        },
    ]


@pytest.fixture
def query_dir(tmp_path: Path) -> Path:
    """Create a temporary query directory with sample SQL files."""
    query_path = tmp_path / "query"
    query_path.mkdir()

    # Create sample query files
    (query_path / "select_users.sql").write_text("SELECT * FROM Users")
    (query_path / "count_orders.sql").write_text("SELECT COUNT(*) as cnt FROM Orders")
    (query_path / "complex-query.sql").write_text(
        "WITH cte AS (SELECT id FROM Users) SELECT * FROM cte"
    )

    return query_path


@pytest.fixture
def mock_procedure_results() -> list[dict[str, Any]]:
    """Sample stored procedure results."""
    return [
        {"result_id": 1, "message": "Success", "rows_affected": 5},
    ]
