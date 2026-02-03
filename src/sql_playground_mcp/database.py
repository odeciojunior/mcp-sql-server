"""Database connection management."""

import logging
import warnings
from contextlib import contextmanager
from typing import Any, Generator

import pyodbc

from .config import DatabaseConfig, PoolConfig
from .pool import ConnectionPool

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database connections with optional pooling support."""

    def __init__(
        self,
        config: DatabaseConfig,
        pool_config: PoolConfig | None = None,
        use_pool: bool = True,
    ):
        """Initialize the database manager.

        Args:
            config: Database configuration
            pool_config: Pool configuration (optional, uses defaults if use_pool=True)
            use_pool: Whether to use connection pooling (default: True)
        """
        self.config = config
        self._pool_config = pool_config
        self._use_pool = use_pool
        self._pool: ConnectionPool | None = None
        self._connection: pyodbc.Connection | None = None

    def _get_pool(self) -> ConnectionPool:
        """Lazy initialization of connection pool."""
        if self._pool is None:
            pool_config = self._pool_config or PoolConfig()
            self._pool = ConnectionPool(self.config, pool_config)
            logger.info(
                f"Connection pool initialized (min={pool_config.min_size}, max={pool_config.max_size})"
            )
        return self._pool

    def connect(self) -> pyodbc.Connection:
        """Establish database connection.

        DEPRECATED: When pooling is enabled (the default), this method emits a
        deprecation warning. Use get_cursor() context manager instead for proper
        connection lifecycle management with automatic cleanup and pool release.

        When pooling is disabled:
            Creates a new connection or returns an existing valid connection.

        When pooling is enabled:
            Acquires a connection from the pool. The caller is responsible for
            calling close() or using get_cursor() instead to ensure the connection
            is properly released back to the pool.

        Returns:
            A pyodbc.Connection object.

        Example:
            # Preferred approach (works with both pooling modes):
            with db.get_cursor() as cursor:
                cursor.execute("SELECT 1")

            # Legacy approach (deprecated with pooling):
            conn = db.connect()  # Emits DeprecationWarning if pooling enabled
        """
        if self._use_pool:
            warnings.warn(
                "connect() is deprecated when pooling is enabled. Use get_cursor() instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Still provide a connection for backward compat
            pool = self._get_pool()
            pooled_conn = pool.acquire()
            # Store reference so release can happen later
            self._connection = pooled_conn.connection
            self._current_pooled_conn = pooled_conn
            return self._connection

        if self._connection is None or not self._is_connected():
            self._connection = pyodbc.connect(
                self.config.get_connection_string(),
                timeout=self.config.connection_timeout,
            )
            self._connection.timeout = self.config.query_timeout
        return self._connection

    def _is_connected(self) -> bool:
        """Check if connection is still valid."""
        if self._connection is None:
            return False
        try:
            self._connection.execute("SELECT 1")
            return True
        except (pyodbc.Error, AttributeError):
            return False

    @contextmanager
    def get_cursor(self) -> Generator[pyodbc.Cursor, None, None]:
        """Context manager for cursor with automatic cleanup.

        When pooling is enabled, acquires a connection from the pool and
        releases it after the cursor is closed.
        """
        if self._use_pool:
            pool = self._get_pool()
            with pool.connection() as pooled_conn:
                cursor = pooled_conn.connection.cursor()
                try:
                    yield cursor
                except Exception as e:
                    try:
                        pooled_conn.connection.rollback()
                    except Exception:
                        logger.debug("Rollback failed during error handling")
                    if isinstance(e, pyodbc.Error):
                        logger.error(f"Database error: {e}")
                    raise
                finally:
                    cursor.close()
        else:
            conn = self.connect()
            cursor = conn.cursor()
            try:
                yield cursor
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    logger.debug("Rollback failed during error handling")
                if isinstance(e, pyodbc.Error):
                    logger.error(f"Database error: {e}")
                raise
            finally:
                cursor.close()

    def execute_query(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        """Execute a query and return results as list of dicts."""
        with self.get_cursor() as cursor:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)

            if cursor.description is None:
                return []

            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def execute_statement(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        """Execute a modification statement and return affected row count."""
        if self._use_pool:
            pool = self._get_pool()
            with pool.connection() as pooled_conn:
                cursor = pooled_conn.connection.cursor()
                try:
                    if params:
                        cursor.execute(sql, params)
                    else:
                        cursor.execute(sql)
                    affected: int = cursor.rowcount
                    pooled_conn.connection.commit()
                    return affected
                except Exception as e:
                    try:
                        pooled_conn.connection.rollback()
                    except Exception:
                        logger.debug("Rollback failed during error handling")
                    if isinstance(e, pyodbc.Error):
                        logger.error(f"Database error: {e}")
                    raise
                finally:
                    cursor.close()
        else:
            conn = self.connect()
            cursor = conn.cursor()
            try:
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)
                affected_rows: int = cursor.rowcount
                conn.commit()
                return affected_rows
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    logger.debug("Rollback failed during error handling")
                if isinstance(e, pyodbc.Error):
                    logger.error(f"Database error: {e}")
                raise
            finally:
                cursor.close()

    def close(self) -> None:
        """Close database connection(s) and pool."""
        if self._pool:
            self._pool.close()
            self._pool = None
        if self._connection:
            self._connection.close()
            self._connection = None

    @property
    def pool_stats(self) -> dict[str, Any] | None:
        """Get connection pool statistics, if pooling is enabled."""
        if self._pool:
            return self._pool.stats()
        return None
