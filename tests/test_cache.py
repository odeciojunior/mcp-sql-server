"""Tests for TTL cache utility."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from sql_playground_mcp.cache import (
    TTLCache,
    cached,
    get_metadata_cache,
    invalidate_metadata_cache,
)


class TestTTLCache:
    """Tests for TTLCache class."""

    def test_init_default_ttl(self):
        cache = TTLCache()
        assert cache.default_ttl == 60

    def test_init_custom_ttl(self):
        cache = TTLCache(default_ttl=120)
        assert cache.default_ttl == 120

    def test_set_and_get_value(self):
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1")
        value, found = cache.get("key1")
        assert found is True
        assert value == "value1"

    def test_get_nonexistent_key(self):
        cache = TTLCache(default_ttl=60)
        value, found = cache.get("nonexistent")
        assert found is False
        assert value is None

    def test_get_expired_key(self):
        cache = TTLCache(default_ttl=1)
        cache.set("key1", "value1", ttl=0)  # Immediately expired
        time.sleep(0.01)  # Ensure it's expired
        value, found = cache.get("key1")
        assert found is False
        assert value is None

    def test_set_with_custom_ttl(self):
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1", ttl=1)
        value, found = cache.get("key1")
        assert found is True
        time.sleep(1.1)
        value, found = cache.get("key1")
        assert found is False

    def test_invalidate_existing_key(self):
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1")
        result = cache.invalidate("key1")
        assert result is True
        value, found = cache.get("key1")
        assert found is False

    def test_invalidate_nonexistent_key(self):
        cache = TTLCache(default_ttl=60)
        result = cache.invalidate("nonexistent")
        assert result is False

    def test_clear(self):
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        count = cache.clear()
        assert count == 3
        _, found1 = cache.get("key1")
        _, found2 = cache.get("key2")
        assert found1 is False
        assert found2 is False

    def test_clear_empty_cache(self):
        cache = TTLCache(default_ttl=60)
        count = cache.clear()
        assert count == 0

    def test_cleanup_expired(self):
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1", ttl=0)  # Immediately expired
        cache.set("key2", "value2", ttl=60)  # Still valid
        time.sleep(0.01)
        count = cache.cleanup_expired()
        assert count == 1
        _, found1 = cache.get("key1")
        _, found2 = cache.get("key2")
        assert found1 is False
        assert found2 is True

    def test_stats(self):
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1")
        cache.set("key2", "value2", ttl=0)  # Immediately expired
        time.sleep(0.01)
        stats = cache.stats()
        assert stats["total_entries"] == 2
        assert stats["valid_entries"] == 1
        assert stats["expired_entries"] == 1
        assert stats["default_ttl"] == 60

    def test_thread_safety(self):
        cache = TTLCache(default_ttl=60)
        results = []
        errors = []

        def worker(n):
            try:
                for i in range(100):
                    key = f"key_{n}_{i}"
                    cache.set(key, f"value_{n}_{i}")
                    value, found = cache.get(key)
                    if found:
                        results.append(value)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 500  # 5 threads * 100 iterations


class TestCachedDecorator:
    """Tests for @cached decorator."""

    def test_cached_function_returns_value(self):
        call_count = 0

        @cached(ttl=60)
        def my_func():
            nonlocal call_count
            call_count += 1
            return "result"

        result = my_func()
        assert result == "result"
        assert call_count == 1

    def test_cached_function_uses_cache(self):
        # Clear any existing cache first
        invalidate_metadata_cache()

        call_count = 0

        @cached(ttl=60)
        def my_func():
            nonlocal call_count
            call_count += 1
            return "result"

        result1 = my_func()
        result2 = my_func()
        assert result1 == result2
        assert call_count == 1  # Function called only once

    def test_cached_with_args(self):
        invalidate_metadata_cache()
        call_count = 0

        @cached(ttl=60)
        def my_func(x, y):
            nonlocal call_count
            call_count += 1
            return x + y

        result1 = my_func(1, 2)
        result2 = my_func(1, 2)  # Same args - should use cache
        result3 = my_func(3, 4)  # Different args - should call function

        assert result1 == 3
        assert result2 == 3
        assert result3 == 7
        assert call_count == 2

    def test_cached_with_kwargs(self):
        invalidate_metadata_cache()
        call_count = 0

        @cached(ttl=60)
        def my_func(name=None):
            nonlocal call_count
            call_count += 1
            return f"Hello, {name}"

        result1 = my_func(name="Alice")
        result2 = my_func(name="Alice")  # Same kwargs - should use cache
        result3 = my_func(name="Bob")  # Different kwargs - should call function

        assert result1 == "Hello, Alice"
        assert result2 == "Hello, Alice"
        assert result3 == "Hello, Bob"
        assert call_count == 2

    def test_cached_with_key_prefix(self):
        invalidate_metadata_cache()
        call_count = 0

        @cached(ttl=60, key_prefix="custom_prefix")
        def my_func():
            nonlocal call_count
            call_count += 1
            return "result"

        result1 = my_func()
        result2 = my_func()
        assert result1 == result2
        assert call_count == 1

    def test_cached_expires(self):
        invalidate_metadata_cache()
        call_count = 0

        @cached(ttl=1)
        def my_func():
            nonlocal call_count
            call_count += 1
            return "result"

        result1 = my_func()
        time.sleep(1.1)  # Wait for cache to expire
        result2 = my_func()  # Should call function again

        assert result1 == result2
        assert call_count == 2


class TestGlobalCache:
    """Tests for global cache functions."""

    def test_get_metadata_cache_returns_singleton(self):
        cache1 = get_metadata_cache()
        cache2 = get_metadata_cache()
        assert cache1 is cache2

    def test_invalidate_metadata_cache(self):
        cache = get_metadata_cache()
        cache.set("test_key", "test_value")
        _, found = cache.get("test_key")
        assert found is True

        count = invalidate_metadata_cache()
        assert count >= 1

        _, found = cache.get("test_key")
        assert found is False


class TestCacheIsolationByDatabase:
    """Tests that cached functions produce different entries per database."""

    def test_cached_with_database_kwarg_produces_different_keys(self):
        """Calling a cached function with different database values must not cross-contaminate."""
        invalidate_metadata_cache()
        call_count = 0

        @cached(ttl=60, key_prefix="isolation_test")
        def my_func(schema=None, database="default"):
            nonlocal call_count
            call_count += 1
            return f"result_{database}"

        r1 = my_func(schema=None, database="default")
        r2 = my_func(schema=None, database="analytics")
        r3 = my_func(schema=None, database="default")  # should be cached

        assert r1 == "result_default"
        assert r2 == "result_analytics"
        assert r3 == "result_default"
        assert call_count == 2  # default + analytics, third call is cached

    def test_same_database_returns_cached(self):
        """Repeated calls with the same database should use cache."""
        invalidate_metadata_cache()
        call_count = 0

        @cached(ttl=60, key_prefix="same_db_test")
        def my_func(database="default"):
            nonlocal call_count
            call_count += 1
            return "data"

        my_func(database="db1")
        my_func(database="db1")
        my_func(database="db1")

        assert call_count == 1
