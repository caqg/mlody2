"""mlody.core.sql — SQL query engine for Parquet-backed analytical queries.

This package exposes ``mlody_query`` and ``MlodyQueryError`` as the public API
for running DuckDB-backed SQL over pre-resolved Parquet file paths.

Usage examples::

    from mlody.core.sql import mlody_query, MlodyQueryError

    # (a) WHERE-only query — SELECT * is injected automatically:
    table = mlody_query(
        paths="/data/train.parquet",
        query="WHERE loss < 0.5",
    )

    # (b) Explicit SELECT query — passed through unchanged:
    table = mlody_query(
        paths="/data/train.parquet",
        query="SELECT loss, epoch FROM read_parquet('/data/train.parquet') WHERE epoch > 5",
    )

    # Error handling:
    try:
        table = mlody_query(paths="/data/train.parquet", query="WHERE bad_col = 1")
    except MlodyQueryError as err:
        print(err.columns)   # actual schema columns
        print(err.cause)     # underlying DuckDB exception

Public re-exports: ``mlody_query``, ``MlodyQueryError``.
"""

from mlody.core.sql.sql_query import (
    MlodyQueryError as MlodyQueryError,
    mlody_query as mlody_query,
)
