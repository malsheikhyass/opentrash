"""Tests for opentrash.engine.segments.

Uses synthetic enriched pings — small DataFrames hand-built to represent
specific workday scenarios. No DuckDB, no real GPS, no network.
"""


import numpy as np
import pandas as pd
import pytest

from opentrash.engine.config import DEFAULT_CONFIG, EngineConfig
from opentrash.engine.segments import (
    TIMELINE_COLUMNS,
    _assign_load_numbers,
    _cumulative_miles,
    _haversine_miles,
    build_all_segments,
    build_timeline,
    derive_phase_per_ping,
    flag_choreography_violations,
)


# ---------------------------------------------------------------------------
# Helpers for building synthetic enriched pings
# ---------------------------------------------------------------------------
def _ts(s: str) -> pd.Timestamp:
    """Local-time Timestamp helper."""
    return pd.Timestamp(s)


def _enriched_pings(rows: list[dict]) -> pd.DataFrame:
    """Build an enriched-pings DataFrame from a list of row dicts.

    Each row provides any subset of enriched columns; missing columns get
    sensible defaults: vehicle 100001, dt_utc = dt_local, speed_mph=5.0,
    route_id=None, apn=None, in_landfill=False, at_depot=False.
    """
    defaults = {
        "vehicle_id": "100001",
        "speed_mph": 5.0,
        "route_id": None,
        "apn": None,
        "in_landfill": False,
        "at_depot": False,
    }
    out = []
    for r in rows:
        row = {**defaults, **r}
        if "dt_utc" not in row and "dt_local" in row:
            row["dt_utc"] = row["dt_local"]
        row.setdefault("lat", 32.7)
        row.setdefault("lon", -117.2)
        out.append(row)
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Haversine math (pure unit tests, no DuckDB)
# ---------------------------------------------------------------------------
def test_haversine_zero_distance():
    a = np.array([32.7])
    miles = _haversine_miles(a, np.array([-117.2]), a, np.array([-117.2]))
    assert miles[0] == pytest.approx(0.0)


def test_haversine_known_distance():
    """Distance between LAX (33.9425, -118.4081) and JFK (40.6413, -73.7781)
    is roughly 2475 miles. Allow generous tolerance for great-circle math."""
    lax_lat = np.array([33.9425])
    lax_lon = np.array([-118.4081])
    jfk_lat = np.array([40.6413])
    jfk_lon = np.array([-73.7781])
    miles = _haversine_miles(lax_lat, lax_lon, jfk_lat, jfk_lon)
    assert 2450 <= miles[0] <= 2500


def test_cumulative_miles_empty_or_single():
    assert _cumulative_miles(np.array([]), np.array([])) == 0.0
    assert _cumulative_miles(np.array([32.7]), np.array([-117.2])) == 0.0


def test_cumulative_miles_three_pings():
    # Three pings along a short line in the example region.
    lats = np.array([32.700, 32.705, 32.710])
    lons = np.array([-117.200, -117.200, -117.200])
    miles = _cumulative_miles(lats, lons)
    # ~0.35 miles per 0.005 deg latitude (~0.69 miles for the pair-sum)
    assert 0.5 < miles < 1.0


# ---------------------------------------------------------------------------
# Phase derivation
# ---------------------------------------------------------------------------
def test_derive_phase_depot_overrides_everything():
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True,
         "in_landfill": True, "route_id": "A", "speed_mph": 1.0},
    ])
    out = derive_phase_per_ping(df)
    assert out["phase"].iloc[0] == "depot"


def test_derive_phase_landfill_overrides_collection():
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 10:00:00"),
         "in_landfill": True, "route_id": "A", "speed_mph": 2.0},
    ])
    out = derive_phase_per_ping(df)
    assert out["phase"].iloc[0] == "landfill"


def test_derive_phase_collection_when_slow_on_route():
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 08:00:00"),
         "route_id": "A", "speed_mph": 5.0},
    ])
    out = derive_phase_per_ping(df)
    assert out["phase"].iloc[0] == "collection"


def test_derive_phase_windshield_when_fast_on_route():
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 08:00:00"),
         "route_id": "A", "speed_mph": 30.0},
    ])
    out = derive_phase_per_ping(df)
    assert out["phase"].iloc[0] == "windshield"


