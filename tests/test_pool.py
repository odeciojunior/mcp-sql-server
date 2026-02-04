"""Tests for connection pool."""

import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import pyodbc

from mcp_sql_server.config import DatabaseConfig, PoolConfig
from mcp_sql_server.pool import ConnectionPool, PooledConnection


@pytest.fixture
def pool_config() -> PoolConfig:
    """Create a test pool configuration."""
    return PoolConfig(
        min_size=1,
        max_size=3,
        idle_timeout=60,
        health_check_interval=30,
        acquire_timeout=5.0,
        max_lifetime=300,
    )


@pytest.fixture
def db_config() -> DatabaseConfig:
    """Create a test database configuration."""
    return DatabaseConfig(
        host="localhost",
        port=1433,
        user="test",
        password="test",
        database="test",
    )


class TestPooledConnection:
    """Tests for PooledConnection dataclass."""

    def test_is_stale_within_lifetime(self):
        conn = PooledConnection(connection=MagicMock())
        assert conn.is_stale(max_lifetime=300) is False

    def test_is_stale_exceeded_lifetime(self):
        conn = PooledConnection(connection=MagicMock())
        conn.created_at = time.time() - 400  # 400 seconds ago
        assert conn.is_stale(max_lifetime=300) is True

    def test_is_stale_zero_lifetime_disabled(self):
        conn = PooledConnection(connection=MagicMock())
        conn.created_at = time.time() - 10000
        assert conn.is_stale(max_lifetime=0) is False

    def test_is_idle_recently_used(self):
        conn = PooledConnection(connection=MagicMock())
        assert conn.is_idle(idle_timeout=60) is False

    def test_is_idle_exceeded_timeout(self):
        conn = PooledConnection(connection=MagicMock())
        conn.last_used_at = time.time() - 100
        assert conn.is_idle(idle_timeout=60) is True

    def test_is_idle_zero_timeout_disabled(self):
        conn = PooledConnection(connection=MagicMock())
        conn.last_used_at = time.time() - 10000
        assert conn.is_idle(idle_timeout=0) is False

    def test_needs_health_check_recently_checked(self):
        conn = PooledConnection(connection=MagicMock())
        assert conn.needs_health_check(interval=30) is False

    def test_needs_health_check_exceeded_interval(self):
        conn = PooledConnection(connection=MagicMock())
        conn.last_health_check = time.time() - 60
        assert conn.needs_health_check(interval=30) is True

    def test_needs_health_check_zero_interval_disabled(self):
        conn = PooledConnection(connection=MagicMock())
        conn.last_health_check = time.time() - 10000
        assert conn.needs_health_check(interval=0) is False

    def test_mark_used_updates_timestamp(self):
        conn = PooledConnection(connection=MagicMock())
        old_time = conn.last_used_at
        old_count = conn.use_count
        time.sleep(0.01)
        conn.mark_used()
        assert conn.last_used_at > old_time
        assert conn.use_count == old_count + 1

    def test_mark_health_checked_updates_timestamp(self):
        conn = PooledConnection(connection=MagicMock())
        old_time = conn.last_health_check
        time.sleep(0.01)
        conn.mark_health_checked()
        assert conn.last_health_check > old_time


