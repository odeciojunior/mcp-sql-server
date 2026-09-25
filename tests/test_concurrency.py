"""Concurrency tests for the connection pool and database registry.

Under mcp 1.x, synchronous tool handlers ran inline on the event loop and were
effectively serialised. mcp 2.x runs them in worker threads via
anyio.to_thread.run_sync(), so tool calls can now execute genuinely in
parallel. These tests exercise the paths that concurrency newly reaches.

The tool path is DatabaseManager.get_cursor() -> ConnectionPool.connection(),
which is lock-protected. DatabaseManager.connect() raises when pooling is
enabled, so it is not reachable from tools.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from mcp_sql_server.config import DatabaseConfig, PoolConfig
from mcp_sql_server.database import DatabaseManager
from mcp_sql_server.pool import ConnectionPool
from mcp_sql_server.registry import DatabaseRegistry

WORKERS = 12
ITERATIONS = 8


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(
        host="localhost",
        port=1433,
        user="test",
        password="test",
        database="test",
    )


@pytest.fixture
def pool_config() -> PoolConfig:
    return PoolConfig(min_size=1, max_size=4, acquire_timeout=10.0)


def _run_concurrently(target, workers=WORKERS):
    """Run target() in `workers` threads, all released at once.

    Returns the list of exceptions raised by the workers.
    """
    errors: list[BaseException] = []
    errors_lock = threading.Lock()
    start = threading.Barrier(workers)

    def wrapped() -> None:
        try:
            start.wait(timeout=10)
            target()
        except BaseException as exc:  # noqa: BLE001 - recorded and re-raised by the test
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=wrapped) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "worker thread deadlocked"
    return errors


class TestPoolConcurrentAcquire:
    """ConnectionPool under parallel acquire/release."""

    def test_concurrent_connection_context_manager(self, db_config, pool_config):
        """Parallel connection() use never exceeds max_size or loses a connection."""
        with patch("pyodbc.connect", return_value=MagicMock()):
            pool = ConnectionPool(db_config, pool_config)
            in_flight = 0
            peak = 0
            counter_lock = threading.Lock()

            def worker() -> None:
                nonlocal in_flight, peak
                for _ in range(ITERATIONS):
                    with pool.connection() as conn:
                        assert conn is not None
                        with counter_lock:
                            in_flight += 1
                            peak = max(peak, in_flight)
                        with counter_lock:
                            in_flight -= 1

            errors = _run_concurrently(worker)
            assert errors == []
            assert in_flight == 0, "a connection was never released"
            assert peak <= pool_config.max_size, f"pool exceeded max_size: {peak}"
            pool.close()

    def test_no_connection_handed_to_two_threads_at_once(self, db_config, pool_config):
        """A pooled connection is never checked out twice simultaneously."""
        with patch("pyodbc.connect", side_effect=lambda *a, **kw: MagicMock()):
            pool = ConnectionPool(db_config, pool_config)
            held: set[int] = set()
            held_lock = threading.Lock()
            collisions: list[int] = []

            def worker() -> None:
                for _ in range(ITERATIONS):
                    with pool.connection() as conn:
                        key = id(conn)
                        with held_lock:
                            if key in held:
                                collisions.append(key)
                            held.add(key)
                        with held_lock:
                            held.discard(key)

            errors = _run_concurrently(worker)
            assert errors == []
            assert collisions == [], "same connection checked out concurrently"
            pool.close()

    def test_stats_consistent_after_concurrent_use(self, db_config, pool_config):
        """Acquire/release accounting balances out once threads finish."""
        with patch("pyodbc.connect", return_value=MagicMock()):
            pool = ConnectionPool(db_config, pool_config)

            def worker() -> None:
                for _ in range(ITERATIONS):
                    with pool.connection():
                        pass

            errors = _run_concurrently(worker)
            assert errors == []
            stats = pool.stats()
            assert pool.size <= pool_config.max_size
            assert pool.available == pool.size, f"leaked connection: {stats}"
            pool.close()


class TestDatabaseManagerConcurrentCursors:
    """The actual tool path: DatabaseManager.get_cursor()."""

    def test_concurrent_get_cursor(self, db_config, pool_config):
        """Parallel get_cursor() calls each get a usable cursor and release it."""
        with patch("pyodbc.connect", side_effect=lambda *a, **kw: MagicMock()):
            manager = DatabaseManager(db_config, pool_config)
            seen = []
            seen_lock = threading.Lock()

            def worker() -> None:
                for _ in range(ITERATIONS):
                    with manager.get_cursor() as cursor:
                        assert cursor is not None
                        with seen_lock:
                            seen.append(id(cursor))

            errors = _run_concurrently(worker)
            assert errors == []
            assert len(seen) == WORKERS * ITERATIONS
            manager.close()


class TestDatabaseManagerPoolCreationRace:
    """P1: DatabaseManager._get_pool() must not construct more than one pool
    when several threads race on the first call."""

    def test_concurrent_get_pool_creates_one_pool(self, db_config, pool_config):
        import time

        manager = DatabaseManager(db_config, pool_config)
        created: list[MagicMock] = []
        created_lock = threading.Lock()

        def make_pool(*args, **kwargs):
            time.sleep(0.1)
            pool = MagicMock()
            with created_lock:
                created.append(pool)
            return pool

        results: list[int] = []
        results_lock = threading.Lock()

        def worker() -> None:
            pool = manager._get_pool()
            with results_lock:
                results.append(id(pool))

        with patch("mcp_sql_server.database.ConnectionPool", side_effect=make_pool):
            errors = _run_concurrently(worker, workers=8)

        assert errors == []
        assert len(created) == 1, f"ConnectionPool constructed {len(created)} times"
        assert len(set(results)) == 1, "threads got different pool objects"


class TestRegistryConcurrentGet:
    """Lazy manager creation in DatabaseRegistry must be atomic."""

    def test_concurrent_get_creates_one_manager_per_alias(self, db_config, pool_config):
        """Racing threads on an uninitialised alias share a single manager."""
        registry = DatabaseRegistry(
            {"default": db_config, "analytics": db_config},
            {"default": pool_config, "analytics": pool_config},
        )
        results: list[tuple[str, int]] = []
        results_lock = threading.Lock()

        with patch("pyodbc.connect", return_value=MagicMock()):

            def worker() -> None:
                for alias in ("default", "analytics"):
                    manager = registry.get(alias)
                    with results_lock:
                        results.append((alias, id(manager)))

            errors = _run_concurrently(worker)
            assert errors == []

        for alias in ("default", "analytics"):
            ids = {mid for name, mid in results if name == alias}
            assert len(ids) == 1, f"{alias} produced {len(ids)} managers, expected 1"

        registry.close()

    def test_concurrent_get_unknown_alias_raises_consistently(self, db_config):
        """An unknown alias raises KeyError in every thread, not just the first."""
        registry = DatabaseRegistry({"default": db_config})

        def worker() -> None:
            with pytest.raises(KeyError):
                registry.get("nope")

        errors = _run_concurrently(worker)
        assert errors == []
        registry.close()


class TestRequestIdIsolation:
    def test_concurrent_calls_get_distinct_ids(self):
        from mcp_sql_server.logging_config import request_id_var, with_request_id

        seen: list[str | None] = []
        seen_lock = threading.Lock()
        barrier = threading.Barrier(WORKERS)

        @with_request_id
        def tool() -> None:
            barrier.wait(timeout=10)  # all calls are in flight at once
            with seen_lock:
                seen.append(request_id_var.get())

        errors = _run_concurrently(tool)
        assert errors == []
        assert len(seen) == WORKERS
        assert None not in seen
        assert len(set(seen)) == WORKERS