def test_derive_phase_windshield_when_no_route_no_flags():
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 08:00:00"),
         "route_id": None, "speed_mph": 30.0},
    ])
    out = derive_phase_per_ping(df)
    assert out["phase"].iloc[0] == "windshield"


# ---------------------------------------------------------------------------
# Timeline construction: simple cases
# ---------------------------------------------------------------------------
def test_empty_input_returns_empty_timeline():
    df = _enriched_pings([])
    out = build_timeline(df)
    assert list(out.columns) == list(TIMELINE_COLUMNS)
    assert len(out) == 0


def test_one_load_full_choreography():
    """Depot -> windshield -> collection -> dump -> depot. One clean load."""
    df = _enriched_pings([
        # depot dwell (start)
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 06:05:00"), "at_depot": True, "speed_mph": 0.0},
        # windshield to route
        {"dt_local": _ts("2026-01-18 06:30:00"), "speed_mph": 30.0,
         "lat": 32.7, "lon": -117.1},
        # collection on route A
        {"dt_local": _ts("2026-01-18 07:00:00"), "route_id": "A", "speed_mph": 5.0,
         "lat": 32.71, "lon": -117.15},
        {"dt_local": _ts("2026-01-18 09:00:00"), "route_id": "A", "speed_mph": 5.0,
         "lat": 32.72, "lon": -117.16},
        # windshield to landfill
        {"dt_local": _ts("2026-01-18 09:30:00"), "speed_mph": 35.0,
         "lat": 32.6, "lon": -117.05},
        # dump
        {"dt_local": _ts("2026-01-18 10:00:00"), "in_landfill": True, "speed_mph": 1.0,
         "lat": 32.5, "lon": -117.0},
        # windshield home
        {"dt_local": _ts("2026-01-18 10:30:00"), "speed_mph": 40.0,
         "lat": 32.6, "lon": -117.05},
        # depot return
        {"dt_local": _ts("2026-01-18 11:00:00"), "at_depot": True, "speed_mph": 0.0},
    ])
    out = build_timeline(df)
    types = out["segment_type"].tolist()
    assert types == [
        "depot_departure", "windshield", "collection",
        "windshield", "dump", "windshield", "depot_arrival",
    ]
    # Load numbers: depots are NaN, the rest are load 1
    loads = out["load_number"].tolist()
    assert pd.isna(loads[0])                 # depot_departure
    assert all(ln == 1.0 for ln in loads[1:6])
    assert pd.isna(loads[6])                 # depot_arrival
    # Collection is on route A
    coll = out[out["segment_type"] == "collection"].iloc[0]
    assert coll["route_id"] == "A"


def test_two_load_day():
    """Two clean load cycles. Second load should be load_number=2."""
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        # Load 1
        {"dt_local": _ts("2026-01-18 07:00:00"), "route_id": "A", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 09:00:00"), "in_landfill": True, "speed_mph": 1.0},
        # Load 2
        {"dt_local": _ts("2026-01-18 10:00:00"), "route_id": "B", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 12:00:00"), "in_landfill": True, "speed_mph": 1.0},
        {"dt_local": _ts("2026-01-18 13:00:00"), "at_depot": True, "speed_mph": 0.0},
    ])
    out = build_timeline(df)
    # Find the two dumps; check they have load 1 and load 2
    dumps = out[out["segment_type"] == "dump"].sort_values("start_dt_local")
    assert dumps["load_number"].tolist() == [1.0, 2.0]
    # Find collections; same loads
    collections = out[out["segment_type"] == "collection"].sort_values("start_dt_local")
    assert collections["load_number"].tolist() == [1.0, 2.0]
    assert collections["route_id"].tolist() == ["A", "B"]


def test_collection_route_switch_creates_two_segments():
    """A→B mid-collection produces two segments, not one."""
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 07:00:00"), "route_id": "A", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 07:30:00"), "route_id": "B", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 08:00:00"), "in_landfill": True, "speed_mph": 1.0},
    ])
    out = build_timeline(df)
    collections = out[out["segment_type"] == "collection"]
    assert len(collections) == 2
    assert collections["route_id"].tolist() == ["A", "B"]
    # Both belong to load 1
    assert collections["load_number"].tolist() == [1.0, 1.0]


