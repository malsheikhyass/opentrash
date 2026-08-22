"""Tests for opentrash.patterns.* — config, window, detector, runner, validator.

Uses synthetic enriched pings with known patterns injected. We can verify
detection by constructing a parcel that's visited every Tuesday at 8am for
52 weeks and asserting weekly1 fields match.

DuckDB is used directly (no spatial extension needed), so all tests run
fully in CI without skips.
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from opentrash.patterns.config import DEFAULT_CONFIG, PatternConfig
from opentrash.patterns.detector import (
    PATTERNS_COLUMNS,
    detect_patterns_chunk,
)
from opentrash.patterns.runner import load_patterns, run_patterns
from opentrash.patterns.validator import validate_patterns
from opentrash.patterns.window import compute_window, window_label


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_enriched_parquet(
    rows: list[dict],
    out_path: Path,
) -> Path:
    """Write a small enriched-pings parquet to out_path."""
    defaults = {
        "vehicle_id": "100001",
        "lat": 32.7,
        "lon": -117.2,
        "speed_mph": 4.0,
        "route_id": "A",
        "apn": "P1",
        "in_landfill": False,
        "at_depot": False,
    }
    enriched = []
    for r in rows:
        row = {**defaults, **r}
        row.setdefault("dt_utc", row["dt_local"])
        enriched.append(row)
    df = pd.DataFrame(enriched)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return out_path


def _make_parcels_wkb_parquet(parcels: list[dict], out_path: Path) -> Path:
    """Tiny parcels_wkb parquet (only the columns the detector reads)."""
    df = pd.DataFrame(parcels)
    df.to_parquet(out_path, index=False)
    return out_path


def _weekly_visits(
    apn: str, vehicle: str, dow: int, hour: int,
    start: date, n_weeks: int,
) -> list[dict]:
    """Generate one slow ping per week on the given DOW + hour."""
    rows: list[dict] = []
    # Advance to the first DOW on or after start
    d = start
    while d.weekday() != dow:
        d += timedelta(days=1)
    for _ in range(n_weeks):
        dt = datetime.combine(d, time(hour=hour, minute=0))
        rows.append({
            "apn": apn, "vehicle_id": vehicle,
            "dt_local": pd.Timestamp(dt), "dt_utc": pd.Timestamp(dt),
            "speed_mph": 4.0,
        })
        d += timedelta(days=7)
    return rows


def _biweekly_visits(
    apn: str, vehicle: str, dow: int, hour: int,
    start: date, n_visits: int,
) -> list[dict]:
    """Generate one slow ping every 14 days on the given DOW + hour."""
    rows: list[dict] = []
    d = start
    while d.weekday() != dow:
        d += timedelta(days=1)
    for _ in range(n_visits):
        dt = datetime.combine(d, time(hour=hour, minute=0))
        rows.append({
            "apn": apn, "vehicle_id": vehicle,
            "dt_local": pd.Timestamp(dt), "dt_utc": pd.Timestamp(dt),
            "speed_mph": 4.0,
        })
        d += timedelta(days=14)
    return rows


# ---------------------------------------------------------------------------
# Window helper
# ---------------------------------------------------------------------------
def test_window_past_year_default():
    anchor = date(2026, 6, 1)
    start, end = compute_window("past_year", anchor=anchor)
    assert end == anchor
    assert (end - start).days == 365


def test_window_past_quarter():
    anchor = date(2026, 6, 1)
    start, end = compute_window("past_quarter", anchor=anchor)
    assert (end - start).days == 90


def test_window_custom_with_strings():
    start, end = compute_window(
        "custom", start_date="2025-01-01", end_date="2025-12-31",
    )
    assert start == date(2025, 1, 1)
    assert end == date(2025, 12, 31)


def test_window_custom_requires_both_dates():
    with pytest.raises(ValueError, match="custom"):
        compute_window("custom", start_date="2025-01-01")


def test_window_unknown_period_raises():
    with pytest.raises(ValueError, match="Unknown period"):
        compute_window("past_century")              # type: ignore[arg-type]


def test_window_label_filesystem_friendly():
    label = window_label(date(2025, 3, 1), date(2026, 2, 28))
    assert "/" not in label
    assert label == "2025-03-01_to_2026-02-28"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def test_pattern_config_defaults_sensible():
    cfg = DEFAULT_CONFIG
    assert cfg.stop_bin_minutes == 15
    assert 0 < cfg.weekly_min_regularity < 1
    assert cfg.weekly_min_weeks_ratio == 0.25
    assert cfg.biweekly_min_weeks_ratio == 0.25
    assert cfg.biweekly_gap_target == 14


def test_pattern_config_is_frozen():
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        DEFAULT_CONFIG.weekly_min_regularity = 0.99   # type: ignore[misc]


# ---------------------------------------------------------------------------
# Detector — the headline test: known weekly pattern should be detected
# ---------------------------------------------------------------------------
def test_detector_finds_perfect_weekly_pattern(tmp_path):
    """A parcel visited every Tuesday at 8am for 52 weeks should detect cleanly."""
    parcels = [
        {"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
    ]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")

    rows = _weekly_visits(
        apn="P1", vehicle="V1",
        dow=1,   # Tuesday
        hour=8,
        start=date(2025, 3, 1), n_weeks=52,
    )
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-04" / "V1.parquet")

    con = duckdb.connect(":memory:")
    try:
        result = detect_patterns_chunk(
            con,
            enriched_glob=str(tmp_path / "enriched" / "**" / "*.parquet"),
            parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
            start_date=date(2025, 3, 1),
            end_date=date(2026, 2, 28),
        )
    finally:
        con.close()

    assert len(result) == 1
    row = result.iloc[0]
    assert row["APN"] == "P1"
    assert row["weekly1_vehicle"] == "V1"
    assert row["weekly1_dow"] == "Tue"
    assert row["weekly1_hour"] == 8
    assert row["weekly1_regularity"] >= 0.95         # near-perfect
    assert row["weekly1_visits"] >= 50
    # Route sidecar carried through
    assert row["route_ref"] == "R1"
    assert row["route_org"] == "O1"
    assert row["route_rec"] == "C1"


def test_detector_finds_biweekly_pattern(tmp_path):
    """A parcel visited every other Wednesday for 26 visits should detect biweekly."""
    parcels = [
        {"APN": "P2", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
    ]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")

    rows = _biweekly_visits(
        apn="P2", vehicle="V_REC",
        dow=2,   # Wednesday
        hour=10,
        start=date(2025, 3, 1),
        n_visits=26,
    )
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-05" / "V_REC.parquet")

    con = duckdb.connect(":memory:")
    try:
        result = detect_patterns_chunk(
            con,
            enriched_glob=str(tmp_path / "enriched" / "**" / "*.parquet"),
            parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
            start_date=date(2025, 3, 1),
            end_date=date(2026, 2, 28),
        )
    finally:
        con.close()

    row = result.iloc[0]
    assert row["biweekly_vehicle"] == "V_REC"
    assert row["biweekly_dow"] == "Wed"
    assert row["biweekly_hour"] == 10
    # Biweekly score should clear the default threshold (0.58)
    assert row["biweekly_score"] >= 0.58


def test_detector_top3_vehicles_emerge_from_3_commodities(tmp_path):
    """Three vehicles (ref weekly, org weekly different DOW, rec biweekly)
    visiting one parcel produce the expected weekly1/weekly2/biweekly slots.
    """
    parcels = [
        {"APN": "P3", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
    ]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")

    rows = []
    # Refuse: Monday 7am, every week
    rows += _weekly_visits("P3", "V_REF", dow=0, hour=7,
                           start=date(2025, 3, 1), n_weeks=52)
    # Organics: Tuesday 7am, every week
    rows += _weekly_visits("P3", "V_ORG", dow=1, hour=7,
                           start=date(2025, 3, 1), n_weeks=52)
    # Recycling: Wednesday 9am, every other week
    rows += _biweekly_visits("P3", "V_REC", dow=2, hour=9,
                             start=date(2025, 3, 1), n_visits=26)
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-01" / "all.parquet")

    con = duckdb.connect(":memory:")
    try:
        result = detect_patterns_chunk(
            con,
            enriched_glob=str(tmp_path / "enriched" / "**" / "*.parquet"),
            parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
            start_date=date(2025, 3, 1),
            end_date=date(2026, 2, 28),
        )
    finally:
        con.close()

    row = result.iloc[0]
    weekly_vehicles = {row["weekly1_vehicle"], row["weekly2_vehicle"]}
    assert weekly_vehicles == {"V_REF", "V_ORG"}
    assert row["biweekly_vehicle"] == "V_REC"


def test_detector_no_pattern_when_too_sparse(tmp_path):
    """A parcel visited only twice in a year should NOT register a pattern."""
    parcels = [
        {"APN": "P4", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
    ]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")

    rows = [
        {"apn": "P4", "vehicle_id": "V_X",
         "dt_local": pd.Timestamp("2025-04-01 08:00:00"),
         "dt_utc": pd.Timestamp("2025-04-01 08:00:00"),
         "speed_mph": 4.0},
        {"apn": "P4", "vehicle_id": "V_X",
         "dt_local": pd.Timestamp("2025-09-15 08:00:00"),
         "dt_utc": pd.Timestamp("2025-09-15 08:00:00"),
         "speed_mph": 4.0},
    ]
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-04-01" / "V_X.parquet")

    con = duckdb.connect(":memory:")
    try:
        result = detect_patterns_chunk(
            con,
            enriched_glob=str(tmp_path / "enriched" / "**" / "*.parquet"),
            parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
            start_date=date(2025, 3, 1),
            end_date=date(2026, 2, 28),
        )
    finally:
        con.close()

    row = result.iloc[0]
    # No pattern detected — all weekly/biweekly columns null
    assert pd.isna(row["weekly1_vehicle"])
    assert pd.isna(row["biweekly_vehicle"])
    # But parcel still appears (LEFT JOIN preserves it)
    assert row["APN"] == "P4"


def test_detector_empty_enriched_returns_parcel_rows_with_nulls(tmp_path):
    """No enriched pings → every parcel appears with null pattern columns."""
    parcels = [
        {"APN": "P_alone", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
    ]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")

    # Empty enriched parquet
    empty = pd.DataFrame({
        "vehicle_id": [], "dt_utc": [], "dt_local": [],
        "lat": [], "lon": [], "speed_mph": [],
        "route_id": [], "apn": [], "in_landfill": [], "at_depot": [],
    })
    (tmp_path / "enriched").mkdir()
    empty.to_parquet(tmp_path / "enriched" / "empty.parquet", index=False)

    con = duckdb.connect(":memory:")
    try:
        result = detect_patterns_chunk(
            con,
            enriched_glob=str(tmp_path / "enriched" / "*.parquet"),
            parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
            start_date=date(2025, 3, 1),
            end_date=date(2026, 2, 28),
        )
    finally:
        con.close()

    assert len(result) == 1
    assert result.iloc[0]["APN"] == "P_alone"
    assert pd.isna(result.iloc[0]["weekly1_vehicle"])


def test_detector_chunk_apns_filter_restricts_scope(tmp_path):
    """chunk_apns=['P1'] restricts the chunk; other parcels don't appear."""
    parcels = [
        {"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
        {"APN": "P2", "route_ref": "R2", "route_org": "O2", "route_rec": "C2"},
    ]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")

    rows = _weekly_visits("P1", "V1", 1, 8, date(2025, 3, 1), 52)
    rows += _weekly_visits("P2", "V2", 2, 9, date(2025, 3, 1), 52)
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-01" / "all.parquet")

    con = duckdb.connect(":memory:")
    try:
        result = detect_patterns_chunk(
            con,
            enriched_glob=str(tmp_path / "enriched" / "**" / "*.parquet"),
            parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
            start_date=date(2025, 3, 1),
            end_date=date(2026, 2, 28),
            chunk_apns=["P1"],
        )
    finally:
        con.close()

    assert set(result["APN"].astype(str)) == {"P1"}


def test_detector_invalid_window_raises(tmp_path):
    parcels = [{"APN": "P1", "route_ref": "R", "route_org": "O", "route_rec": "C"}]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")
    (tmp_path / "enriched").mkdir()
    pd.DataFrame({
        "vehicle_id": [], "dt_utc": [], "dt_local": [],
        "lat": [], "lon": [], "speed_mph": [],
        "route_id": [], "apn": [], "in_landfill": [], "at_depot": [],
    }).to_parquet(tmp_path / "enriched" / "x.parquet", index=False)

    con = duckdb.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="end_date"):
            detect_patterns_chunk(
                con,
                enriched_glob=str(tmp_path / "enriched" / "*.parquet"),
                parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
                start_date=date(2026, 1, 1),
                end_date=date(2025, 1, 1),                  # end < start
            )
    finally:
        con.close()


