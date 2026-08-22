"""Tests for opentrash.adapters.gps.postgres.

Like the Geotab adapter tests, no live database is required. We test:

- credential validation (missing URL raises before connecting),
- the date coercion helper,
- the ``_normalize`` static method (raw DB rows -> canonical schema),
- Protocol conformance.
"""

import pandas as pd
import pytest

from opentrash.adapters.gps.base import GPS_COLUMNS, GPSAdapter
from opentrash.adapters.gps.postgres import PostgresAdapter, _as_date


# ----- pure helpers -----
def test_as_date_accepts_strings_and_dates():
    from datetime import date, datetime
    assert _as_date("2025-08-18") == date(2025, 8, 18)
    assert _as_date(date(2025, 8, 18)) == date(2025, 8, 18)
    assert _as_date(datetime(2025, 8, 18, 9, 30)) == date(2025, 8, 18)
    with pytest.raises(TypeError):
        _as_date(99999)


# ----- credential validation -----
def test_missing_url_raises(monkeypatch):
    monkeypatch.delenv("OPENTRASH_PG_URL", raising=False)
    adapter = PostgresAdapter()
    with pytest.raises(ValueError, match="Missing Postgres URL"):
        adapter.fetch("100421", "2025-08-18", "2025-08-18")


def test_url_from_env(monkeypatch):
    monkeypatch.setenv("OPENTRASH_PG_URL", "postgresql+psycopg2://u:p@h:5432/db")
    adapter = PostgresAdapter()
    assert adapter.url == "postgresql+psycopg2://u:p@h:5432/db"
    # explicit arg wins
    adapter2 = PostgresAdapter(url="postgresql+psycopg2://other:p@h:5432/db")
    assert "other" in adapter2.url


# ----- normalize: the testable core of fetch -----
def test_normalize_happy_path():
    raw = pd.DataFrame({
        "DeviceId": [1, 1, 1],
        "DateTimeUTC_naive": pd.to_datetime([
            "2025-08-18 17:00:00",
            "2025-08-18 17:00:05",
            "2025-08-18 18:30:00",
        ]),
        "Latitude": [32.7, 32.71, 32.72],
        "Longitude": [-117.1, -117.11, -117.12],
        "Speed": [50.0, 0.0, 25.0],   # km/h
    })
    df = PostgresAdapter._normalize(raw, vehicle_name="100421")

    assert tuple(df.columns) == GPS_COLUMNS
    assert len(df) == 3
    assert df["vehicle_id"].iloc[0] == "100421"
    # 50 km/h -> 31 mph (rounded)
    assert df["speed_mph"].iloc[0] == 31
    # dt_utc is UTC-aware
    assert "UTC" in str(df["dt_utc"].dtype)
    # dt_local is Pacific
    assert "Pacific" in str(df["dt_local"].dtype)
    # sorted by dt_utc
    assert df["dt_utc"].is_monotonic_increasing


def test_normalize_empty():
    raw = pd.DataFrame(columns=["DeviceId", "DateTimeUTC_naive", "Latitude", "Longitude", "Speed"])
    df = PostgresAdapter._normalize(raw, vehicle_name="100421")
    assert df.empty
    assert tuple(df.columns) == GPS_COLUMNS


# ----- Protocol conformance -----
def test_postgres_adapter_satisfies_protocol():
    adapter = PostgresAdapter(url="postgresql+psycopg2://u:p@h:5432/db")
    assert isinstance(adapter, GPSAdapter)