# ---------------------------------------------------------------------------
# Mileage attached to segments
# ---------------------------------------------------------------------------
def test_segment_mileage_nonzero_when_moving():
    """A windshield segment with movement gets positive miles."""
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 06:30:00"), "speed_mph": 30.0,
         "lat": 32.70, "lon": -117.20},
        {"dt_local": _ts("2026-01-18 06:35:00"), "speed_mph": 30.0,
         "lat": 32.75, "lon": -117.10},
        {"dt_local": _ts("2026-01-18 09:00:00"), "in_landfill": True, "speed_mph": 1.0},
    ])
    out = build_timeline(df)
    windshield = out[out["segment_type"] == "windshield"].iloc[0]
    assert windshield["miles"] > 5.0       # ~7-9 miles for ~0.05 deg lat + 0.10 deg lon


def test_mileage_inflation_applied():
    """mileage_inflation_pct multiplies every segment's miles."""
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 06:30:00"), "speed_mph": 30.0,
         "lat": 32.70, "lon": -117.20},
        {"dt_local": _ts("2026-01-18 06:35:00"), "speed_mph": 30.0,
         "lat": 32.75, "lon": -117.10},
        {"dt_local": _ts("2026-01-18 09:00:00"), "in_landfill": True, "speed_mph": 1.0},
    ])
    out_clean = build_timeline(df, config=EngineConfig(mileage_inflation_pct=0.0))
    out_inflated = build_timeline(df, config=EngineConfig(mileage_inflation_pct=0.10))
    ws_clean = out_clean[out_clean["segment_type"] == "windshield"]["miles"].iloc[0]
    ws_inflated = out_inflated[out_inflated["segment_type"] == "windshield"]["miles"].iloc[0]
    assert ws_inflated == pytest.approx(ws_clean * 1.10, rel=0.001)


def test_segment_mileage_zero_when_stationary():
    """Depot dwell with same lat/lon has miles ~= 0."""
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0,
         "lat": 32.80, "lon": -117.10},
        {"dt_local": _ts("2026-01-18 06:05:00"), "at_depot": True, "speed_mph": 0.0,
         "lat": 32.80, "lon": -117.10},
        {"dt_local": _ts("2026-01-18 06:30:00"), "speed_mph": 30.0,
         "lat": 32.75, "lon": -117.20},
        {"dt_local": _ts("2026-01-18 09:00:00"), "in_landfill": True, "speed_mph": 1.0},
    ])
    out = build_timeline(df)
    depot_dep = out[out["segment_type"] == "depot_departure"].iloc[0]
    assert depot_dep["miles"] == pytest.approx(0.0, abs=0.01)


def test_segment_duration_in_seconds():
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 06:05:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 06:30:00"), "speed_mph": 30.0},
        {"dt_local": _ts("2026-01-18 09:00:00"), "in_landfill": True, "speed_mph": 1.0},
    ])
    out = build_timeline(df)
    depot_dep = out[out["segment_type"] == "depot_departure"].iloc[0]
    assert depot_dep["duration_seconds"] == pytest.approx(5 * 60, abs=1.0)


# ---------------------------------------------------------------------------
# Choreography violations
# ---------------------------------------------------------------------------
def test_violations_empty_when_clean():
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 07:00:00"), "route_id": "A", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 09:00:00"), "in_landfill": True, "speed_mph": 1.0},
        {"dt_local": _ts("2026-01-18 10:00:00"), "at_depot": True, "speed_mph": 0.0},
    ])
    timeline = build_timeline(df)
    violations = flag_choreography_violations(timeline)
    assert len(violations) == 0


def test_violation_loaded_depot_return():
    """Day ends on a collection without a closing dump."""
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 07:00:00"), "route_id": "A", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 10:00:00"), "at_depot": True, "speed_mph": 0.0},
    ])
    timeline = build_timeline(df)
    violations = flag_choreography_violations(timeline)
    types = violations["violation_type"].tolist()
    assert "loaded_depot_return" in types


def test_violation_loaded_depot_departure():
    """Day starts with a dump (no preceding collection)."""
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 06:30:00"), "in_landfill": True, "speed_mph": 1.0},
        {"dt_local": _ts("2026-01-18 07:30:00"), "route_id": "A", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 09:00:00"), "in_landfill": True, "speed_mph": 1.0},
        {"dt_local": _ts("2026-01-18 10:00:00"), "at_depot": True, "speed_mph": 0.0},
    ])
    timeline = build_timeline(df)
    violations = flag_choreography_violations(timeline)
    types = violations["violation_type"].tolist()
    assert "loaded_depot_departure" in types


