"""Database registry for managing named database connections."""

import logging
import threading
from pathlib import Path
from typing import Any

from .config import (
    DatabaseConfig,
    PoolConfig,
    load_all_database_configs,
    load_all_pool_configs,
)
from .database import DatabaseManager

logger = logging.getLogger(__name__)


class DatabaseRegistry:
    """Registry managing named DatabaseManager instances with lazy initialization.

    Each named database gets its own DatabaseManager and connection pool.
    The "default" database is always required and maps to the standard DB_* env vars.
    """

    def __init__(
        self,
        configs: dict[str, DatabaseConfig],
        pool_configs: dict[str, PoolConfig] | None = None,
    ) -> None:
        """Initialize the registry.

        Args:
            configs: Mapping of database alias -> DatabaseConfig. Must include "default".
            pool_configs: Optional mapping of alias -> PoolConfig.
                          Missing aliases use PoolConfig defaults.

        Raises:
            ValueError: If "default" is not in configs.
        """
        if "default" not in configs:
            raise ValueError("configs must include a 'default' database entry")
        self._configs = configs
        self._pool_configs = pool_configs or {}
        self._managers: dict[str, DatabaseManager] = {}
        self._lock = threading.Lock()

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
        """Return list of configured database alias names."""
        return list(self._configs.keys())

    def get_database_info(self) -> list[dict[str, Any]]:
        """Return connection info for all databases (no passwords).

        Returns:
            List of dicts with name, host, port, database fields.
        """
        databases: list[dict[str, Any]] = []
        for name in self._configs:
            config = self._configs[name]
            databases.append({
                "name": name,
                "host": config.host,
                "port": config.port,
                "database": config.database,
            })
        return databases

    def close(self) -> None:
        """Close all DatabaseManager instances and their pools."""
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

        Reads DB_DATABASES and all DB_* / DB_{PREFIX}_* env vars.

        Args:
            env_path: Path to .env file.

        Returns:
            A configured DatabaseRegistry instance.
        """
        configs = load_all_database_configs(env_path)
        pool_configs = load_all_pool_configs(env_path)
        return cls(configs=configs, pool_configs=pool_configs)
