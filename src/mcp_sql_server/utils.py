"""Shared utilities for MCP server tools and resources."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import DatabaseManager
    from .registry import DatabaseRegistry

# Lazy imports to avoid circular dependency
_db_getter = None
_registry_getter = None


def get_db(database: str = "default") -> "DatabaseManager":
    """Get database manager for the named database, lazily initialized.

    This function provides lazy access to database managers via the registry,
    avoiding circular import issues between tools/resources and the server module.

    Args:
        database: Database alias name (default: "default").

    Returns:
        The DatabaseManager instance for the named database.
    """
    global _db_getter
    if _db_getter is None:
        from .server import get_db as server_get_db
        _db_getter = server_get_db
    return _db_getter(database)


def get_registry() -> "DatabaseRegistry":
    """Get the database registry, lazily initialized.

    Returns:
        The DatabaseRegistry instance from the server module.
    """
    global _registry_getter
    if _registry_getter is None:
        from .server import get_registry as server_get_registry
        _registry_getter = server_get_registry
    return _registry_getter()
