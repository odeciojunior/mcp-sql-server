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

    @patch("mcp_sql_server.registry.load_pool_config")
    @patch("mcp_sql_server.registry.load_database_config")
    @patch("mcp_sql_server.registry.get_database_names")
    def test_from_env(self, mock_names, mock_db_config, mock_pool_config, default_config):
        mock_names.return_value = ["default"]
        mock_db_config.return_value = default_config
        mock_pool_config.return_value = PoolConfig()
        registry = DatabaseRegistry.from_env()
        assert "default" in registry.list_databases()
        assert registry.config_errors == {}

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


class TestMisconfiguredAliases:
    def _from_env_with_bad_alias(self, default_config):
        def db_config(name, env_path=None):
            if name == "broken":
                DatabaseConfig(host="", user="u", password="p-secret", database="d")
            return default_config

        with patch("mcp_sql_server.registry.get_database_names", return_value=["default", "broken"]), \
             patch("mcp_sql_server.registry.load_database_config", side_effect=db_config), \
             patch("mcp_sql_server.registry.load_pool_config", return_value=PoolConfig()):
            return DatabaseRegistry.from_env()

    def test_bad_alias_recorded_not_raised(self, default_config):
        registry = self._from_env_with_bad_alias(default_config)
        assert registry.config_errors == {"broken": "host: string_too_short"}
        assert registry.list_databases() == ["default", "broken"]

    def test_get_bad_alias_raises_safe_message(self, default_config):
        registry = self._from_env_with_bad_alias(default_config)
        with pytest.raises(ValueError, match="Database 'broken' is misconfigured: host: string_too_short") as exc_info:
            registry.get("broken")
        assert "p-secret" not in str(exc_info.value)

    def test_default_still_works(self, default_config, mock_pyodbc):
        registry = self._from_env_with_bad_alias(default_config)
        assert registry.get("default") is registry.get("default")
        registry.close()

    def test_database_info_reports_status(self, default_config):
        info = self._from_env_with_bad_alias(default_config).get_database_info()
        by_name = {d["name"]: d for d in info}
        assert by_name["default"]["status"] == "ok"
        assert by_name["broken"] == {
            "name": "broken",
            "status": "misconfigured",
            "error": "host: string_too_short",
        }

    def test_misconfigured_default_allowed(self, second_config):
        registry = DatabaseRegistry(
            configs={"analytics": second_config},
            config_errors={"default": "host: string_too_short"},
        )
        with pytest.raises(ValueError, match="misconfigured"):
            registry.get("default")

    def test_invalid_db_databases_still_raises(self):
        with patch("mcp_sql_server.registry.get_database_names", side_effect=ValueError("Invalid database alias '1x'")):
            with pytest.raises(ValueError, match="Invalid database alias"):
                DatabaseRegistry.from_env()


def test_resource_databases_shows_status(default_config):
    from mcp_sql_server.resources import database_info

    registry = DatabaseRegistry(
        configs={"default": default_config},
        config_errors={"broken": "host: string_too_short"},
    )
    with patch.object(database_info, "_get_registry", return_value=registry):
        text = database_info.resource_databases()
    assert "| broken | - | - | - | misconfigured: host: string_too_short |" in text
    assert "| default | host1 | 1433 | db1 | ok |" in text
