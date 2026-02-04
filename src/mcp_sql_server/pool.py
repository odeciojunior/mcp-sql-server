"""Thread-safe connection pool for database connections."""

import logging
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator

import pyodbc

from .config import DatabaseConfig, PoolConfig

logger = logging.getLogger(__name__)


@dataclass
class PooledConnection:
    """A connection wrapper with metadata for pool management."""

    connection: pyodbc.Connection
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    last_health_check: float = field(default_factory=time.time)
    use_count: int = 0

    def is_stale(self, max_lifetime: int) -> bool:
        """Check if connection has exceeded its maximum lifetime."""
        if max_lifetime <= 0:
            return False
        return (time.time() - self.created_at) > max_lifetime

    def is_idle(self, idle_timeout: int) -> bool:
        """Check if connection has been idle too long."""
        if idle_timeout <= 0:
            return False
        return (time.time() - self.last_used_at) > idle_timeout

    def needs_health_check(self, interval: int) -> bool:
        """Check if connection needs a health check."""
        if interval <= 0:
            return False
        return (time.time() - self.last_health_check) > interval

    def mark_used(self) -> None:
        """Update usage timestamp and increment counter."""
        self.last_used_at = time.time()
        self.use_count += 1

    def mark_health_checked(self) -> None:
        """Update health check timestamp."""
        self.last_health_check = time.time()


