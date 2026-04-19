"""Hand-rolled recursive descent parser for mlody traversal path expressions.

Accepts a UTF-8 string containing a traversal expression (may be empty) and
returns a ``PathExpression`` on success, or raises ``TraversalParseError`` on
any syntax error.

This module has **zero mandatory runtime dependencies** outside the Python
standard library (design D-1, CON-002).

Grammar (see ``TRAVERSAL_GRAMMAR_EBNF`` in ``traversal_grammar``):

    traversal_expr  ::= segment*
    segment         ::= field_seg | bracket_seg | recursive_seg
    field_seg       ::= "." IDENT
    bracket_seg     ::= "[" ( INT | STR | "*" ) "]"
    recursive_seg   ::= ".."
    IDENT           ::= [a-zA-Z_][a-zA-Z0-9_]*
    INT             ::= "-"? [0-9]+
    STR             ::= '"' [^"]* '"'

The parser is a direct transliteration of the grammar. Parsing is stateless;
the only state is the cursor position (``_pos``) inside the ``_Parser`` helper
class, which is instantiated fresh for each call to ``parse_traversal_expression``.
"""

from __future__ import annotations

from mlody.core.traversal_grammar import (
    FieldSegment,
    IndexSegment,
    KeySegment,
    PathExpression,
    PathSegment,
    RecursiveDescentSegment,
    SliceSegment,
    SqlSegment,
    TraversalParseError,
    WildcardSegment,
)


