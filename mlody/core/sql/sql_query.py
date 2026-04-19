"""mlody SQL query engine: DuckDB-backed analytical queries over Parquet files.

All implementation is in this single module.  Public surface:

- ``mlody_query(paths, query) -> pa.Table``
- ``MlodyQueryError``
- ``_normalize_paths`` (private, tested directly)
- ``_read_columns`` (private, tested directly)
- ``_build_query`` (private, tested directly)
"""

from __future__ import annotations

import glob as glob_module
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import sqlglot

# sqlglot.exp is an alias for sqlglot.expressions defined in sqlglot/__init__.py.
# Import sqlglot first, then access exp through the module attribute — importing
# 'sqlglot.exp' directly fails because there is no sqlglot/exp.py file on disk.
import sqlglot.expressions as _sqlglot_expressions

# ---------------------------------------------------------------------------
# MlodyQueryError  (tasks 2.1–2.3, D-7)
# ---------------------------------------------------------------------------


class MlodyQueryError(Exception):
    """Structured exception raised by ``mlody_query`` on any failure.

    All four fields are always populated.  ``cause`` is set as the Python
    exception chain via ``raise MlodyQueryError(...) from cause``, which also
    sets ``__cause__`` for traceback display.

    Attributes:
        query: The original SQL string supplied by the caller.
        expanded_query: The SQL string after SELECT injection (may equal
            ``query`` if no injection was applied).
        columns: Column names read from the Parquet schema; empty list if
            schema could not be read.
        cause: The underlying exception (DuckDB, sqlglot, or Python).
    """

    def __init__(
        self,
        *,
        query: str,
        expanded_query: str,
        columns: list[str],
        cause: BaseException,
    ) -> None:
        super().__init__(str(cause))
        self.query = query
        self.expanded_query = expanded_query
        self.columns = columns
        self.cause = cause

    def __str__(self) -> str:
        cols_repr = ", ".join(self.columns) if self.columns else "<none>"
        return (
            f"MlodyQueryError:\n"
            f"  original query:  {self.query!r}\n"
            f"  expanded query:  {self.expanded_query!r}\n"
            f"  available cols:  [{cols_repr}]\n"
            f"  cause:           {self.cause!r}"
        )


# ---------------------------------------------------------------------------
# Path normalization  (tasks 3.1–3.4, D-3)
# ---------------------------------------------------------------------------


def _normalize_paths(paths: str | Path | list[str | Path]) -> str:
    """Convert paths to a DuckDB ``read_parquet(...)`` argument fragment.

    Returns a string suitable for embedding inside ``read_parquet(<fragment>)``
    in a SQL query.

    - Single ``str`` → ``'<string>'`` (single-quoted literal)
    - Single ``Path`` → ``'<absolute_path>'``
    - ``list`` → ``['<path0>', '<path1>', ...]`` (DuckDB list literal)

    Args:
        paths: A single path (str or Path) or a list of paths.

    Returns:
        DuckDB-compatible SQL fragment for use in ``read_parquet(...)``.
    """
    if isinstance(paths, list):
        items = ", ".join(f"'{Path(p).resolve() if isinstance(p, Path) else p}'" for p in paths)
        return f"[{items}]"
    if isinstance(paths, Path):
        return f"'{paths.resolve()}'"
    # str: use as-is (may be a glob or an absolute/relative path)
    return f"'{paths}'"


# ---------------------------------------------------------------------------
# Schema reading for diagnostics  (tasks 4.1–4.4, D-4)
# ---------------------------------------------------------------------------


def _read_columns(paths: str | Path | list[str | Path]) -> list[str]:
    """Read column names from Parquet file metadata (schema only, no row data).

    For a glob string, expands the glob and reads the first match.  For a
    list, reads the first element.  For a Path, resolves to absolute string.

    All exceptions are caught and an empty list returned — schema-read failure
    must never mask the original query error.

    Args:
        paths: Same type accepted by ``mlody_query``.

    Returns:
        List of column name strings; ``[]`` if schema cannot be read.
    """
    try:
        if isinstance(paths, list):
            if not paths:
                return []
            target = str(Path(paths[0]).resolve()) if isinstance(paths[0], Path) else str(paths[0])
        elif isinstance(paths, Path):
            target = str(paths.resolve())
        else:
            # str: may be a glob — expand and take first match
            matches = glob_module.glob(paths)
            if not matches:
                return []
            target = matches[0]

        schema = pq.read_schema(target)
        return list(schema.names)
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# SELECT injection  (tasks 5.1–5.4, D-2, D-6)
# ---------------------------------------------------------------------------


