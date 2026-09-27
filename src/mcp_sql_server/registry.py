"""Database registry for managing named database connections."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pyodbc

from .config import (
    DatabaseConfig,
    PoolConfig,
    describe_config_error,
    get_database_names,
    load_database_config,
    load_pool_config,
)
from .database import DatabaseManager
from .errors import sanitize_error

logger = logging.getLogger(__name__)

# Probe connections are health checks, not work: they must fail fast rather
# than inherit DB_TIMEOUT (30s by default), which would make a listing of
# several offline databases take minutes.
DEFAULT_PROBE_TIMEOUT = 5.0

# Probes are independent and almost entirely I/O wait, so they run together.
# Capped because a registry could hold many aliases.
_MAX_PROBE_WORKERS = 8


class DatabaseRegistry:
    """Registry managing named DatabaseManager instances with lazy initialization.

    Each named database gets its own DatabaseManager and connection pool.
    The "default" database is always required and maps to the standard DB_* env vars.
    """

    def __init__(
        self,
        configs: dict[str, DatabaseConfig],
        pool_configs: dict[str, PoolConfig] | None = None,
        config_errors: dict[str, str] | None = None,
    ) -> None:
        """Initialize the registry.

        Args:
            configs: Mapping of alias -> DatabaseConfig for valid databases.
            pool_configs: Optional mapping of alias -> PoolConfig.
                          Missing aliases use PoolConfig defaults.
            config_errors: Mapping of alias -> safe error message for
                           databases whose configuration is invalid.

        Raises:
            ValueError: If "default" is in neither configs nor config_errors.
        """
        self._config_errors = dict(config_errors or {})
        if "default" not in configs and "default" not in self._config_errors:
            raise ValueError("configs must include a 'default' database entry")
        self._configs = configs
        self._pool_configs = pool_configs or {}
        self._managers: dict[str, DatabaseManager] = {}
        self._lock = threading.Lock()

    @property
    def config_errors(self) -> dict[str, str]:
        """Aliases whose configuration failed validation, with safe messages."""
        return dict(self._config_errors)

    def get(self, name: str = "default") -> DatabaseManager:
        """Get or lazily create a DatabaseManager for the named database.

        Args:
            name: Database alias name.

        Returns:
            The DatabaseManager for the named database.

        Raises:
            KeyError: If name is not a configured database.
        """
        # Fast path: already created
        if name in self._managers:
            return self._managers[name]

        if name in self._config_errors:
            raise ValueError(
                f"Database '{name}' is misconfigured: {self._config_errors[name]}"
            )

        # Validate name exists in config
        if name not in self._configs:
            available = ", ".join(sorted(self._configs.keys()))
            raise KeyError(
                f"Unknown database '{name}'. Available databases: {available}"
            )

        # Thread-safe lazy initialization
        with self._lock:
            if name not in self._managers:
                config = self._configs[name]
                pool_config = self._pool_configs.get(name, PoolConfig())
                manager = DatabaseManager(config, pool_config)
                self._managers[name] = manager
                logger.info(
                    f"Initialized database '{name}': {config.database}@{config.host}"
                )
        return self._managers[name]

    def list_databases(self) -> list[str]:
        """Return all configured alias names, including misconfigured ones."""
        return [*self._configs, *(n for n in self._config_errors if n not in self._configs)]

    def probe(
        self, name: str, timeout: float = DEFAULT_PROBE_TIMEOUT
    ) -> tuple[str, str | None]:
        """Open one connection to `name` and run SELECT 1.

        Deliberately not routed through get(): that lazily builds a
        DatabaseManager and a pool of min_size connections, which is far too
        heavy for a health check and would leave a broken pool cached for a
        database that is down.

        Args:
            name: Database alias to probe.
            timeout: Seconds to wait for the connection.

        Returns:
            ("ok", None) if a query succeeded, else ("unreachable", message).
            The message is sanitised: it never contains credentials.

        Raises:
            KeyError: If name is not a configured database.
        """
        if name not in self._configs:
            raise KeyError(f"Unknown database '{name}'")
        config = self._configs[name]
        conn = None
        try:
            conn = pyodbc.connect(config.get_connection_string(), timeout=int(timeout))
            # Opening a connection is not enough: a database can accept the
            # login and still refuse to serve queries.
            conn.cursor().execute("SELECT 1")
            return "ok", None
        except Exception as e:
            return "unreachable", sanitize_error(e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.debug("Error closing probe connection for '%s'", name)

    def get_database_info(
        self, probe: bool = False, timeout: float = DEFAULT_PROBE_TIMEOUT
    ) -> list[dict[str, Any]]:
        """Return connection info for all databases (no passwords).

        Args:
            probe: Open a real connection per alias to determine status.
                   Without it no reachability claim is made.
            timeout: Per-probe connection timeout in seconds.

        Returns:
            One dict per alias. Configured: name, host, port, database, and
            status "ok" / "unreachable" (with error) when probed, else
            "unknown". Misconfigured: name, status "misconfigured", error.

        Status never asserts reachability that was not measured -- reporting
        "ok" for a database that merely parsed is how an OFFLINE database
        came back healthy.
        """
        names = self.list_databases()
        probeable = [n for n in names if n not in self._config_errors]

        results: dict[str, tuple[str, str | None]] = {}
        if probe and probeable:
            workers = min(len(probeable), _MAX_PROBE_WORKERS)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for name, result in zip(
                    probeable, pool.map(lambda n: self.probe(n, timeout), probeable)
                ):
                    results[name] = result

        databases: list[dict[str, Any]] = []
        for name in names:
            if name in self._config_errors:
                databases.append({
                    "name": name,
                    "status": "misconfigured",
                    "error": self._config_errors[name],
                })
                continue
            config = self._configs[name]
            status, error = results.get(name, ("unknown", None))
            entry: dict[str, Any] = {
                "name": name,
                "host": config.host,
                "port": config.port,
                "database": config.database,
                "status": status,
            }
            if error:
                entry["error"] = error
            databases.append(entry)
        return databases

    def close(self) -> None:
        """Close all DatabaseManager instances and their pools.

        Shutdown path: every manager is closed even if one fails. Failures are
        logged, not raised, so one bad pool cannot leave the others open.
        Use close_database() to close one database and see its error.
        """
        with self._lock:
            for name, manager in self._managers.items():
                try:
                    manager.close()
                    logger.info(f"Closed database '{name}'")
                except Exception:
                    logger.exception(f"Error closing database '{name}'")
            self._managers.clear()

    def close_database(self, name: str) -> None:
        """Close a specific named database connection.

        Args:
            name: Database alias to close.

        Raises:
            KeyError: If name is not a configured database.
        """
        if name not in self._configs:
            raise KeyError(f"Unknown database '{name}'")
        with self._lock:
            manager = self._managers.pop(name, None)
            if manager:
                manager.close()
                logger.info(f"Closed database '{name}'")

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "DatabaseRegistry":
        """Create a registry from environment configuration.

        Each alias is loaded independently: an invalid alias is recorded in
        config_errors (with a value-free message) instead of failing the
        whole registry. An invalid DB_DATABASES list still raises.
        """
        configs: dict[str, DatabaseConfig] = {}
        pool_configs: dict[str, PoolConfig] = {}
        errors: dict[str, str] = {}
        for name in get_database_names(env_path):
            try:
                config = load_database_config(name, env_path)
                pool_config = load_pool_config(name, env_path)
            except ValueError as e:
                errors[name] = describe_config_error(e)
                continue
            configs[name] = config
            pool_configs[name] = pool_config
        return cls(configs=configs, pool_configs=pool_configs, config_errors=errors)
