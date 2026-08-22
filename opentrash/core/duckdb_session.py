"""DuckDB session management.

DuckDB is the package's compute engine: serverless, runs in-process, and reads
parquet straight off disk with no server to stand up. The ``spatial`` extension
adds geometry types and spatial functions (ST_* ) so we can do spatial work in
SQL when that's the fastest path.

This module centralizes connection setup so every part of the package gets a
connection configured the same way — with the spatial extension ready to use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb


def connect(database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with the spatial extension loaded.

    Parameters
    ----------
    database:
        Path to a DuckDB database file, or ``":memory:"`` (default) for an
        in-memory database. In-memory is the common case: we read parquet from
        disk on demand rather than loading it into a persistent database.

    Returns
    -------
    A ready-to-use DuckDB connection with ``spatial`` installed and loaded.
    """
    import duckdb

    con = duckdb.connect(database)
    # Load the spatial extension, installing it only if it isn't present yet.
    # Trying LOAD first avoids an unnecessary network call on machines where
    # the extension is already installed.
    try:
        con.execute("LOAD spatial;")
    except duckdb.Error:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
    return con