def test_detector_columns_match_canonical_schema(tmp_path):
    parcels = [{"APN": "P1", "route_ref": "R", "route_org": "O", "route_rec": "C"}]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")
    rows = _weekly_visits("P1", "V1", 1, 8, date(2025, 3, 1), 52)
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-04" / "V1.parquet")
    con = duckdb.connect(":memory:")
    try:
        result = detect_patterns_chunk(
            con,
            enriched_glob=str(tmp_path / "enriched" / "**" / "*.parquet"),
            parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
            start_date=date(2025, 3, 1),
            end_date=date(2026, 2, 28),
        )
    finally:
        con.close()
    assert list(result.columns) == list(PATTERNS_COLUMNS)


# ---------------------------------------------------------------------------
# Effective min-weeks (floor + ratio)
# ---------------------------------------------------------------------------
def test_effective_min_weeks_uses_ratio_on_long_window(tmp_path):
    """On a 52-week window with floor=6 and ratio=0.25, effective min = max(6, 13) = 13.

    A parcel with only 10 weeks of weekly visits should fail the threshold
    even though it would have passed v1's flat floor=6.
    """
    parcels = [{"APN": "P1", "route_ref": "R", "route_org": "O", "route_rec": "C"}]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")
    rows = _weekly_visits("P1", "V1", 1, 8, date(2025, 3, 1), n_weeks=10)
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-04" / "V1.parquet")

    con = duckdb.connect(":memory:")
    try:
        result = detect_patterns_chunk(
            con,
            enriched_glob=str(tmp_path / "enriched" / "**" / "*.parquet"),
            parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
            start_date=date(2025, 3, 1),
            end_date=date(2026, 2, 28),
            config=PatternConfig(weekly_min_weeks=6, weekly_min_weeks_ratio=0.25),
        )
    finally:
        con.close()

    # 10 weeks observed; effective threshold = max(6, ceil(0.25 * 53)) = 13.
    # Below threshold -> no weekly pattern detected.
    assert pd.isna(result.iloc[0]["weekly1_vehicle"])


