"""Tests for DatabaseManager."""

import warnings
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import pyodbc

from sql_playground_mcp.config import DatabaseConfig
from sql_playground_mcp.database import DatabaseManager


@pytest.fixture
def non_pooled_db_manager(mock_pyodbc, sample_config):
    """Create a DatabaseManager without pooling (for legacy tests)."""
    return DatabaseManager(sample_config, use_pool=False)


class TestDatabaseManagerInit:
    """Tests for DatabaseManager initialization."""

    def test_init_stores_config(self, sample_config):
        db = DatabaseManager(sample_config)
        assert db.config == sample_config

    def test_init_connection_is_none(self, sample_config):
        db = DatabaseManager(sample_config)
        assert db._connection is None

    def test_init_with_different_configs(self):
        config1 = DatabaseConfig(
            host="host1", user="u", password="p", database="db1"
        )
        config2 = DatabaseConfig(
            host="host2", user="u", password="p", database="db2"
        )
        db1 = DatabaseManager(config1)
        db2 = DatabaseManager(config2)
        assert db1.config.host == "host1"
        assert db2.config.host == "host2"


class TestDatabaseManagerConnect:
    """Tests for DatabaseManager.connect() method (non-pooled mode)."""

    def test_connect_creates_connection(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        conn = db.connect()
        assert conn is not None
        mock_pyodbc.assert_called_once()

    def test_connect_uses_connection_string(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.connect()
        call_args = mock_pyodbc.call_args
        conn_str = call_args[0][0]
        assert "SERVER=test-host" in conn_str

    def test_connect_sets_query_timeout(self, mock_pyodbc, mock_connection, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        conn = db.connect()
        assert conn.timeout == sample_config.query_timeout

    def test_connect_reuses_existing_connection(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        # Make _is_connected return True
        with patch.object(db, '_is_connected', return_value=True):
            db._connection = MagicMock()
            original_conn = db._connection
            conn = db.connect()
            assert conn is original_conn
            # Should not create new connection
            mock_pyodbc.assert_not_called()

    def test_connect_reconnects_if_stale(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        # First connection
        db._connection = MagicMock()
        # Simulate stale connection
        with patch.object(db, '_is_connected', return_value=False):
            db.connect()
            mock_pyodbc.assert_called_once()

    def test_connect_passes_timeout_to_pyodbc(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.connect()
        call_kwargs = mock_pyodbc.call_args[1]
        assert call_kwargs['timeout'] == sample_config.connection_timeout

    def test_connect_raises_on_pyodbc_error(self, sample_config):
        with patch('pyodbc.connect') as mock_connect:
            mock_connect.side_effect = pyodbc.Error("Connection failed")
            db = DatabaseManager(sample_config, use_pool=False)
            with pytest.raises(pyodbc.Error):
                db.connect()

    def test_connect_emits_deprecation_warning_when_pooled(self, mock_pyodbc, sample_config):
        """When pooling is enabled, connect() should emit deprecation warning."""
        db = DatabaseManager(sample_config, use_pool=True)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            db.connect()
            db.close()
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()


class TestDatabaseManagerIsConnected:
    """Tests for DatabaseManager._is_connected() method."""

    def test_is_connected_false_when_none(self, sample_config):
        db = DatabaseManager(sample_config)
        assert db._is_connected() is False

    def test_is_connected_true_when_valid(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config)
        db.connect()
        # Mock the execute to succeed
        db._connection.execute = MagicMock(return_value=True)
        assert db._is_connected() is True

    def test_is_connected_false_on_pyodbc_error(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config)
        db.connect()
        # Mock execute to raise error
        db._connection.execute = MagicMock(side_effect=pyodbc.Error("Timeout"))
        assert db._is_connected() is False

    def test_is_connected_false_on_attribute_error(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config)
        db._connection = MagicMock()
        db._connection.execute = MagicMock(side_effect=AttributeError())
        assert db._is_connected() is False


class TestDatabaseManagerGetCursor:
    """Tests for DatabaseManager.get_cursor() context manager."""

    def test_get_cursor_returns_cursor(self, mock_pyodbc, mock_connection, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        with db.get_cursor() as cursor:
            assert cursor is not None

    def test_get_cursor_closes_cursor_on_exit(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        with db.get_cursor() as cursor:
            pass
        mock_cursor.close.assert_called_once()

    def test_get_cursor_closes_cursor_on_exception(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(ValueError):
            with db.get_cursor() as cursor:
                raise ValueError("Test error")
        mock_cursor.close.assert_called_once()

    def test_get_cursor_reraises_pyodbc_error(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        mock_cursor.execute.side_effect = pyodbc.Error("Query failed")
        with pytest.raises(pyodbc.Error):
            with db.get_cursor() as cursor:
                cursor.execute("SELECT 1")

    def test_get_cursor_connects_if_needed(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        assert db._connection is None
        with db.get_cursor():
            pass
        assert db._connection is not None

    def test_get_cursor_uses_existing_connection(self, mock_pyodbc, mock_connection, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.connect()
        mock_pyodbc.reset_mock()
        with db.get_cursor():
            pass
        # Should reuse existing connection
        mock_pyodbc.assert_not_called()

    def test_get_cursor_with_pool_acquires_and_releases(self, mock_pyodbc, mock_cursor, sample_config):
        """With pooling, get_cursor should acquire from pool and release after."""
        db = DatabaseManager(sample_config, use_pool=True)
        with db.get_cursor() as cursor:
            assert cursor is not None
            # Pool should have 0 available (connection in use)
            assert db._pool.available == 0
        # After context, connection should be back in pool
        assert db._pool.available == 1
        db.close()


class TestDatabaseManagerExecuteQuery:
    """Tests for DatabaseManager.execute_query() method."""

    def test_execute_query_returns_dict_list(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        results = db.execute_query("SELECT * FROM test")
        assert isinstance(results, list)
        assert all(isinstance(r, dict) for r in results)

    def test_execute_query_uses_column_names(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        results = db.execute_query("SELECT * FROM test")
        # Based on mock_cursor fixture: id, name, value
        assert "id" in results[0]
        assert "name" in results[0]
        assert "value" in results[0]

    def test_execute_query_returns_all_rows(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        results = db.execute_query("SELECT * FROM test")
        assert len(results) == 3

    def test_execute_query_with_params(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.execute_query("SELECT * FROM test WHERE id = ?", (1,))
        mock_cursor.execute.assert_called_with("SELECT * FROM test WHERE id = ?", (1,))

    def test_execute_query_without_params(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.execute_query("SELECT * FROM test")
        mock_cursor.execute.assert_called_with("SELECT * FROM test")

    def test_execute_query_empty_results(self, mock_pyodbc, mock_cursor_empty, mock_connection, sample_config):
        mock_connection.cursor.return_value = mock_cursor_empty
        with patch('pyodbc.connect', return_value=mock_connection):
            db = DatabaseManager(sample_config, use_pool=False)
            results = db.execute_query("SELECT * FROM empty_table")
            assert results == []


class TestDatabaseManagerExecuteStatement:
    """Tests for DatabaseManager.execute_statement() method."""

    def test_execute_statement_returns_rowcount(self, mock_pyodbc, mock_cursor, sample_config):
        mock_cursor.rowcount = 5
        db = DatabaseManager(sample_config, use_pool=False)
        affected = db.execute_statement("UPDATE test SET x=1")
        assert affected == 5

    def test_execute_statement_commits(self, mock_pyodbc, mock_connection, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.execute_statement("UPDATE test SET x=1")
        mock_connection.commit.assert_called_once()

    def test_execute_statement_with_params(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.execute_statement("UPDATE test SET x=? WHERE id=?", (1, 2))
        mock_cursor.execute.assert_called_with("UPDATE test SET x=? WHERE id=?", (1, 2))

    def test_execute_statement_without_params(self, mock_pyodbc, mock_cursor, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.execute_statement("DELETE FROM test")
        mock_cursor.execute.assert_called_with("DELETE FROM test")


class TestExecuteStatementRollback:
    """Tests for rollback behavior in execute_statement()."""

    def test_execute_statement_rollback_on_error_pooled(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        """Pooled path: rollback is called when execute raises."""
        mock_cursor.execute.side_effect = pyodbc.Error("Execute failed")
        db = DatabaseManager(sample_config, use_pool=True)
        with pytest.raises(pyodbc.Error):
            db.execute_statement("UPDATE test SET x=1")
        # Called at least once in error handler (also called by pool.release)
        assert mock_connection.rollback.call_count >= 1
        db.close()

    def test_execute_statement_rollback_on_error_non_pooled(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        """Non-pooled path: rollback is called when execute raises."""
        mock_cursor.execute.side_effect = pyodbc.Error("Execute failed")
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(pyodbc.Error):
            db.execute_statement("UPDATE test SET x=1")
        mock_connection.rollback.assert_called_once()

    def test_execute_statement_no_rollback_on_success(self, mock_pyodbc, mock_connection, sample_config):
        """Rollback should not be called on successful execution."""
        db = DatabaseManager(sample_config, use_pool=False)
        db.execute_statement("UPDATE test SET x=1")
        mock_connection.rollback.assert_not_called()
        mock_connection.commit.assert_called_once()

    def test_execute_statement_rollback_failure_still_raises_original(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        """If rollback itself fails, the original error should still be raised."""
        mock_cursor.execute.side_effect = pyodbc.Error("Execute failed")
        mock_connection.rollback.side_effect = pyodbc.Error("Rollback failed")
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(pyodbc.Error, match="Execute failed"):
            db.execute_statement("UPDATE test SET x=1")


class TestGetCursorRollback:
    """Tests for rollback behavior in get_cursor()."""

    def test_get_cursor_rollback_on_pyodbc_error(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        """Rollback is called when a pyodbc error occurs inside get_cursor."""
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(pyodbc.Error):
            with db.get_cursor() as cursor:
                raise pyodbc.Error("Query failed")
        mock_connection.rollback.assert_called_once()

    def test_get_cursor_rollback_on_generic_exception(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        """Rollback is called when a non-pyodbc exception occurs inside get_cursor."""
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(ValueError):
            with db.get_cursor() as cursor:
                raise ValueError("Application error")
        mock_connection.rollback.assert_called_once()

    def test_get_cursor_no_rollback_on_success(self, mock_pyodbc, mock_connection, sample_config):
        """Rollback should not be called on successful cursor usage."""
        db = DatabaseManager(sample_config, use_pool=False)
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1")
        mock_connection.rollback.assert_not_called()

    def test_get_cursor_rollback_on_error_pooled(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        """Pooled path: rollback is called when exception occurs inside get_cursor."""
        db = DatabaseManager(sample_config, use_pool=True)
        with pytest.raises(ValueError):
            with db.get_cursor() as cursor:
                raise ValueError("Application error")
        mock_connection.rollback.assert_called()
        db.close()

    def test_get_cursor_rollback_failure_still_raises_original(self, mock_pyodbc, mock_cursor, mock_connection, sample_config):
        """If rollback fails, original error should still propagate."""
        mock_connection.rollback.side_effect = pyodbc.Error("Rollback failed")
        db = DatabaseManager(sample_config, use_pool=False)
        with pytest.raises(ValueError, match="App error"):
            with db.get_cursor() as cursor:
                raise ValueError("App error")


class TestDatabaseManagerClose:
    """Tests for DatabaseManager.close() method."""

    def test_close_closes_connection(self, mock_pyodbc, mock_connection, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.connect()
        db.close()
        mock_connection.close.assert_called_once()

    def test_close_sets_connection_none(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        db.connect()
        db.close()
        assert db._connection is None

    def test_close_without_connection_is_safe(self, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        # Should not raise
        db.close()
        assert db._connection is None

    def test_close_closes_pool(self, mock_pyodbc, sample_config):
        """When pooling is enabled, close() should close the pool."""
        db = DatabaseManager(sample_config, use_pool=True)
        # Initialize the pool
        with db.get_cursor():
            pass
        assert db._pool is not None
        db.close()
        assert db._pool is None


class TestDatabaseManagerPoolStats:
    """Tests for DatabaseManager.pool_stats property."""

    def test_pool_stats_returns_none_when_not_pooled(self, sample_config):
        db = DatabaseManager(sample_config, use_pool=False)
        assert db.pool_stats is None

    def test_pool_stats_returns_dict_when_pooled(self, mock_pyodbc, sample_config):
        db = DatabaseManager(sample_config, use_pool=True)
        # Initialize pool
        with db.get_cursor():
            pass
        stats = db.pool_stats
        assert stats is not None
        # Check for the new stats fields
        assert "total_connections" in stats
        assert "available" in stats
        assert "in_use" in stats
        assert "total_acquisitions" in stats
        assert "total_releases" in stats
        db.close()
