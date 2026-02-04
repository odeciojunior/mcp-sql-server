"""Object definition tools for MCP server."""

import logging
from typing import Any

from ..security import sanitize_table_name
from ..utils import get_db as _get_db

logger = logging.getLogger(__name__)


def get_view_definition(
    view_name: str,
    schema: str = "dbo",
    database: str = "default",
) -> dict[str, Any]:
    """
    Get the SQL definition of a database view.

    Args:
        view_name: Name of the view
        schema: Schema name (default: dbo)
        database: Target database alias name (default: "default")

    Returns:
        Dictionary with view definition
    """
    try:
        sanitize_table_name(view_name, schema)
    except ValueError as e:
        return {"error": str(e), "success": False}

    sql = """
    SELECT OBJECT_DEFINITION(OBJECT_ID(?)) as definition
    """

    try:
        full_name = f"{schema}.{view_name}"
        results = _get_db(database).execute_query(sql, (full_name,))
        if results and results[0].get("definition"):
            return {"success": True, "view": full_name, "definition": results[0]["definition"]}
        return {"error": f"View not found: {full_name}", "success": False}
    except Exception as e:
        logger.error(f"Error getting view definition: {e}")
        return {"error": str(e), "success": False}


def get_function_definition(
    function_name: str,
    schema: str = "dbo",
    database: str = "default",
) -> dict[str, Any]:
    """
    Get the SQL definition of a user-defined function.

    Args:
        function_name: Name of the function
        schema: Schema name (default: dbo)
        database: Target database alias name (default: "default")

    Returns:
        Dictionary with function definition
    """
    try:
        sanitize_table_name(function_name, schema)
    except ValueError as e:
        return {"error": str(e), "success": False}

    sql = """
    SELECT OBJECT_DEFINITION(OBJECT_ID(?)) as definition
    """

    try:
        full_name = f"{schema}.{function_name}"
        results = _get_db(database).execute_query(sql, (full_name,))
        if results and results[0].get("definition"):
            return {"success": True, "function": full_name, "definition": results[0]["definition"]}
        return {"error": f"Function not found: {full_name}", "success": False}
    except Exception as e:
        logger.error(f"Error getting function definition: {e}")
        return {"error": str(e), "success": False}