class _Parser:
    """Single-use parser instance holding cursor state over the input string."""

    def __init__(self, expr: str) -> None:
        self._expr = expr
        self._pos = 0

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def parse(self) -> PathExpression:
        segments: list[PathSegment] = []
        while self._pos < len(self._expr):
            seg = self._parse_segment()
            segments.append(seg)
        return PathExpression(segments=tuple(segments))

    # ------------------------------------------------------------------
    # Segment dispatch
    # ------------------------------------------------------------------

    def _parse_segment(self) -> PathSegment:
        ch = self._peek()
        if ch == "[":
            return self._parse_bracket_seg()
        if ch == ".":
            return self._parse_dot_seg()
        # Any other character is unexpected (trailing junk, etc.)
        self._fail(f"unexpected character {ch!r}")

    # ------------------------------------------------------------------
    # Bracket segment: "[" ( INT | STR | "*" ) "]"
    # ------------------------------------------------------------------

    def _parse_bracket_seg(self) -> PathSegment:
        start = self._pos
        self._consume("[")  # already peeked "["

        if self._at_end():
            self._fail_at(start, "unterminated '[' — expected integer, quoted key, '*', or slice")

        ch = self._peek()

        # SQL query segment: [@sql <query>]
        if ch == "@":
            return self._parse_sql_seg(start)

        # Wildcard: [*]
        if ch == "*":
            self._pos += 1
            self._expect_close_bracket(start)
            return WildcardSegment()

        # Quoted string key: ["..."]
        if ch == '"':
            key = self._parse_quoted_string(start)
            self._expect_close_bracket(start)
            return KeySegment(key=key)

        # Slice with no start: [:stop], [:stop:step], [::step], [:]
        if ch == ":":
            return self._parse_slice_tail(start, start_val=None)

        # Integer (possibly negative) — could be [INT] or [INT:...] (slice)
        if ch == "-" or ch.isdigit():
            index = self._parse_integer(start)
            if not self._at_end() and self._peek() == ":":
                return self._parse_slice_tail(start, start_val=index)
            self._expect_close_bracket(start)
            return IndexSegment(index=index)

        # Anything else is invalid bracket content
        self._fail_at(
            self._pos,
            (
                f"invalid bracket content starting with {ch!r}; "
                "expected an integer, a quoted string (\"...\"), '*', or a slice (e.g. '1:4')"
            ),
        )

    def _parse_sql_seg(self, bracket_start: int) -> SqlSegment:
        """Parse ``[@sql <query>]`` — SQL query segment.

        Cursor is positioned immediately after the opening ``[``, pointing at
        ``@``.  Consumes ``@sql``, optional whitespace, then the SQL body up to
        the matching ``]`` (nested ``[]`` are balanced so SQL array subscripts
        work).  Returns a ``SqlSegment`` with ``query`` stripped of surrounding
        whitespace.
        """
        keyword = "@sql"
        end = self._pos + len(keyword)
        if self._expr[self._pos : end].lower() != keyword:
            self._fail_at(
                self._pos,
                f"expected '{keyword}' after '['; "
                f"got {self._expr[self._pos:end]!r}",
            )
        self._pos = end  # consume "@sql"

        # Skip optional whitespace between @sql and the query body.
        while not self._at_end() and self._peek() in (" ", "\t"):
            self._pos += 1

        # Consume SQL body, tracking bracket depth so nested [] are handled.
        # depth=1 because we are already inside the outer "[".
        depth = 1
        query_start = self._pos
        while not self._at_end():
            ch = self._peek()
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    break
            self._pos += 1

        if self._at_end():
            self._fail_at(
                bracket_start,
                "unterminated '[@sql' — missing closing ']'",
            )

        query = self._expr[query_start : self._pos].strip()
        self._pos += 1  # consume closing "]"
        return SqlSegment(query=query)

    def _parse_quoted_string(self, bracket_start: int) -> str:
        """Parse a double-quoted string, consuming both delimiters.

        Returns the content between the quotes (no unescaping — the grammar
        forbids embedded double-quotes: ``STR ::= '"' [^"]* '"'``).
        """
        self._consume('"')
        start = self._pos
        while not self._at_end() and self._peek() != '"':
            self._pos += 1
        if self._at_end():
            self._fail_at(
                bracket_start,
                "unterminated string key in bracket — missing closing '\"'",
            )
        content = self._expr[start : self._pos]
        self._consume('"')
        return content

    def _parse_integer(self, bracket_start: int) -> int:
        """Parse an optional '-' followed by one or more digits."""
        start = self._pos
        if self._peek() == "-":
            self._pos += 1
        if self._at_end() or not self._peek().isdigit():
            self._fail_at(
                bracket_start,
                f"invalid bracket content: expected digit after '-'",
            )
        while not self._at_end() and self._peek().isdigit():
            self._pos += 1
        return int(self._expr[start : self._pos])

    def _parse_slice_tail(self, bracket_start: int, start_val: int | None) -> SliceSegment:
        """Parse the remainder of a slice bracket after the optional start integer.

        Cursor must be positioned at the first ``:``.  Consumes ``:`` + optional
        stop INT + optional ``:`` + optional step INT + ``]``.

        Examples (cursor at first ``:`` in each):
            ``:]``         → SliceSegment(start_val, None, None)
            ``:4]``        → SliceSegment(start_val, 4, None)
            ``:4:2]``      → SliceSegment(start_val, 4, 2)
            ``::2]``       → SliceSegment(start_val, None, 2)
        """
        self._consume(":")  # consume first ':'

        # Parse optional stop
        stop_val: int | None = None
        if not self._at_end() and (self._peek() == "-" or self._peek().isdigit()):
            stop_val = self._parse_integer(bracket_start)

        # Parse optional step
        step_val: int | None = None
        if not self._at_end() and self._peek() == ":":
            self._consume(":")
            if not self._at_end() and (self._peek() == "-" or self._peek().isdigit()):
                step_val = self._parse_integer(bracket_start)

        self._expect_close_bracket(bracket_start)
        return SliceSegment(start=start_val, stop=stop_val, step=step_val)

    def _expect_close_bracket(self, bracket_start: int) -> None:
        if self._at_end() or self._peek() != "]":
            self._fail_at(
                bracket_start,
                "unterminated '[' — missing closing ']'",
            )
        self._pos += 1  # consume "]"

    # ------------------------------------------------------------------
    # Dot-prefix segments: ".." (recursive descent) or "." IDENT (field)
    # ------------------------------------------------------------------

    def _parse_dot_seg(self) -> PathSegment:
        """Parse either a RecursiveDescentSegment ('..') or a FieldSegment ('.IDENT')."""
        pos_of_dot = self._pos
        self._consume(".")

        if self._at_end():
            # A bare trailing "." with nothing after it is invalid
            self._fail_at(pos_of_dot, "bare '.' — expected identifier or '..' for recursive descent")

        if self._peek() == ".":
            # Recursive descent ".."
            self._pos += 1
            return RecursiveDescentSegment()

        # Field segment: IDENT must start with [a-zA-Z_]
        ch = self._peek()
        if not (ch.isalpha() or ch == "_"):
            self._fail_at(
                pos_of_dot,
                (
                    f"'.' must be followed by an identifier "
                    f"([a-zA-Z_][a-zA-Z0-9_]*), got {ch!r}"
                ),
            )

        name = self._parse_ident()
        return FieldSegment(name=name)

    def _parse_ident(self) -> str:
        """Parse IDENT: [a-zA-Z_][a-zA-Z0-9_]*."""
        start = self._pos
        while not self._at_end() and (
            self._peek().isalnum() or self._peek() == "_"
        ):
            self._pos += 1
        return self._expr[start : self._pos]

    # ------------------------------------------------------------------
    # Primitive helpers
    # ------------------------------------------------------------------

    def _peek(self) -> str:
        return self._expr[self._pos]

    def _at_end(self) -> bool:
        return self._pos >= len(self._expr)

    def _consume(self, expected: str) -> None:
        """Advance past ``expected`` character (caller guarantees it's there)."""
        self._pos += len(expected)

    def _fail(self, message: str) -> None:
        raise TraversalParseError(message, self._expr, self._pos)

    def _fail_at(self, position: int, message: str) -> None:
        raise TraversalParseError(message, self._expr, position)


def parse_traversal_expression(expr: str) -> PathExpression:
    """Parse a traversal expression string into a ``PathExpression`` AST.

    Args:
        expr: A UTF-8 string containing a traversal expression (may be empty).

    Returns:
        A ``PathExpression`` on success.

    Raises:
        ``TraversalParseError``: on any syntax error, with the input string,
        position of the first error, and a plain-English description.

    This function is stateless and thread-safe.
    """
    parser = _Parser(expr)
    result = parser.parse()

    # After consuming all recognised segments, the cursor must be at the end.
    # If it is not, there is trailing junk that is not part of any valid segment.
    if parser._pos < len(expr):  # noqa: SLF001
        raise TraversalParseError(
            f"unexpected characters starting at position {parser._pos}: "
            f"{expr[parser._pos:]!r}",
            expr,
            parser._pos,
        )
    return result
