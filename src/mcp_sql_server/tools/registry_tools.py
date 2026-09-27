"""Registry tools for listing configured database connections."""

from typing import Any

from ..registry import DEFAULT_PROBE_TIMEOUT
from ..utils import get_registry as _get_registry


def list_databases(
    probe: bool = True, timeout: float = DEFAULT_PROBE_TIMEOUT
) -> dict[str, Any]:
    """List all configured database connections.

    Args:
        probe: Open a real connection per database to report whether it is
               actually reachable. Set False for a fast config-only listing,
               which reports status "unknown" rather than assuming "ok".
        timeout: Per-probe connection timeout in seconds.

    Returns:
        Dictionary with database names and connection info (no passwords).
        Each entry's status is "ok", "unreachable", "misconfigured", or
        "unknown".
    """
    registry = _get_registry()
    databases = registry.get_database_info(probe=probe, timeout=timeout)
    return {"success": True, "databases": databases, "count": len(databases)}
