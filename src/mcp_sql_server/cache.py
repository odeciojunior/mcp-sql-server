"""Simple TTL-based cache utility for metadata queries."""

import logging
import threading
import time
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

# Type variable for generic function return type
T = TypeVar("T")


class TTLCache:
    """Thread-safe TTL-based cache for storing query results.

    Attributes:
        default_ttl: Default time-to-live in seconds for cached items.
    """

    def __init__(self, default_ttl: int = 60) -> None:
        """Initialize the cache.

        Args:
            default_ttl: Default time-to-live in seconds (default: 60).
        """
        self._cache: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl

    def get(self, key: str) -> tuple[Any, bool]:
        """Get a value from the cache.

        Args:
            key: The cache key.

        Returns:
            Tuple of (value, found). If found is False, value is None.
        """
        with self._lock:
            if key in self._cache:
                value, expires_at = self._cache[key]
                if time.time() < expires_at:
                    logger.debug(f"Cache hit for key: {key}")
                    return value, True
                else:
                    # Expired, remove from cache
                    del self._cache[key]
                    logger.debug(f"Cache expired for key: {key}")
            return None, False

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set a value in the cache.

        Args:
            key: The cache key.
            value: The value to cache.
            ttl: Time-to-live in seconds. Uses default_ttl if not specified.
        """
        ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + ttl
        with self._lock:
            self._cache[key] = (value, expires_at)
            logger.debug(f"Cached key: {key} (TTL: {ttl}s)")

    def invalidate(self, key: str) -> bool:
        """Invalidate a specific cache entry.

        Args:
            key: The cache key to invalidate.

        Returns:
            True if the key was found and removed, False otherwise.
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug(f"Invalidated cache key: {key}")
                return True
            return False

    def clear(self) -> int:
        """Clear all cached entries.

        Returns:
            Number of entries that were cleared.
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.debug(f"Cleared {count} cache entries")
            return count

    def cleanup_expired(self) -> int:
        """Remove all expired entries from the cache.

        Returns:
            Number of expired entries that were removed.
        """
        now = time.time()
        with self._lock:
            expired_keys = [
                key for key, (_, expires_at) in self._cache.items()
                if now >= expires_at
            ]
            for key in expired_keys:
                del self._cache[key]
            if expired_keys:
                logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
            return len(expired_keys)

    def stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dictionary with cache statistics.
        """
        now = time.time()
        with self._lock:
            total = len(self._cache)
            valid = sum(1 for _, expires_at in self._cache.values() if now < expires_at)
            expired = total - valid
            return {
                "total_entries": total,
                "valid_entries": valid,
                "expired_entries": expired,
                "default_ttl": self.default_ttl,
            }


# Global cache instance for metadata queries
_metadata_cache: TTLCache | None = None


def get_metadata_cache() -> TTLCache:
    """Get the global metadata cache instance.

    Returns:
        The global TTLCache instance for metadata.
    """
    global _metadata_cache
    if _metadata_cache is None:
        _metadata_cache = TTLCache(default_ttl=60)
    return _metadata_cache


def cached(ttl: int | None = None, key_prefix: str = "") -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator for caching function results.

    Args:
        ttl: Time-to-live in seconds. Uses cache default if not specified.
        key_prefix: Prefix for the cache key.

    Returns:
        Decorator function.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            cache = get_metadata_cache()

            # Build cache key from function name and arguments
            key_parts = [key_prefix or func.__name__]
            if args:
                key_parts.extend(str(arg) for arg in args)
            if kwargs:
                key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = ":".join(key_parts)

            # Try to get from cache
            value, found = cache.get(cache_key)
            if found:
                return value  # type: ignore[no-any-return]

            # Execute function and cache result
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl)
            return result

        return wrapper
    return decorator


def invalidate_metadata_cache() -> int:
    """Invalidate all metadata cache entries.

    Returns:
        Number of entries that were cleared.
    """
    return get_metadata_cache().clear()
