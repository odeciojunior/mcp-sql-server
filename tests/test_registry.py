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
        """Unprobed, a valid config is "unknown" -- it used to claim "ok"."""
        info = self._from_env_with_bad_alias(default_config).get_database_info()
        by_name = {d["name"]: d for d in info}
        assert by_name["default"]["status"] == "unknown"
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
    # "unknown", not "ok": the resource does not probe, so it cannot claim the
    # database is reachable. It previously printed "ok" unconditionally.
    assert "| default | host1 | 1433 | db1 | unknown |" in text


class TestConnectivityProbe:
    """`status` must never claim reachability without having measured it.

    Before this, get_database_info() reported status "ok" for every alias whose
    config merely parsed -- including databases that were OFFLINE on the server.
    """

    def test_probe_returns_ok_when_select_succeeds(self, registry):
        with patch("pyodbc.connect", return_value=MagicMock()) as connect:
            assert registry.probe("default") == ("ok", None)
        assert connect.called

    def test_probe_runs_select_one(self, registry):
        """A connection that opens but cannot serve a query is not reachable."""
        conn = MagicMock()
        with patch("pyodbc.connect", return_value=conn):
            registry.probe("default")
        conn.cursor.return_value.execute.assert_called_once_with("SELECT 1")

    def test_probe_closes_the_connection(self, registry):
        conn = MagicMock()
        with patch("pyodbc.connect", return_value=conn):
            registry.probe("default")
        conn.close.assert_called_once()

    def test_probe_reports_unreachable_on_failure(self, registry):
        with patch("pyodbc.connect", side_effect=Exception("Cannot open database")):
            status, error = registry.probe("default")
        assert status == "unreachable"
        assert error and "Cannot open database" in error

    def test_probe_error_does_not_leak_the_password(self, registry):
        boom = Exception("login failed for PWD=p1 at host1")
        with patch("pyodbc.connect", side_effect=boom):
            _, error = registry.probe("default")
        assert error is not None
        assert "p1" not in error

    def test_probe_does_not_create_a_pooled_manager(self, registry):
        """Probing must not leave a DatabaseManager cached for a dead database."""
        with patch("pyodbc.connect", return_value=MagicMock()):
            registry.probe("default")
        assert registry._managers == {}

    def test_probe_unknown_alias_raises(self, registry):
        with pytest.raises(KeyError):
            registry.probe("nope")

    def test_info_without_probe_reports_unknown(self, registry):
        """The old cheap listing stays available -- but it no longer says "ok"."""
        with patch("pyodbc.connect") as connect:
            info = registry.get_database_info()
        connect.assert_not_called()
        assert {d["status"] for d in info} == {"unknown"}

    def test_info_with_probe_reports_measured_status(self, default_config, second_config):
        registry = DatabaseRegistry(
            configs={"default": default_config, "analytics": second_config},
        )

        def _connect(conn_str, *a, **kw):
            if "db2" in conn_str:
                raise Exception("Cannot open database")
            return MagicMock()

        with patch("pyodbc.connect", side_effect=_connect):
            info = {d["name"]: d for d in registry.get_database_info(probe=True)}

        assert info["default"]["status"] == "ok"
        assert info["analytics"]["status"] == "unreachable"
        assert "Cannot open database" in info["analytics"]["error"]

    def test_misconfigured_alias_is_never_probed(self, default_config):
        registry = DatabaseRegistry(
            configs={"default": default_config},
            config_errors={"broken": "DB_BROKEN_HOST is required"},
        )
        with patch("pyodbc.connect", return_value=MagicMock()) as connect:
            info = {d["name"]: d for d in registry.get_database_info(probe=True)}
        assert info["broken"]["status"] == "misconfigured"
        assert connect.call_count == 1  # only "default"

    def test_probes_run_in_parallel(self, default_config):
        """Serial probing of N dead aliases would take N * timeout."""
        import time

        configs = {"default": default_config}
        for i in range(4):
            configs[f"db{i}"] = default_config
        registry = DatabaseRegistry(configs=configs)

        def _slow(*a, **kw):
            time.sleep(0.3)
            return MagicMock()

        with patch("pyodbc.connect", side_effect=_slow):
            start = time.monotonic()
            registry.get_database_info(probe=True)
            elapsed = time.monotonic() - start

        assert elapsed < 0.9, f"probes appear serialised: {elapsed:.2f}s for 5 aliases"
