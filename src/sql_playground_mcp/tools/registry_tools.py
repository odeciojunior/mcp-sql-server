"""Registry tools for listing configured database connections."""

from typing import Any

from ..utils import get_registry as _get_registry


def list_databases() -> dict[str, Any]:
    """List all configured database connections.

    Returns:
        Dictionary with database names and connection info (no passwords).
    """
    registry = _get_registry()
    databases = registry.get_database_info()
    return {"success": True, "databases": databases, "count": len(databases)}
