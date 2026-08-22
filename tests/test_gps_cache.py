"""Tests for opentrash.cache.gps_cache.

Uses a stub adapter (no network) that returns a canned frame and counts calls,
so we can prove cache-miss writes, cache-hit reads (no second call), the
on-disk layout (YYYYMMDD/vehicle.parquet), refresh, and fleet concatenation.
"""

import pandas as pd

from opentrash.adapters.gps.base import GPS_COLUMNS
from opentrash.cache.gps_cache import (
    cache_path,
    get_gps_day,
    get_gps_fleet_day,
)


class _StubAdapter:
    """A fake GPSAdapter that returns canned pings and counts fetch calls."""

    def __init__(self):
        self.calls = []

    def fetch(self, vehicle, start_date, end_date):
        self.calls.append((vehicle, str(start_date), str(end_date)))
        return pd.DataFrame(
            {
                "vehicle_id": pd.Series([str(vehicle)], dtype="string"),
                "dt_utc": pd.to_datetime(["2025-08-18T17:00:00Z"], utc=True),
                "dt_local": pd.to_datetime(["2025-08-18T10:00:00"]).tz_localize("US/Pacific"),
                "lat": [32.7],
                "lon": [-117.1],
                "speed_mph": [20.0],
            }
        )[list(GPS_COLUMNS)]


def test_cache_path_layout(tmp_path):
    p = cache_path(tmp_path, "100421", "2025-08-18")
    assert p.parent.name == "2025-08-18"      # YYYY-MM-DD directory
    assert p.name == "100421.parquet"


def test_cache_miss_then_hit(tmp_path):
    adapter = _StubAdapter()
    # First call: cache miss -> fetch + write.
    df1 = get_gps_day(adapter, "100421", "2025-08-18", tmp_path)
    assert len(df1) == 1
    assert len(adapter.calls) == 1
    assert cache_path(tmp_path, "100421", "2025-08-18").exists()

    # Second call: cache hit -> read from disk, NO new fetch.
    df2 = get_gps_day(adapter, "100421", "2025-08-18", tmp_path)
    assert len(df2) == 1
    assert len(adapter.calls) == 1            # still 1 — proves cache hit


def test_cache_refresh_forces_refetch(tmp_path):
    adapter = _StubAdapter()
    get_gps_day(adapter, "100421", "2025-08-18", tmp_path)
    get_gps_day(adapter, "100421", "2025-08-18", tmp_path, refresh=True)
    assert len(adapter.calls) == 2            # refresh bypassed the cache


def test_fleet_day_concats_and_caches_each(tmp_path):
    adapter = _StubAdapter()
    fleet = ["100421", "200111", "100999"]
    df = get_gps_fleet_day(adapter, fleet, "2025-08-18", tmp_path)
    assert len(df) == 3                        # one ping per stubbed vehicle
    assert set(df["vehicle_id"]) == set(fleet)
    # one cached file per vehicle
    for v in fleet:
        assert cache_path(tmp_path, v, "2025-08-18").exists()

    # Re-running the fleet pull hits cache for all -> no new fetches.
    calls_before = len(adapter.calls)
    get_gps_fleet_day(adapter, fleet, "2025-08-18", tmp_path)
    assert len(adapter.calls) == calls_before
