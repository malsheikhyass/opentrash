"""Tests for opentrash.core.duckdb_session.

The spatial extension is downloaded from DuckDB's extension server on first
use. If the test environment can't reach that server, we skip cleanly rather
than fail — real users on real networks will have no trouble.
"""

import pytest

from opentrash.core.duckdb_session import connect


def _connect_or_skip():
    """Connect, or skip the test if spatial extension can't be downloaded."""
    try:
        return connect()
    except Exception as e:
        if "extensions.duckdb.org" in str(e) or "Failed to download" in str(e):
            pytest.skip("DuckDB spatial extension unreachable in this environment")
        raise


def test_connect_returns_working_connection():
    con = _connect_or_skip()
    try:
        row = con.execute("SELECT 1 AS x").fetchone()
        assert row[0] == 1
    finally:
        con.close()


def test_spatial_extension_is_loaded():
    con = _connect_or_skip()
    try:
        # ST_AsText is a spatial function — if the extension is loaded, this works.
        row = con.execute("SELECT ST_AsText(ST_Point(1.0, 2.0)) AS pt").fetchone()
        assert "POINT" in row[0].upper()
    finally:
        con.close()


def test_in_memory_is_default():
    """Two separate calls to connect() create independent in-memory DBs."""
    con1 = _connect_or_skip()
    con2 = _connect_or_skip()
    try:
        con1.execute("CREATE TABLE t (x INTEGER)")
        con1.execute("INSERT INTO t VALUES (1)")
        # con2 should not see con1's table.
        tables = con2.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 't'"
        ).fetchall()
        assert tables == []
    finally:
        con1.close()
        con2.close()