class TestConnectionPool:
    """Tests for ConnectionPool class."""

    def test_pool_initializes_min_connections(self, db_config, pool_config):
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)

            # min_size=1, so 1 connection should be created
            assert mock_connect.call_count == 1
            assert pool.size == 1
            pool.close()

    def test_pool_acquire_returns_connection(self, db_config, pool_config):
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            pooled_conn = pool.acquire()

            assert pooled_conn is not None
            assert pooled_conn.connection is mock_conn
            pool.release(pooled_conn)
            pool.close()

    def test_pool_release_returns_to_pool(self, db_config, pool_config):
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            pooled_conn = pool.acquire()
            assert pool.available == 0

            pool.release(pooled_conn)
            assert pool.available == 1
            pool.close()

    def test_pool_context_manager(self, db_config, pool_config):
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)

            with pool.connection() as pooled_conn:
                assert pooled_conn is not None
                assert pool.available == 0

            # After context, connection should be returned
            assert pool.available == 1
            pool.close()

    def test_pool_creates_new_when_empty(self, db_config, pool_config):
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            conn1 = pool.acquire()
            conn2 = pool.acquire()  # Should create new

            assert mock_connect.call_count == 2
            assert pool.size == 2
            pool.release(conn1)
            pool.release(conn2)
            pool.close()

    def test_pool_respects_max_size(self, db_config):
        pool_config = PoolConfig(min_size=1, max_size=2, acquire_timeout=0.5)

        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            conn1 = pool.acquire()
            conn2 = pool.acquire()

            # Third acquire should timeout
            with pytest.raises(TimeoutError):
                pool.acquire()

            pool.release(conn1)
            pool.release(conn2)
            pool.close()

    def test_pool_retires_stale_connections(self, db_config):
        pool_config = PoolConfig(
            min_size=1, max_size=3, max_lifetime=1, acquire_timeout=2.0
        )

        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            conn1 = pool.acquire()
            pool.release(conn1)

            # Wait for connection to become stale
            time.sleep(1.5)

            # Should get a new connection, not the stale one
            conn2 = pool.acquire()
            assert conn2 is not conn1
            pool.release(conn2)
            pool.close()

    def test_pool_health_check_on_acquire(self, db_config):
        pool_config = PoolConfig(
            min_size=1, max_size=3, health_check_interval=1, acquire_timeout=2.0
        )

        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            conn1 = pool.acquire()
            pool.release(conn1)

            # Wait for health check to be needed
            time.sleep(1.5)

            # Health check should pass
            conn2 = pool.acquire()
            assert mock_conn.execute.called
            pool.release(conn2)
            pool.close()

    def test_pool_closes_unhealthy_connections(self, db_config):
        pool_config = PoolConfig(
            min_size=1, max_size=3, health_check_interval=1, acquire_timeout=2.0
        )

        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            conn1 = pool.acquire()
            pool.release(conn1)

            # Wait for health check
            time.sleep(1.5)

            # Make health check fail
            mock_conn.execute.side_effect = pyodbc.Error("Connection lost")

            # Should create new connection after failed health check
            conn2 = pool.acquire()
            assert mock_connect.call_count >= 2
            pool.release(conn2)
            pool.close()

    def test_pool_stats(self, db_config, pool_config):
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            stats = pool.stats()

            # Check all expected statistics fields
            assert "total_connections" in stats
            assert "pool_size" in stats
            assert "in_use" in stats
            assert "available" in stats
            assert "peak_usage" in stats
            assert "total_acquisitions" in stats
            assert "total_releases" in stats
            assert "failed_acquisitions" in stats
            assert "health_checks" in stats
            assert "max_size" in stats
            assert "min_size" in stats
            assert "closed" in stats

            assert stats["max_size"] == 3
            assert stats["closed"] is False
            pool.close()
            assert pool.stats()["closed"] is True

    def test_pool_stats_tracking_acquisitions(self, db_config, pool_config):
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)

            # Acquire and release connections
            conn1 = pool.acquire()
            stats = pool.stats()
            assert stats["total_acquisitions"] == 1
            assert stats["in_use"] == 1
            assert stats["peak_usage"] == 1

            conn2 = pool.acquire()
            stats = pool.stats()
            assert stats["total_acquisitions"] == 2
            assert stats["in_use"] == 2
            assert stats["peak_usage"] == 2

            pool.release(conn1)
            stats = pool.stats()
            assert stats["total_releases"] == 1
            assert stats["in_use"] == 1
            assert stats["peak_usage"] == 2  # Peak should remain

            pool.release(conn2)
            stats = pool.stats()
            assert stats["total_releases"] == 2
            assert stats["in_use"] == 0
            assert stats["peak_usage"] == 2

            pool.close()

    def test_pool_stats_failed_acquisitions(self, db_config):
        pool_config = PoolConfig(min_size=1, max_size=1, acquire_timeout=0.5)

        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            conn1 = pool.acquire()

            # Second acquire should fail (pool exhausted)
            with pytest.raises(TimeoutError):
                pool.acquire()

            stats = pool.stats()
            assert stats["failed_acquisitions"] == 1

            pool.release(conn1)
            pool.close()

    def test_pool_stats_health_checks(self, db_config):
        pool_config = PoolConfig(
            min_size=1, max_size=3, health_check_interval=0, acquire_timeout=2.0
        )

        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            conn1 = pool.acquire()
            conn1._pooled_conn_last_health_check = 0  # Force health check needed

            pool.release(conn1)

            # Since health_check_interval=0, health checks are disabled
            # Let's test with interval > 0
            pool.close()

        pool_config2 = PoolConfig(
            min_size=1, max_size=3, health_check_interval=1, acquire_timeout=2.0
        )

        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config2)
            conn1 = pool.acquire()
            pool.release(conn1)

            # Wait for health check interval
            time.sleep(1.5)

            # This acquire should trigger a health check
            conn2 = pool.acquire()
            stats = pool.stats()
            assert stats["health_checks"] >= 1

            pool.release(conn2)
            pool.close()

    def test_pool_close(self, db_config, pool_config):
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            pool.acquire()
            pool.close()

            assert pool.stats()["closed"] is True
            with pytest.raises(RuntimeError):
                pool.acquire()

    def test_pool_concurrent_access(self, db_config):
        pool_config = PoolConfig(min_size=2, max_size=5, acquire_timeout=5.0)

        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            results = []
            errors = []

            def worker():
                try:
                    with pool.connection() as conn:
                        results.append(conn)
                        time.sleep(0.1)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0
            assert len(results) == 5
            pool.close()


