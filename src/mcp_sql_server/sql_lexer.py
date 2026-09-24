"""Minimal T-SQL tokenizer for statement validation and log masking.

This is not a parser. It classifies tokens and tracks parenthesis depth so
that validation never inspects string literals, quoted identifiers, or
comments for keywords. Any character it does not recognise becomes an OTHER
token, so a mismatch with SQL Server's own lexer can only split words (and
cause a rejection), never hide them.
"""

from dataclasses import dataclass
from enum import Enum

_WHITESPACE = frozenset(" \t\r\n\f\v")
_ASCII_DIGITS = frozenset("0123456789")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class TokenKind(Enum):
    WORD = "word"
    STRING = "string"
    QUOTED_IDENT = "quoted_ident"
    NUMBER = "number"
    SEMICOLON = "semicolon"
    OTHER = "other"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str
    start: int
    end: int
    depth: int

    @property
    def upper(self) -> str:
        return self.text.upper()


class LexError(ValueError):
    """Raised for an unterminated string, quoted identifier, or block comment."""


def _is_word_start(ch: str) -> bool:
    return ch in "_@#" or ch.isalpha()


def _is_word_char(ch: str) -> bool:
    return ch in "_@#$" or ch in _ASCII_DIGITS or ch.isalpha()


def _scan_delimited(sql: str, pos: int, close: str, what: str) -> int:
    """Return the index just past the closing delimiter.

    `pos` is the index just after the opening delimiter. A doubled closing
    delimiter is an escaped literal character.
    """
    n = len(sql)
    i = pos
    while i < n:
        if sql[i] == close:
            if i + 1 < n and sql[i + 1] == close:
                i += 2
                continue
            return i + 1
        i += 1
    raise LexError(f"unterminated {what}")


def _scan_block_comment(sql: str, pos: int) -> int:
    """Return the index just past a (possibly nested) block comment at `pos`."""
    n = len(sql)
    nesting = 0
    i = pos
    while i < n:
        if sql.startswith("/*", i):
            nesting += 1
            i += 2
        elif sql.startswith("*/", i):
            nesting -= 1
            i += 2
            if nesting == 0:
                return i
        else:
            i += 1
    raise LexError("unterminated block comment")


def _scan_number(sql: str, pos: int) -> int:
    n = len(sql)
    i = pos
    if sql[i] == "0" and i + 1 < n and sql[i + 1] in "xX":
        i += 2
        while i < n and sql[i] in _HEX_DIGITS:
            i += 1
        return i
    while i < n and sql[i] in _ASCII_DIGITS:
        i += 1
    if i < n and sql[i] == ".":
        i += 1
        while i < n and sql[i] in _ASCII_DIGITS:
            i += 1
    if i < n and sql[i] in "eE":
        # T-SQL always reads e/E here as the start of an exponent, even with
        # no digits following (e.g. "1e" is a float literal, not "1" then a
        # word "e..."). Consuming it unconditionally keeps a following
        # keyword from being glued onto it (fail closed: "1eEXEC" must not
        # split into NUMBER "1" + WORD "eEXEC").
        i += 1
        if i < n and sql[i] in "+-":
            # A sign with no digit after it is ambiguous: SQL Server may read
            # "1e-" followed by "-" as a bare exponent then a minus, or may
            # read "1e--" as "1e" followed by a "--" comment. We cannot know
            # which, so refuse rather than silently pick one (fail closed).
            if i + 1 >= n or sql[i + 1] not in _ASCII_DIGITS:
                raise LexError("malformed number exponent")
            i += 1
        while i < n and sql[i] in _ASCII_DIGITS:
            i += 1
    return i


def tokenize(sql: str) -> list[Token]:
    """Split T-SQL into tokens; comments and whitespace produce none.

    Raises:
        LexError: for an unterminated string, quoted identifier, or block comment.
    """
    tokens: list[Token] = []
    n = len(sql)
    i = 0
    depth = 0
    while i < n:
        ch = sql[i]
        if ch in _WHITESPACE:
            i += 1
            continue
        if sql.startswith("--", i):
            while i < n and sql[i] not in "\r\n":
                i += 1
            continue
        if sql.startswith("/*", i):
            i = _scan_block_comment(sql, i)
            continue

        start = i
        token_depth = depth
        if ch in "Nn" and i + 1 < n and sql[i + 1] == "'":
            kind = TokenKind.STRING
            end = _scan_delimited(sql, i + 2, "'", "string")
        elif ch == "'":
            kind = TokenKind.STRING
            end = _scan_delimited(sql, i + 1, "'", "string")
        elif ch == "[":
            kind = TokenKind.QUOTED_IDENT
            end = _scan_delimited(sql, i + 1, "]", "quoted identifier")
        elif ch == '"':
            kind = TokenKind.QUOTED_IDENT
            end = _scan_delimited(sql, i + 1, '"', "quoted identifier")
        elif ch in _ASCII_DIGITS or (ch == "." and i + 1 < n and sql[i + 1] in _ASCII_DIGITS):
            kind = TokenKind.NUMBER
            end = _scan_number(sql, i)
        elif _is_word_start(ch):
            kind = TokenKind.WORD
            end = i + 1
            while end < n and _is_word_char(sql[end]):
                end += 1
        elif ch == ";":
            kind = TokenKind.SEMICOLON
            end = i + 1
        else:
            kind = TokenKind.OTHER
            end = i + 1
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
                token_depth = depth

        tokens.append(Token(kind, sql[start:end], start, end, token_depth))
        i = end
    return tokens