class ConnectionPool:
    """Thread-safe connection pool using Queue for synchronization."""

    def __init__(self, db_config: DatabaseConfig, pool_config: PoolConfig | None = None):
        """Initialize the connection pool.

        Args:
            db_config: Database configuration for creating connections
            pool_config: Pool configuration (uses defaults if not provided)
        """
        self._db_config = db_config
        self._pool_config = pool_config or PoolConfig()
        self._pool: queue.Queue[PooledConnection] = queue.Queue(
            maxsize=self._pool_config.max_size
        )
        self._lock = threading.Lock()
        self._created_count = 0
        self._closed = False

        # Tracking metrics
        self._total_acquisitions = 0
        self._total_releases = 0
        self._failed_acquisitions = 0
        self._health_check_count = 0
        self._transaction_resets = 0
        self._in_use = 0
        self._peak_usage = 0

        # Pre-create minimum connections
        self._initialize_pool()

    def _initialize_pool(self) -> None:
        """Create the minimum number of connections."""
        for _ in range(self._pool_config.min_size):
            try:
                conn = self._create_connection()
                self._pool.put_nowait(conn)
            except Exception as e:
                logger.warning(f"Failed to pre-create connection: {e}")

    def _create_connection(self) -> PooledConnection:
        """Create a new database connection."""
        with self._lock:
            if self._created_count >= self._pool_config.max_size:
                raise RuntimeError("Pool has reached maximum size")
            self._created_count += 1

        try:
            conn = pyodbc.connect(
                self._db_config.get_connection_string(),
                timeout=self._db_config.connection_timeout,
            )
            conn.timeout = self._db_config.query_timeout
            logger.debug(f"Created new connection (total: {self._created_count})")
            return PooledConnection(connection=conn)
        except Exception:
            with self._lock:
                self._created_count -= 1
            raise

    def _is_connection_healthy(self, pooled_conn: PooledConnection) -> bool:
        """Check if a connection is still usable."""
        with self._lock:
            self._health_check_count += 1
        try:
            pooled_conn.connection.execute("SELECT 1")
            pooled_conn.connection.rollback()
            pooled_conn.mark_health_checked()
            return True
        except (pyodbc.Error, AttributeError):
            return False

    def _reset_connection(self, pooled_conn: PooledConnection) -> bool:
        """Reset connection state by rolling back any pending transaction."""
        try:
            pooled_conn.connection.rollback()
            with self._lock:
                self._transaction_resets += 1
            return True
        except (pyodbc.Error, AttributeError) as e:
            logger.debug(f"Failed to reset connection state: {e}")
            return False

    def _close_connection(self, pooled_conn: PooledConnection) -> None:
        """Close a connection and decrement counter."""
        try:
            pooled_conn.connection.close()
        except Exception as e:
            logger.debug(f"Error closing connection: {e}")
        finally:
            with self._lock:
                self._created_count -= 1
            logger.debug(f"Closed connection (remaining: {self._created_count})")

    def _track_acquisition(self) -> None:
        """Track a successful acquisition."""
        with self._lock:
            self._total_acquisitions += 1
            self._in_use += 1
            if self._in_use > self._peak_usage:
                self._peak_usage = self._in_use

    def acquire(self) -> PooledConnection:
        """Acquire a connection from the pool.

        Returns:
            A healthy pooled connection

        Raises:
            TimeoutError: If no connection available within timeout
            RuntimeError: If pool is closed
        """
        if self._closed:
            raise RuntimeError("Pool is closed")

        deadline = time.time() + self._pool_config.acquire_timeout

        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                with self._lock:
                    self._failed_acquisitions += 1
                raise TimeoutError(
                    f"Could not acquire connection within {self._pool_config.acquire_timeout}s"
                )

            try:
                # Try to get from pool first
                pooled_conn = self._pool.get(timeout=min(remaining, 0.1))

                # Check if connection should be retired
                if pooled_conn.is_stale(self._pool_config.max_lifetime):
                    logger.debug("Retiring stale connection")
                    self._close_connection(pooled_conn)
                    continue

                if pooled_conn.is_idle(self._pool_config.idle_timeout):
                    logger.debug("Retiring idle connection")
                    self._close_connection(pooled_conn)
                    continue

                # Health check if needed
                if pooled_conn.needs_health_check(self._pool_config.health_check_interval):
                    if not self._is_connection_healthy(pooled_conn):
                        logger.debug("Retiring unhealthy connection")
                        self._close_connection(pooled_conn)
                        continue

                pooled_conn.mark_used()
                self._track_acquisition()
                return pooled_conn

            except queue.Empty:
                # No connections available, try to create new one
                with self._lock:
                    can_create = self._created_count < self._pool_config.max_size

                if can_create:
                    try:
                        pooled_conn = self._create_connection()
                        pooled_conn.mark_used()
                        self._track_acquisition()
                        return pooled_conn
                    except Exception as e:
                        logger.warning(f"Failed to create new connection: {e}")
                        with self._lock:
                            self._failed_acquisitions += 1
                        # Continue waiting for available connection
                        continue

    def release(self, pooled_conn: PooledConnection) -> None:
        """Return a connection to the pool.

        Args:
            pooled_conn: The connection to return
        """
        with self._lock:
            self._total_releases += 1
            self._in_use = max(0, self._in_use - 1)

        if self._closed:
            self._close_connection(pooled_conn)
            return

        # Check if connection should be retired
        if pooled_conn.is_stale(self._pool_config.max_lifetime):
            self._close_connection(pooled_conn)
            return

        # Reset transaction state before returning to pool
        if not self._reset_connection(pooled_conn):
            self._close_connection(pooled_conn)
            return

        try:
            self._pool.put_nowait(pooled_conn)
        except queue.Full:
            # Pool is full, close this connection
            self._close_connection(pooled_conn)

    @contextmanager
    def connection(self) -> Generator[PooledConnection, None, None]:
        """Context manager for acquiring and releasing connections.

        Usage:
            with pool.connection() as conn:
                cursor = conn.connection.cursor()
                cursor.execute("SELECT 1")
        """
        pooled_conn = self.acquire()
        try:
            yield pooled_conn
        finally:
            self.release(pooled_conn)

    def close(self) -> None:
        """Close all connections and shutdown the pool."""
        self._closed = True

        # Drain the pool
        while True:
            try:
                pooled_conn = self._pool.get_nowait()
                self._close_connection(pooled_conn)
            except queue.Empty:
                break

        logger.info("Connection pool closed")

    def stats(self) -> dict[str, Any]:
        """Get pool statistics.

        Returns:
            Dictionary with pool statistics
        """
        with self._lock:
            return {
                "total_connections": self._created_count,
                "pool_size": self._pool_config.max_size,
                "in_use": self._in_use,
                "available": self._pool.qsize(),
                "peak_usage": self._peak_usage,
                "total_acquisitions": self._total_acquisitions,
                "total_releases": self._total_releases,
                "failed_acquisitions": self._failed_acquisitions,
                "health_checks": self._health_check_count,
                "transaction_resets": self._transaction_resets,
                "max_size": self._pool_config.max_size,
                "min_size": self._pool_config.min_size,
                "closed": self._closed,
            }

    @property
    def size(self) -> int:
        """Current number of created connections."""
        return self._created_count

    @property
    def available(self) -> int:
        """Number of available connections in pool."""
        return self._pool.qsize()
