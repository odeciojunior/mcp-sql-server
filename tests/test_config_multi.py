"""Tests for multi-database configuration loading."""

import os
from typing import Generator
from unittest.mock import patch

import pytest

from mcp_sql_server.config import (
    DatabaseConfig,
    PoolConfig,
    get_database_names,
    load_all_database_configs,
    load_all_pool_configs,
)


@pytest.fixture
def multi_db_env() -> Generator[None, None, None]:
    """Set up environment variables for multi-database config."""
    original_env = os.environ.copy()
    for key in list(os.environ.keys()):
        if key.startswith("DB_"):
            del os.environ[key]

    os.environ.update({
        # Default database
        "DB_HOST": "host1",
        "DB_USER": "user1",
        "DB_PASSWORD": "pass1",
        "DB_NAME": "db1",
        # Additional databases
        "DB_DATABASES": "analytics,archive",
        # Analytics database
        "DB_ANALYTICS_HOST": "host2",
        "DB_ANALYTICS_USER": "user2",
        "DB_ANALYTICS_PASSWORD": "pass2",
        "DB_ANALYTICS_NAME": "db2",
        # Archive database
        "DB_ARCHIVE_HOST": "host3",
        "DB_ARCHIVE_USER": "user3",
        "DB_ARCHIVE_PASSWORD": "pass3",
        "DB_ARCHIVE_NAME": "db3",
    })
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original_env)


@pytest.fixture
def single_db_env() -> Generator[None, None, None]:
    """Set up environment for single database (no DB_DATABASES)."""
    original_env = os.environ.copy()
    for key in list(os.environ.keys()):
        if key.startswith("DB_"):
            del os.environ[key]

    os.environ.update({
        "DB_HOST": "host1",
        "DB_USER": "user1",
        "DB_PASSWORD": "pass1",
        "DB_NAME": "db1",
    })
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original_env)


class TestGetDatabaseNames:
    def test_single_db_returns_default_only(self, single_db_env):
        names = get_database_names()
        assert names == ["default"]

    def test_multi_db_returns_all(self, multi_db_env):
        names = get_database_names()
        assert names == ["default", "analytics", "archive"]

    def test_empty_db_databases(self, single_db_env):
        os.environ["DB_DATABASES"] = ""
        names = get_database_names()
        assert names == ["default"]

    def test_whitespace_handling(self, single_db_env):
        os.environ["DB_DATABASES"] = " analytics , archive "
        names = get_database_names()
        assert names == ["default", "analytics", "archive"]

    def test_invalid_alias_rejected(self, single_db_env):
        os.environ["DB_DATABASES"] = "bad;name"
        with pytest.raises(ValueError, match="Invalid database alias"):
            get_database_names()

    def test_numeric_start_rejected(self, single_db_env):
        os.environ["DB_DATABASES"] = "123bad"
        with pytest.raises(ValueError, match="Invalid database alias"):
            get_database_names()

    def test_default_alias_skipped(self, single_db_env):
        os.environ["DB_DATABASES"] = "default,analytics"
        os.environ["DB_ANALYTICS_HOST"] = "host2"
        os.environ["DB_ANALYTICS_USER"] = "user2"
        os.environ["DB_ANALYTICS_PASSWORD"] = "pass2"
        os.environ["DB_ANALYTICS_NAME"] = "db2"
        names = get_database_names()
        assert names == ["default", "analytics"]
        assert names.count("default") == 1


class TestDatabaseConfigFromEnvPrefixed:
    def test_reads_prefixed_vars(self, multi_db_env):
        config = DatabaseConfig.from_env_prefixed("analytics")
        assert config.host == "host2"
        assert config.user == "user2"
        assert config.password == "pass2"
        assert config.database == "db2"

    def test_case_insensitive_prefix(self, multi_db_env):
        config = DatabaseConfig.from_env_prefixed("ANALYTICS")
        assert config.host == "host2"

    def test_defaults_for_optional_fields(self, multi_db_env):
        config = DatabaseConfig.from_env_prefixed("analytics")
        assert config.port == 1433
        assert config.driver == "ODBC Driver 17 for SQL Server"
        assert config.encrypt is False
        assert config.trust_cert is False


class TestPoolConfigFromEnvPrefixed:
    def test_reads_prefixed_vars(self, single_db_env):
        os.environ["DB_ANALYTICS_POOL_MIN_SIZE"] = "3"
        os.environ["DB_ANALYTICS_POOL_MAX_SIZE"] = "15"
        config = PoolConfig.from_env_prefixed("analytics")
        assert config.min_size == 3
        assert config.max_size == 15

    def test_defaults_when_no_prefix_vars(self, single_db_env):
        config = PoolConfig.from_env_prefixed("analytics")
        assert config.min_size == 1
        assert config.max_size == 5


class TestLoadAllDatabaseConfigs:
    def test_single_db(self, single_db_env):
        configs = load_all_database_configs()
        assert list(configs.keys()) == ["default"]
        assert configs["default"].host == "host1"

    def test_multi_db(self, multi_db_env):
        configs = load_all_database_configs()
        assert "default" in configs
        assert "analytics" in configs
        assert "archive" in configs
        assert configs["default"].host == "host1"
        assert configs["analytics"].host == "host2"
        assert configs["archive"].host == "host3"


class TestLoadAllPoolConfigs:
    def test_single_db(self, single_db_env):
        configs = load_all_pool_configs()
        assert list(configs.keys()) == ["default"]

    def test_multi_db(self, multi_db_env):
        configs = load_all_pool_configs()
        assert "default" in configs
        assert "analytics" in configs
        assert "archive" in configs


class TestTimeoutEnvVars:
    """GAP 5: Tests that connection_timeout and query_timeout are read from env vars."""

    def test_default_timeout_values(self, single_db_env):
        config = DatabaseConfig.from_env()
        assert config.connection_timeout == 30
        assert config.query_timeout == 120

    def test_custom_timeout_from_env(self, single_db_env):
        os.environ["DB_TIMEOUT"] = "60"
        os.environ["DB_QUERY_TIMEOUT"] = "300"
        config = DatabaseConfig.from_env()
        assert config.connection_timeout == 60
        assert config.query_timeout == 300

    def test_prefixed_timeout_defaults(self, multi_db_env):
        config = DatabaseConfig.from_env_prefixed("analytics")
        assert config.connection_timeout == 30
        assert config.query_timeout == 120

    def test_prefixed_timeout_from_env(self, multi_db_env):
        os.environ["DB_ANALYTICS_TIMEOUT"] = "45"
        os.environ["DB_ANALYTICS_QUERY_TIMEOUT"] = "240"
        config = DatabaseConfig.from_env_prefixed("analytics")
        assert config.connection_timeout == 45
        assert config.query_timeout == 240
