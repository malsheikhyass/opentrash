"""Tests for opentrash.cache.master_index."""

import pandas as pd

from opentrash.cache.master_index import (
    build_master_index,
    files_for_route,
)


def _vd_indexes():
    """Two days, two vehicles each — small enough to verify by hand."""
    return pd.DataFrame([
        # In bbox of route A only
        {"day": "2026-01-18", "vehicle_id": "v1",
         "lat_min": 32.70, "lat_max": 32.72, "lon_min": -117.20, "lon_max": -117.18,
         "rows": 10, "min_local": pd.Timestamp("2026-01-18 06:00"),
         "max_local": pd.Timestamp("2026-01-18 14:00"),
         "file_path": "/cache/2026-01-18/v1.parquet"},
        # Spans both A and B
        {"day": "2026-01-18", "vehicle_id": "v2",
         "lat_min": 32.70, "lat_max": 32.85, "lon_min": -117.25, "lon_max": -117.10,
         "rows": 20, "min_local": pd.Timestamp("2026-01-18 06:00"),
         "max_local": pd.Timestamp("2026-01-18 14:00"),
         "file_path": "/cache/2026-01-18/v2.parquet"},
        # Far away — matches no route
        {"day": "2026-01-19", "vehicle_id": "v3",
         "lat_min": 40.00, "lat_max": 40.01, "lon_min": -75.00, "lon_max": -74.99,
         "rows": 5, "min_local": pd.Timestamp("2026-01-19 06:00"),
         "max_local": pd.Timestamp("2026-01-19 07:00"),
         "file_path": "/cache/2026-01-19/v3.parquet"},
    ])


def _route_bboxes():
    return pd.DataFrame([
        {"route_id": "A", "rte_min_lon": -117.21, "rte_min_lat": 32.69,
         "rte_max_lon": -117.17, "rte_max_lat": 32.73},
        {"route_id": "B", "rte_min_lon": -117.13, "rte_min_lat": 32.80,
         "rte_max_lon": -117.05, "rte_max_lat": 32.90},
    ])


def test_master_index_assigns_only_overlapping_routes():
    master = build_master_index(_vd_indexes(), _route_bboxes())
    # v1 overlaps A only; v2 overlaps both A and B; v3 overlaps none.
    pairs = set(zip(master["vehicle_id"], master["route_id"], strict=True))
    assert ("v1", "A") in pairs
    assert ("v1", "B") not in pairs
    assert ("v2", "A") in pairs
    assert ("v2", "B") in pairs
    assert all(vid != "v3" for vid in master["vehicle_id"])
    # day column carried through
    assert set(master["day"]) == {"2026-01-18"}


def test_master_index_empty_inputs():
    master = build_master_index(pd.DataFrame(), pd.DataFrame())
    assert master.empty


def test_files_for_route_filters_by_day_window():
    master = build_master_index(_vd_indexes(), _route_bboxes())
    files = files_for_route(master, "A", start_day="2026-01-18", end_day="2026-01-18")
    assert sorted(files) == [
        "/cache/2026-01-18/v1.parquet",
        "/cache/2026-01-18/v2.parquet",
    ]
    # Window with no matches
    none = files_for_route(master, "A", start_day="2026-02-01")
    assert none == []
