"""Database connection management."""

import logging
from contextlib import contextmanager
from typing import Any, Generator

import pyodbc

from .config import DatabaseConfig, PoolConfig
from .errors import sanitize_error
from .pool import ConnectionPool

logger = logging.getLogger(__name__)


class SessionStateError(RuntimeError):
    """A connection's session state could not be restored after a query.

    The connection must not be reused: pooled connections are retired and a
    non-pooled connection is closed.
    """


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
        """Return the non-pooled connection, opening or reopening it as needed.

        Only valid when pooling is disabled. With pooling, use get_cursor(),
        which acquires and releases pool connections safely.

        Not thread-safe: it mutates `_connection` without a lock. Non-pooled
        mode is not used on any tool path.

        Raises:
            RuntimeError: if pooling is enabled.
        """
        if self._use_pool:
            raise RuntimeError(
                "connect() is not supported when pooling is enabled; use get_cursor()"
            )

        if self._connection is None or not self._is_connected():
            self._drop_connection()
            self._connection = pyodbc.connect(
                self.config.get_connection_string(),
                timeout=self.config.connection_timeout,
            )
            self._connection.timeout = self.config.query_timeout
        return self._connection

    def _is_connected(self) -> bool:
        """Check if the connection is still valid (rolls back the probe)."""
        if self._connection is None:
            return False
        try:
            self._connection.execute("SELECT 1")
            self._connection.rollback()
            return True
        except (pyodbc.Error, AttributeError):
            return False

    @contextmanager
    def get_cursor(self) -> Generator[pyodbc.Cursor, None, None]:
        """Context manager for cursor with automatic cleanup.

        When pooling is enabled, acquires a connection from the pool and
        releases it after the cursor is closed. On error the cursor is closed
        before rolling back: a pending result set would make rollback fail
        with "Connection is busy with results for another command".
        """
        if self._use_pool:
            pool = self._get_pool()
            with pool.connection() as pooled_conn:
                cursor = pooled_conn.connection.cursor()
                closed = False
                try:
                    yield cursor
                except Exception as e:
                    self._close_cursor(cursor)
                    closed = True
                    if isinstance(e, SessionStateError):
                        pooled_conn.invalid = True
                    else:
                        try:
                            pooled_conn.connection.rollback()
                        except Exception:
                            logger.debug("Rollback failed during error handling")
                    if isinstance(e, pyodbc.Error):
                        logger.error(f"Database error: {sanitize_error(e)}")
                    raise
                finally:
                    if not closed:
                        cursor.close()
        else:
            conn = self.connect()
            cursor = conn.cursor()
            closed = False
            try:
                yield cursor
            except Exception as e:
                self._close_cursor(cursor)
                closed = True
                if isinstance(e, SessionStateError):
                    self._drop_connection()
                else:
                    try:
                        conn.rollback()
                    except Exception:
                        logger.debug("Rollback failed during error handling")
                if isinstance(e, pyodbc.Error):
                    logger.error(f"Database error: {sanitize_error(e)}")
                raise
            finally:
                if not closed:
                    cursor.close()

    @staticmethod
    def _close_cursor(cursor: pyodbc.Cursor) -> None:
        try:
            cursor.close()
        except Exception:
            logger.debug("Cursor close failed during error handling")

    def _drop_connection(self) -> None:
        """Close and forget the non-pooled connection."""
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                logger.debug("Connection close failed")
            self._connection = None

    def execute_query(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
        max_rows: int | None = None,
        server_limit: bool = False,
    ) -> list[dict[str, Any]]:
        """Execute a query and return results as list of dicts.

        Args:
            sql: SQL to execute, unmodified.
            params: Positional parameters for ``?`` placeholders.
            max_rows: Read at most this many rows (``fetchmany``).
            server_limit: Also cap rows on the server with ``SET ROWCOUNT``.
                Requires ``max_rows``. Do not use for stored procedures:
                ROWCOUNT would also limit DML inside them.

        Raises:
            SessionStateError: the session could not be reset afterwards, or
                the session's database changed; the connection is retired.
        """
        if server_limit and max_rows is None:
            raise ValueError("server_limit requires max_rows")
        if max_rows is not None and max_rows < 1:
            raise ValueError("max_rows must be at least 1")

        with self.get_cursor() as cursor:
            expected_db: str | None = None
            if server_limit:
                # Read the pre-query database name on the same cursor/connection
                # so the post-query check is case- and collation-consistent,
                # rather than comparing against the configured name (DB_NAME()
                # returns the name as stored in sys.databases, which may differ
                # in case from a case-insensitively matched DATABASE= setting).
                # No session state has changed yet, so let failures here
                # propagate as ordinary pyodbc.Error.
                cursor.execute("SELECT DB_NAME()")
                row = cursor.fetchone()
                expected_db = row[0] if row is not None else None
                # Literal int, not a parameter: a parameterized SET runs inside
                # sp_executesql and reverts when that call returns.
                cursor.execute(f"SET ROWCOUNT {int(max_rows or 0)}")
            try:
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)

                if cursor.description is None:
                    return []

                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchmany(max_rows) if max_rows is not None else cursor.fetchall()
                return [dict(zip(columns, row)) for row in rows]
            finally:
                if server_limit:
                    self._reset_session(cursor, expected_db)

    def _reset_session(self, cursor: pyodbc.Cursor, expected_db: str | None) -> None:
        """Undo SET ROWCOUNT and confirm the session is still on the same database."""
        try:
            cursor.cancel()
            cursor.execute("SET ROWCOUNT 0")
            cursor.execute("SELECT DB_NAME()")
            row = cursor.fetchone()
        except pyodbc.Error as e:
            raise SessionStateError("could not reset session state") from e
        if row is None or row[0] != expected_db:
            raise SessionStateError("session database changed")

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
                        logger.error(f"Database error: {sanitize_error(e)}")
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
                    logger.error(f"Database error: {sanitize_error(e)}")
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
