"""Error handling and sanitization utilities."""

import re
from typing import Any


# Patterns for sensitive information that should be sanitized
SENSITIVE_PATTERNS = [
    # Login/auth errors - hide username
    (r"Login failed for user '([^']+)'", "Login failed for user '[REDACTED]'"),
    # Connection string details
    (r"SERVER=([^;]+)", "SERVER=[REDACTED]"),
    (r"UID=([^;]+)", "UID=[REDACTED]"),
    (r"PWD=([^;]+)", "PWD=[REDACTED]"),
    # IP addresses
    (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "[REDACTED_IP]"),
    # Database names in errors (optional - may want to keep these)
    # (r"database '([^']+)'", "database '[REDACTED]'"),
]

# Error message simplifications for cleaner output
ERROR_SIMPLIFICATIONS = {
    r"Invalid object name '([^']+)'": "Object not found: {}",
    r"Invalid column name '([^']+)'": "Column not found: {}",
    r"Could not find stored procedure '([^']+)'": "Procedure not found: {}",
    r"The multi-part identifier \"([^\"]+)\" could not be bound": "Invalid identifier: {}",
    r"Arithmetic overflow error": "Numeric overflow error",
    r"String or binary data would be truncated": "Data too large for column",
    r"Violation of PRIMARY KEY constraint": "Duplicate primary key",
    r"Violation of UNIQUE KEY constraint": "Duplicate unique value",
    r"The INSERT statement conflicted with the FOREIGN KEY constraint": "Foreign key constraint violation",
    r"The DELETE statement conflicted with the REFERENCE constraint": "Cannot delete - referenced by other records",
}


def sanitize_error(error: Exception | str, context: str = "") -> str:
    """Sanitize error message to hide sensitive details.

    Args:
        error: Exception or error string to sanitize
        context: Optional context for the error (e.g., "query", "connection")

    Returns:
        Sanitized error message
    """
    error_str = str(error)

    # Apply sensitive pattern replacements
    for pattern, replacement in SENSITIVE_PATTERNS:
        error_str = re.sub(pattern, replacement, error_str, flags=re.IGNORECASE)

    return error_str


def simplify_error(error: str) -> str:
    """Simplify common SQL Server error messages for clarity.

    Args:
        error: Error message to simplify

    Returns:
        Simplified error message
    """
    for pattern, template in ERROR_SIMPLIFICATIONS.items():
        match = re.search(pattern, error, re.IGNORECASE)
        if match:
            if "{}" in template and match.groups():
                return template.format(match.group(1))
            return template

    return error


def create_error_response(
    error: Exception | str,
    context: str = "",
    include_details: bool = False,
) -> dict[str, Any]:
    """Create a standardized error response with sanitized message.

    Args:
        error: Exception or error string
        context: Optional context for the error
        include_details: Whether to include additional details

    Returns:
        Dictionary with 'error' and 'success' keys
    """
    sanitized = sanitize_error(error, context)
    simplified = simplify_error(sanitized)

    response: dict[str, Any] = {
        "success": False,
        "error": simplified,
    }

    if include_details and sanitized != simplified:
        response["error_detail"] = sanitized

    if context:
        response["error_context"] = context

    return response


class MCPError(Exception):
    """Base exception for MCP server errors."""

    def __init__(self, message: str, context: str = ""):
        super().__init__(message)
        self.message = message
        self.context = context

    def to_response(self) -> dict[str, Any]:
        """Convert to error response dictionary."""
        return create_error_response(self.message, self.context)


class ValidationError(MCPError):
    """Raised when SQL validation fails."""

    def __init__(self, message: str, blocked_keyword: str | None = None):
        super().__init__(message, context="validation")
        self.blocked_keyword = blocked_keyword


class ConnectionError(MCPError):
    """Raised when database connection fails."""

    def __init__(self, message: str):
        super().__init__(message, context="connection")


class QueryError(MCPError):
    """Raised when query execution fails."""

    def __init__(self, message: str):
        super().__init__(message, context="query")


class TimeoutError(MCPError):
    """Raised when an operation times out."""

    def __init__(self, message: str):
        super().__init__(message, context="timeout")
