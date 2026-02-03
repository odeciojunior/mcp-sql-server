"""Security utilities for SQL validation and keyword blocking."""

import re

# Blocked SQL keywords that could cause damage
BLOCKED_KEYWORDS: set[str] = {
    "DROP",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "GRANT",
    "REVOKE",
    "SHUTDOWN",
    "BACKUP",
    "RESTORE",
    "DBCC",
    "OPENROWSET",
    "OPENQUERY",
    "OPENDATASOURCE",
    "BULK",
    "KILL",
}

# Blocked prefixes for dangerous system procedures
BLOCKED_PREFIXES: set[str] = {"xp_", "sp_"}

# Allowed statement types for execute_query (read-only)
ALLOWED_QUERY_KEYWORDS: set[str] = {"SELECT", "WITH"}

# Allowed statement types for execute_statement (modifications)
ALLOWED_STATEMENT_KEYWORDS: set[str] = {"INSERT", "UPDATE", "DELETE"}


def validate_query(sql: str, allow_modifications: bool = False) -> tuple[bool, str]:
    """
    Validate SQL query for security issues.

    Args:
        sql: The SQL query to validate
        allow_modifications: Whether to allow INSERT/UPDATE/DELETE

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not sql or not sql.strip():
        return False, "Query cannot be empty"

    # Normalize SQL for checking
    sql_upper = sql.upper().strip()

    # Check for blocked keywords
    for keyword in BLOCKED_KEYWORDS:
        pattern = rf"\b{keyword}\b"
        if re.search(pattern, sql_upper):
            return False, f"Blocked keyword detected: {keyword}"

    # Check for blocked prefixes (xp_, sp_)
    for prefix in BLOCKED_PREFIXES:
        pattern = rf"\b{prefix}\w+"
        if re.search(pattern, sql_upper, re.IGNORECASE):
            return False, f"System procedure calls not allowed: {prefix}*"

    # Validate statement type
    first_word = sql_upper.split()[0] if sql_upper else ""

    if allow_modifications:
        allowed = ALLOWED_QUERY_KEYWORDS | ALLOWED_STATEMENT_KEYWORDS
    else:
        allowed = ALLOWED_QUERY_KEYWORDS

    if first_word not in allowed:
        return False, f"Statement type '{first_word}' not allowed"

    return True, ""


def validate_identifier(name: str) -> tuple[bool, str]:
    """
    Validate table/column/schema names to prevent injection.
    Only allows alphanumeric characters and underscores.
    """
    if not name:
        return False, "Identifier cannot be empty"

    # SQL Server identifier rules: starts with letter or underscore
    pattern = r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    if not re.match(pattern, name):
        return False, f"Invalid identifier: {name}"

    # Check for reserved words
    if name.upper() in BLOCKED_KEYWORDS:
        return False, f"Reserved keyword not allowed as identifier: {name}"

    return True, ""


def validate_procedure_name(proc_name: str) -> tuple[bool, str]:
    """
    Validate procedure name isn't a blocked system procedure.

    Args:
        proc_name: Name of the stored procedure

    Returns:
        Tuple of (is_valid, error_message)
    """
    proc_upper = proc_name.upper()
    for prefix in BLOCKED_PREFIXES:
        if proc_upper.startswith(prefix.upper()):
            return False, f"System procedure not allowed: {proc_name}"
    return True, ""


def sanitize_table_name(table_name: str, schema: str = "dbo") -> str:
    """
    Safely quote table name for use in queries.
    Uses bracket notation to prevent injection.
    """
    valid, error = validate_identifier(table_name)
    if not valid:
        raise ValueError(error)

    valid, error = validate_identifier(schema)
    if not valid:
        raise ValueError(error)

    return f"[{schema}].[{table_name}]"
