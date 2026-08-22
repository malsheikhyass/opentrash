"""Tests for opentrash.adapters.gps.geotab.

The live API call is never exercised here. Instead we test:

- the pure helpers (date coercion, speed conversion),
- credential validation (missing creds raise before any network call),
- row normalization (raw ping dicts -> canonical schema, local-day filter),
- Protocol conformance (GeotabAdapter satisfies GPSAdapter structurally).
"""

from datetime import date, datetime

import pytest

from opentrash.adapters.gps.base import GPS_COLUMNS, GPSAdapter
from opentrash.adapters.gps.geotab import (
    GeotabAdapter,
    _as_date,
    _kmh_to_mph,
)


def _pst():
    """Return the US/Pacific pytz timezone. Lazy so [geotab] isn't required."""
    import pytz
    return pytz.timezone("US/Pacific")


# ----- pure helpers -----
def test_as_date_accepts_multiple_types():
    assert _as_date("2025-08-18") == date(2025, 8, 18)
    assert _as_date(date(2025, 8, 18)) == date(2025, 8, 18)
    assert _as_date(datetime(2025, 8, 18, 9, 30)) == date(2025, 8, 18)
    with pytest.raises(TypeError):
        _as_date(12345)


def test_kmh_to_mph():
    assert _kmh_to_mph(0) == 0
    assert _kmh_to_mph(None) == 0
    assert _kmh_to_mph(100) == 62        # 100 km/h ~ 62 mph
    assert _kmh_to_mph(50) == 31


# ----- credential validation -----
def test_missing_credentials_raise(monkeypatch):
    # Ensure no env vars leak in.
    for var in ("GEOTAB_USERNAME", "GEOTAB_PASSWORD", "GEOTAB_DATABASE"):
        monkeypatch.delenv(var, raising=False)
    adapter = GeotabAdapter()
    with pytest.raises(ValueError, match="Missing Geotab credentials"):
        adapter.fetch("100421", "2025-08-18", "2025-08-18")


def test_credentials_from_env(monkeypatch):
    monkeypatch.setenv("GEOTAB_USERNAME", "u")
    monkeypatch.setenv("GEOTAB_PASSWORD", "p")
    monkeypatch.setenv("GEOTAB_DATABASE", "db")
    adapter = GeotabAdapter()
    assert adapter.username == "u"
    assert adapter.database == "db"
    # args win over env
    adapter2 = GeotabAdapter(username="explicit")
    assert adapter2.username == "explicit"


# ----- row normalization (the testable core of fetch) -----
def _window(day="2025-08-18"):
    d = date.fromisoformat(day)
    from_local = _pst().localize(datetime.combine(d, datetime.min.time()))
    to_local = _pst().localize(datetime.combine(d, datetime.max.time().replace(microsecond=0)))
    return from_local, to_local


def test_normalize_rows_happy_path():
    from_local, to_local = _window()
    rows = [
        {"vehicle_id": "100421", "dt_raw": "2025-08-18T17:00:00Z",
         "lat": 32.7, "lon": -117.1, "speed_mph": 20},
        {"vehicle_id": "100421", "dt_raw": "2025-08-18T18:30:00Z",
         "lat": 32.71, "lon": -117.11, "speed_mph": 15},
    ]
    df = GeotabAdapter._normalize_rows(rows, from_local, to_local)
    assert tuple(df.columns) == GPS_COLUMNS
    assert len(df) == 2
    # sorted by dt_utc ascending
    assert df["dt_utc"].is_monotonic_increasing
    # dt_local is Pacific
    assert "Pacific" in str(df["dt_local"].dtype)


def test_normalize_rows_filters_outside_local_day():
    from_local, to_local = _window("2025-08-18")
    # 06:00 UTC on the 18th = 23:00 PT on the 17th -> outside the local day.
    rows = [
        {"vehicle_id": "100421", "dt_raw": "2025-08-18T06:00:00Z",
         "lat": 32.7, "lon": -117.1, "speed_mph": 0},
    ]
    df = GeotabAdapter._normalize_rows(rows, from_local, to_local)
    assert df.empty
    assert tuple(df.columns) == GPS_COLUMNS


def test_normalize_rows_empty_input():
    from_local, to_local = _window()
    df = GeotabAdapter._normalize_rows([], from_local, to_local)
    assert df.empty
    assert tuple(df.columns) == GPS_COLUMNS


# ----- Protocol conformance -----
def test_geotab_adapter_satisfies_protocol():
    adapter = GeotabAdapter(username="u", password="p", database="db")
    assert isinstance(adapter, GPSAdapter)
