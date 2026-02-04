"""Tests for DatabaseRegistry."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from mcp_sql_server.config import DatabaseConfig, PoolConfig
from mcp_sql_server.registry import DatabaseRegistry


@pytest.fixture
def default_config() -> DatabaseConfig:
    return DatabaseConfig(
        host="host1", port=1433, user="u1", password="p1",
        database="db1", driver="ODBC Driver 17 for SQL Server",
    )


@pytest.fixture
def second_config() -> DatabaseConfig:
    return DatabaseConfig(
        host="host2", port=1433, user="u2", password="p2",
        database="db2", driver="ODBC Driver 17 for SQL Server",
    )


@pytest.fixture
def registry(default_config: DatabaseConfig, second_config: DatabaseConfig) -> DatabaseRegistry:
    return DatabaseRegistry(
        configs={"default": default_config, "analytics": second_config},
    )


class TestDatabaseRegistry:
    def test_init_requires_default(self, second_config):
        with pytest.raises(ValueError, match="must include a 'default'"):
            DatabaseRegistry(configs={"other": second_config})

    def test_list_databases(self, registry):
        names = registry.list_databases()
        assert "default" in names
        assert "analytics" in names

    @patch("pyodbc.connect")
    def test_get_default(self, mock_connect, registry):
        mock_connect.return_value = MagicMock()
        db = registry.get("default")
        assert db is not None
        assert db.config.database == "db1"

    @patch("pyodbc.connect")
    def test_get_named(self, mock_connect, registry):
        mock_connect.return_value = MagicMock()
        db = registry.get("analytics")
        assert db is not None
        assert db.config.database == "db2"

    def test_get_unknown_raises(self, registry):
        with pytest.raises(KeyError, match="Unknown database 'nonexistent'"):
            registry.get("nonexistent")

    @patch("pyodbc.connect")
    def test_get_returns_same_instance(self, mock_connect, registry):
        mock_connect.return_value = MagicMock()
        db1 = registry.get("default")
        db2 = registry.get("default")
        assert db1 is db2

    @patch("pyodbc.connect")
    def test_lazy_init(self, mock_connect, registry):
        """Manager not created until get() is called."""
        assert len(registry._managers) == 0
        mock_connect.return_value = MagicMock()
        registry.get("default")
        assert "default" in registry._managers
        assert "analytics" not in registry._managers

    @patch("pyodbc.connect")
    def test_close_all(self, mock_connect, registry):
        mock_connect.return_value = MagicMock()
        registry.get("default")
        registry.get("analytics")
        assert len(registry._managers) == 2
        registry.close()
        assert len(registry._managers) == 0

    @patch("pyodbc.connect")
    def test_close_database(self, mock_connect, registry):
        mock_connect.return_value = MagicMock()
        registry.get("default")
        registry.get("analytics")
        registry.close_database("analytics")
        assert "analytics" not in registry._managers
        assert "default" in registry._managers

    def test_close_database_unknown_raises(self, registry):
        with pytest.raises(KeyError):
            registry.close_database("nonexistent")

    @patch("pyodbc.connect")
    def test_get_thread_safe(self, mock_connect, registry):
        """Concurrent get() calls return the same instance."""
        mock_connect.return_value = MagicMock()
        results = {}

        def get_db(name, idx):
            results[idx] = registry.get(name)

        threads = [threading.Thread(target=get_db, args=("default", i)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        instances = list(results.values())
        assert all(inst is instances[0] for inst in instances)

    def test_get_database_info(self, registry):
        info = registry.get_database_info()
        assert len(info) == 2
        names = [d["name"] for d in info]
        assert "default" in names
        assert "analytics" in names
        # No password exposed
        for db in info:
            assert "password" not in db

    @patch("mcp_sql_server.registry.load_all_database_configs")
    @patch("mcp_sql_server.registry.load_all_pool_configs")
    def test_from_env(self, mock_pool_configs, mock_db_configs, default_config):
        mock_db_configs.return_value = {"default": default_config}
        mock_pool_configs.return_value = {"default": PoolConfig()}
        registry = DatabaseRegistry.from_env()
        assert "default" in registry.list_databases()

    @patch("pyodbc.connect")
    def test_get_default_no_arg(self, mock_connect, registry):
        """get() with no argument returns default database."""
        mock_connect.return_value = MagicMock()
        db = registry.get()
        assert db.config.database == "db1"

    @patch("pyodbc.connect")
    def test_close_continues_after_error(self, mock_connect, registry):
        """GAP 7: close() should close all managers even if one raises."""
        mock_connect.return_value = MagicMock()
        registry.get("default")
        registry.get("analytics")

        # Make the first manager's close() raise
        first_manager = registry._managers["default"]
        first_manager.close = MagicMock(side_effect=RuntimeError("close failed"))
        second_manager = registry._managers["analytics"]
        second_manager.close = MagicMock()

        # close() should not raise and should clear all managers
        registry.close()
        assert len(registry._managers) == 0
        first_manager.close.assert_called_once()
        second_manager.close.assert_called_once()