class TestConnectionPoolTransactionReset:
    """Tests for transaction reset on connection release."""

    def test_release_calls_rollback(self, db_config, pool_config):
        """Verify rollback is called when releasing a connection."""
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            pooled_conn = pool.acquire()
            pool.release(pooled_conn)

            mock_conn.rollback.assert_called()
            pool.close()

    def test_release_discards_on_rollback_failure(self, db_config, pool_config):
        """Verify connection is discarded if rollback fails during release."""
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.rollback.side_effect = pyodbc.Error("Rollback failed")
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            pooled_conn = pool.acquire()

            pool.release(pooled_conn)

            # Connection should be closed/discarded, not returned to pool
            assert pool.available == 0
            mock_conn.close.assert_called()
            pool.close()

    def test_release_tracks_transaction_resets(self, db_config, pool_config):
        """Verify transaction_resets counter increments on release."""
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            conn1 = pool.acquire()
            pool.release(conn1)

            conn2 = pool.acquire()
            pool.release(conn2)

            stats = pool.stats()
            assert stats["transaction_resets"] == 2
            pool.close()

    def test_health_check_includes_rollback(self, db_config):
        """Verify rollback is called as part of health check."""
        pool_config = PoolConfig(
            min_size=1, max_size=3, health_check_interval=1, acquire_timeout=2.0
        )

        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            conn1 = pool.acquire()
            pool.release(conn1)

            # Wait for health check interval to elapse
            time.sleep(1.5)

            # Acquire should trigger health check which includes rollback
            conn2 = pool.acquire()
            # rollback called during release + health check
            assert mock_conn.rollback.call_count >= 2
            pool.release(conn2)
            pool.close()

    def test_stats_includes_transaction_resets(self, db_config, pool_config):
        """Verify transaction_resets appears in stats output."""
        with patch("pyodbc.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn

            pool = ConnectionPool(db_config, pool_config)
            stats = pool.stats()
            assert "transaction_resets" in stats
            assert stats["transaction_resets"] == 0
            pool.close()


class TestPoolConfig:
    """Tests for PoolConfig class."""

    def test_default_values(self):
        config = PoolConfig()
        assert config.min_size == 1
        assert config.max_size == 5
        assert config.idle_timeout == 300
        assert config.health_check_interval == 30
        assert config.acquire_timeout == 10.0
        assert config.max_lifetime == 3600

    def test_min_size_validation(self):
        with pytest.raises(ValueError):
            PoolConfig(min_size=0)

    def test_max_size_validation(self):
        with pytest.raises(ValueError):
            PoolConfig(max_size=0)

    def test_min_greater_than_max_validation(self):
        with pytest.raises(ValueError):
            PoolConfig(min_size=10, max_size=5)

    def test_from_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DB_POOL_MIN_SIZE=2\n"
            "DB_POOL_MAX_SIZE=10\n"
            "DB_POOL_IDLE_TIMEOUT=600\n"
        )

        with patch.dict("os.environ", {}, clear=True):
            config = PoolConfig.from_env(env_path=env_file)
            assert config.min_size == 2
            assert config.max_size == 10
            assert config.idle_timeout == 600