def _build_query(query: str, normalized_paths: str) -> str:
    """Build the final SQL to execute, injecting FROM (and SELECT * if needed).

    Three cases, determined by sqlglot AST inspection:

    1. ``WITH`` (CTE) — passed through unchanged; the caller owns all clauses.
    2. ``SELECT`` with a ``FROM`` clause — passed through unchanged; the caller
       explicitly addressed the data source.
    3. ``SELECT`` **without** a ``FROM`` clause (e.g. ``SELECT count(*) WHERE
       x=1``) — ``FROM read_parquet(<paths>)`` is injected into the AST using
       sqlglot so that column-position semantics are preserved.
    4. Bare clause (``WHERE …``, ``GROUP BY …``) or unparseable fragment —
       ``SELECT * FROM read_parquet(<paths>)`` is prepended as a string.

    Args:
        query: The caller-supplied SQL string.
        normalized_paths: The output of ``_normalize_paths``, embedded in the
            injected FROM clause when required.

    Returns:
        The SQL string to hand to DuckDB.
    """
    try:
        ast = sqlglot.parse_one(query, dialect="duckdb")
    except Exception:  # noqa: BLE001
        # sqlglot could not parse the query as a complete statement (e.g. a
        # bare WHERE clause).  Prepend SELECT * FROM so DuckDB receives a
        # syntactically complete query.
        return f"SELECT * FROM read_parquet({normalized_paths}) {query}"

    if isinstance(ast, _sqlglot_expressions.With):
        # CTE: the caller owns the full statement; pass through unchanged.
        return query

    if isinstance(ast, _sqlglot_expressions.Select):
        if ast.find(_sqlglot_expressions.From) is not None:
            # Caller supplied a FROM clause — pass through unchanged.
            return query
        # SELECT without FROM: inject FROM using sqlglot AST so column
        # positions and aliases are preserved (e.g. SELECT count(*) WHERE …).
        from_expr = f"read_parquet({normalized_paths})"
        return ast.from_(from_expr).sql(dialect="duckdb")

    # Bare clause parsed successfully (unusual): inject SELECT *.
    return f"SELECT * FROM read_parquet({normalized_paths}) {query}"


# ---------------------------------------------------------------------------
# mlody_query — public entry point  (tasks 6.1–6.4)
# ---------------------------------------------------------------------------


def mlody_query(
    paths: str | Path | list[str | Path],
    query: str,
) -> pa.Table:
    """Execute a SQL query over one or more Parquet files via in-memory DuckDB.

    Normalizes ``paths``, optionally injects a SELECT clause, executes the
    query, and returns the full result as a ``pyarrow.Table``.  An empty
    result (zero rows) is returned as a ``pa.Table`` with the correct schema —
    never ``None`` or an empty list.

    All DuckDB and sqlglot exceptions are wrapped in ``MlodyQueryError`` before
    propagating to the caller.

    Args:
        paths: Pre-resolved Parquet path(s).  May be:
            - A single glob string (e.g. ``"data/*.parquet"``)
            - A single ``pathlib.Path`` object
            - A list of ``str`` or ``Path`` objects
        query: A SQL string.  If it does not begin with ``SELECT`` or ``WITH``
            (as determined by sqlglot), ``SELECT * FROM read_parquet(<paths>)``
            is prepended automatically.

    Returns:
        A ``pyarrow.Table`` containing the query result.

    Raises:
        MlodyQueryError: For any DuckDB error, sqlglot parse error, or other
            failure during query execution.  Never raises raw DuckDB or
            sqlglot exceptions.
    """
    normalized = _normalize_paths(paths)
    columns = _read_columns(paths)

    # _build_query may raise MlodyQueryError for sqlglot parse failures.
    expanded_sql = _build_query(query, normalized)

    try:
        with duckdb.connect() as conn:
            # .arrow() returns a RecordBatchReader in DuckDB 1.5+; call
            # .read_all() to materialise it into a pa.Table as required.
            result: pa.Table = conn.execute(expanded_sql).arrow().read_all()
        return result
    except MlodyQueryError:
        # Re-raise parse errors from _build_query unchanged.
        raise
    except Exception as exc:  # noqa: BLE001
        raise MlodyQueryError(
            query=query,
            expanded_query=expanded_sql,
            columns=columns,
            cause=exc,
        ) from exc
