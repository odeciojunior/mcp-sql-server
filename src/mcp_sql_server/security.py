"""Security utilities for SQL validation and keyword blocking."""

import re

from .sql_lexer import LexError, Token, TokenKind, tokenize

# Blocked SQL keywords that could cause damage. Also used by validate_identifier.
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

# Words that begin a separate T-SQL statement. T-SQL does not require ';'
# between statements, so these are rejected anywhere in either mode.
# Kept apart from BLOCKED_KEYWORDS so identifiers such as a table named
# "Print" remain valid for describe_table.
# END (CASE ... END) and FETCH (OFFSET ... FETCH) are deliberately absent.
STATEMENT_WORDS: set[str] = {
    "EXEC", "EXECUTE", "DENY", "USE", "DECLARE", "WAITFOR", "BEGIN", "COMMIT",
    "ROLLBACK", "SAVE", "PRINT", "RAISERROR", "THROW", "IF", "WHILE", "GOTO",
    "RETURN", "OPEN", "CLOSE", "DEALLOCATE", "CHECKPOINT", "RECONFIGURE",
    "SETUSER", "REVERT", "RECEIVE", "SEND", "WRITETEXT", "UPDATETEXT",
    "READTEXT", "ADD",
}

# Consecutive word pairs that start a statement or cause side effects.
# Single words ENABLE/DISABLE/NEXT/GET/MOVE/END are common identifiers.
BLOCKED_PAIRS: set[tuple[str, str]] = {
    ("ENABLE", "TRIGGER"),
    ("DISABLE", "TRIGGER"),
    ("NEXT", "VALUE"),
    ("GET", "CONVERSATION"),
    ("MOVE", "CONVERSATION"),
    ("END", "CONVERSATION"),
}

# System functions that read files, including over SMB (credential leak).
BLOCKED_FUNCTIONS: set[str] = {
    "FN_XE_FILE_TARGET_READ_FILE",
    "FN_TRACE_GETTABLE",
    "FN_GET_AUDIT_FILE",
}

# Allowed statement types for execute_query (read-only)
ALLOWED_QUERY_KEYWORDS: set[str] = {"SELECT", "WITH"}

# Allowed statement types for execute_statement (modifications)
ALLOWED_STATEMENT_KEYWORDS: set[str] = {"INSERT", "UPDATE", "DELETE"}

# Words that may not appear anywhere in a read-only query
READ_ONLY_FORBIDDEN: set[str] = {"INSERT", "UPDATE", "DELETE", "MERGE", "INTO", "SET"}

# Words that may precede a second top-level SELECT
SET_OPERATORS: set[str] = {"UNION", "EXCEPT", "INTERSECT"}

_DML_WORDS: set[str] = {"INSERT", "UPDATE", "DELETE", "MERGE"}
_MULTIPLE = "Multiple statements are not allowed"


def _follows_set_operator(tokens: list[Token], idx: int) -> bool:
    """True if tokens[idx] is preceded by UNION/EXCEPT/INTERSECT (optionally + ALL)."""
    j = idx - 1
    if j >= 0 and tokens[j].kind is TokenKind.WORD and tokens[j].upper == "ALL":
        j -= 1
    return j >= 0 and tokens[j].kind is TokenKind.WORD and tokens[j].upper in SET_OPERATORS


def _check_read_query(tokens: list[Token]) -> tuple[bool, str]:
    select_seen = False
    for idx, tok in enumerate(tokens):
        if tok.kind is not TokenKind.WORD:
            continue
        word = tok.upper
        if word in READ_ONLY_FORBIDDEN:
            return False, f"Data modification not allowed in read-only query: {word}"
        if word == "SELECT" and tok.depth == 0:
            if select_seen and not _follows_set_operator(tokens, idx):
                return False, _MULTIPLE
            select_seen = True
    return True, ""


def _check_modify_statement(tokens: list[Token]) -> tuple[bool, str]:
    first = tokens[0].upper
    set_seen = False
    select_seen = False
    values_seen = False
    output_seen = False
    output_into_used = False
    for idx, tok in enumerate(tokens):
        if idx == 0 or tok.kind is not TokenKind.WORD:
            continue
        word = tok.upper
        if word in _DML_WORDS and tok.depth == 0:
            return False, _MULTIPLE
        if word == "SET":
            if first != "UPDATE" or set_seen or tok.depth != 0:
                return False, _MULTIPLE
            set_seen = True
        elif word in ("VALUES", "DEFAULT") and tok.depth == 0:
            values_seen = True
        elif word == "SELECT" and tok.depth == 0:
            if first != "INSERT" or values_seen:
                return False, _MULTIPLE
            if select_seen and not _follows_set_operator(tokens, idx):
                return False, _MULTIPLE
            select_seen = True
        elif word == "OUTPUT" and tok.depth == 0:
            output_seen = True
        elif word == "INTO":
            if idx == 1 and first == "INSERT":
                continue
            if output_seen and not output_into_used and tok.depth == 0:
                output_into_used = True
                continue
            return False, "INTO not allowed here"
    return True, ""


def validate_query(sql: str, allow_modifications: bool = False) -> tuple[bool, str]:
    """
    Validate SQL for security issues.

    Tokenizes the SQL so string literals, quoted identifiers, and comments
    are never inspected for keywords, then enforces a single statement at
    parenthesis depth 0 (T-SQL does not need ';' between statements).

    Args:
        sql: The SQL to validate
        allow_modifications: True for execute_statement (INSERT/UPDATE/DELETE
            only); False for execute_query (SELECT/WITH only)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not sql or not sql.strip():
        return False, "Query cannot be empty"

    try:
        tokens = tokenize(sql)
    except LexError as e:
        return False, f"Invalid SQL: {e}"

    if not tokens:
        return False, "Query cannot be empty"

    last = len(tokens) - 1
    for idx, tok in enumerate(tokens):
        if tok.kind is TokenKind.SEMICOLON and idx != last:
            return False, _MULTIPLE

    words = [t for t in tokens if t.kind is TokenKind.WORD]

    for tok in words:
        for prefix in sorted(BLOCKED_PREFIXES):
            if tok.upper.startswith(prefix.upper()):
                return False, f"System procedure calls not allowed: {prefix}*"

    for tok in words:
        if tok.upper in BLOCKED_KEYWORDS:
            return False, f"Blocked keyword detected: {tok.upper}"

    for tok in words:
        if tok.upper in STATEMENT_WORDS:
            return False, f"Statement not allowed: {tok.upper}"

    for a, b in zip(tokens, tokens[1:]):
        if (
            a.kind is TokenKind.WORD
            and b.kind is TokenKind.WORD
            and (a.upper, b.upper) in BLOCKED_PAIRS
        ):
            return False, f"Statement not allowed: {a.upper} {b.upper}"

    for tok in words:
        if tok.upper in BLOCKED_FUNCTIONS:
            return False, f"Function not allowed: {tok.upper}"

    first = tokens[0]
    allowed = ALLOWED_STATEMENT_KEYWORDS if allow_modifications else ALLOWED_QUERY_KEYWORDS
    first_text = first.upper if first.kind is TokenKind.WORD else first.text
    if first.kind is not TokenKind.WORD or first_text not in allowed:
        return False, f"Statement type '{first_text}' not allowed"

    if allow_modifications:
        return _check_modify_statement(tokens)
    return _check_read_query(tokens)


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