# ---------------------------------------------------------------------------
# Runner — chunked orchestration
# ---------------------------------------------------------------------------
def test_runner_writes_partitioned_chunks(tmp_path):
    parcels = [
        {"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
        {"APN": "P2", "route_ref": "R2", "route_org": "O2", "route_rec": "C2"},
    ]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")
    rows = _weekly_visits("P1", "V1", 1, 8, date(2025, 3, 1), 52)
    rows += _weekly_visits("P2", "V2", 2, 9, date(2025, 3, 1), 52)
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-01" / "all.parquet")

    manifest = run_patterns(
        enriched_root=tmp_path / "enriched",
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        out_root=tmp_path / "patterns",
        period="custom",
        start_date=date(2025, 3, 1),
        end_date=date(2026, 2, 28),
    )

    assert len(manifest) == 2                    # R1 and R2 chunks
    assert all(manifest["ran"])
    # Output files exist
    label = window_label(date(2025, 3, 1), date(2026, 2, 28))
    assert (tmp_path / "patterns" / label / "by_route" / "R1.parquet").exists()
    assert (tmp_path / "patterns" / label / "by_route" / "R2.parquet").exists()


def test_runner_skips_fresh_chunks(tmp_path):
    """Second run on the same inputs skips chunks marked up-to-date."""
    parcels = [{"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"}]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")
    rows = _weekly_visits("P1", "V1", 1, 8, date(2025, 3, 1), 52)
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-04" / "V1.parquet")

    run_patterns(
        enriched_root=tmp_path / "enriched",
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        out_root=tmp_path / "patterns",
        period="custom",
        start_date=date(2025, 3, 1),
        end_date=date(2026, 2, 28),
    )
    # Force the output mtime to be in the future (so newest_input < output)
    import time
    time.sleep(0.05)

    manifest = run_patterns(
        enriched_root=tmp_path / "enriched",
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        out_root=tmp_path / "patterns",
        period="custom",
        start_date=date(2025, 3, 1),
        end_date=date(2026, 2, 28),
    )
    assert all(not r for r in manifest["ran"])     # all skipped


def test_runner_refresh_forces_rerun(tmp_path):
    parcels = [{"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"}]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")
    rows = _weekly_visits("P1", "V1", 1, 8, date(2025, 3, 1), 52)
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-04" / "V1.parquet")

    run_patterns(
        enriched_root=tmp_path / "enriched",
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        out_root=tmp_path / "patterns",
        period="custom",
        start_date=date(2025, 3, 1),
        end_date=date(2026, 2, 28),
    )
    manifest = run_patterns(
        enriched_root=tmp_path / "enriched",
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        out_root=tmp_path / "patterns",
        period="custom",
        start_date=date(2025, 3, 1),
        end_date=date(2026, 2, 28),
        refresh=True,
    )
    assert all(manifest["ran"])


def test_load_patterns_combines_chunks(tmp_path):
    parcels = [
        {"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
        {"APN": "P2", "route_ref": "R2", "route_org": "O2", "route_rec": "C2"},
    ]
    _make_parcels_wkb_parquet(parcels, tmp_path / "parcels_wkb.parquet")
    rows = _weekly_visits("P1", "V1", 1, 8, date(2025, 3, 1), 52)
    rows += _weekly_visits("P2", "V2", 2, 9, date(2025, 3, 1), 52)
    _make_enriched_parquet(rows, tmp_path / "enriched" / "2025-03-01" / "all.parquet")

    run_patterns(
        enriched_root=tmp_path / "enriched",
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        out_root=tmp_path / "patterns",
        period="custom",
        start_date=date(2025, 3, 1),
        end_date=date(2026, 2, 28),
    )
    label = window_label(date(2025, 3, 1), date(2026, 2, 28))
    combined = load_patterns(tmp_path / "patterns", label)
    assert len(combined) == 2
    assert set(combined["APN"].astype(str)) == {"P1", "P2"}


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------
def test_validator_runs_on_a_patterns_frame():
    """The v1 validator returns a ValidationReport with the expected fields."""
    df = pd.DataFrame({c: [None] for c in PATTERNS_COLUMNS})
    df.loc[0, "APN"] = "P1"
    df.loc[0, "weekly1_vehicle"] = "V1"
    df.loc[0, "weekly1_dow"] = "Tue"
    report = validate_patterns(df)
    assert report.total == 1
    assert report.weekly1_count == 1
    assert report.weekly2_count == 0
    assert report.biweekly_count == 0