def test_violation_overrun_loads():
    """More than 3 loads → overrun flag."""
    rows = [{"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0}]
    base = pd.Timestamp("2026-01-18 07:00:00")
    for i in range(4):                      # 4 loads
        rows.append({"dt_local": base + pd.Timedelta(hours=2 * i),
                     "route_id": "A", "speed_mph": 5.0})
        rows.append({"dt_local": base + pd.Timedelta(hours=2 * i, minutes=30),
                     "in_landfill": True, "speed_mph": 1.0})
    rows.append({"dt_local": _ts("2026-01-18 16:00:00"),
                 "at_depot": True, "speed_mph": 0.0})
    df = _enriched_pings(rows)
    timeline = build_timeline(df)
    violations = flag_choreography_violations(timeline)
    types = violations["violation_type"].tolist()
    assert "overrun_loads" in types


def test_violation_mid_load_route_switch():
    """A single load touches multiple route_ids."""
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 07:00:00"), "route_id": "A", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 07:30:00"), "route_id": "B", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 09:00:00"), "in_landfill": True, "speed_mph": 1.0},
        {"dt_local": _ts("2026-01-18 10:00:00"), "at_depot": True, "speed_mph": 0.0},
    ])
    timeline = build_timeline(df)
    violations = flag_choreography_violations(timeline)
    types = violations["violation_type"].tolist()
    assert "mid_load_route_switch" in types


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def test_build_all_segments_writes_to_correct_layout(tmp_path):
    enriched_dir = tmp_path / "enriched" / "2026-01-18"
    enriched_dir.mkdir(parents=True)
    df = _enriched_pings([
        {"dt_local": _ts("2026-01-18 06:00:00"), "at_depot": True, "speed_mph": 0.0},
        {"dt_local": _ts("2026-01-18 07:00:00"), "route_id": "A", "speed_mph": 5.0},
        {"dt_local": _ts("2026-01-18 09:00:00"), "in_landfill": True, "speed_mph": 1.0},
        {"dt_local": _ts("2026-01-18 10:00:00"), "at_depot": True, "speed_mph": 0.0},
    ])
    enriched_path = enriched_dir / "100001.parquet"
    df.to_parquet(enriched_path, index=False)

    out = build_all_segments(enriched_path, tmp_path / "segments")
    assert out["timeline"].exists()
    assert out["violations"].exists()
    assert out["timeline"].parent.name == "2026-01-18"
    assert out["timeline"].name == "100001.parquet"

    timeline = pd.read_parquet(out["timeline"])
    assert list(timeline.columns) == list(TIMELINE_COLUMNS)
    assert len(timeline) >= 4               # depot_dep, collection, dump, depot_arr


def test_build_all_segments_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_all_segments(tmp_path / "nope.parquet", tmp_path / "segments")


# ---------------------------------------------------------------------------
# Config knob
# ---------------------------------------------------------------------------
def test_config_has_mileage_inflation_knob():
    assert hasattr(DEFAULT_CONFIG, "mileage_inflation_pct")
    assert DEFAULT_CONFIG.mileage_inflation_pct == 0.0      # default is honest


def test_load_numbers_helper_directly():
    """Direct exercise of _assign_load_numbers for a hand-built timeline."""
    timeline = pd.DataFrame([
        {"segment_type": "depot_departure", "load_number": None},
        {"segment_type": "windshield", "load_number": None},
        {"segment_type": "collection", "load_number": None},
        {"segment_type": "dump", "load_number": None},
        {"segment_type": "collection", "load_number": None},
        {"segment_type": "dump", "load_number": None},
        {"segment_type": "depot_arrival", "load_number": None},
    ])
    out = _assign_load_numbers(timeline)
    loads = out["load_number"].tolist()
    assert pd.isna(loads[0])
    assert loads[1] == 1.0
    assert loads[2] == 1.0
    assert loads[3] == 1.0
    assert loads[4] == 2.0
    assert loads[5] == 2.0
    assert pd.isna(loads[6])
