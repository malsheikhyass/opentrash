"""Tests for opentrash.cache.gps_indexes."""

import pandas as pd

from opentrash.adapters.gps.base import GPS_COLUMNS
from opentrash.cache.gps_indexes import (
    VEHICLE_DAY_INDEX_COLUMNS,
    build_all_day_indexes,
    build_day_index,
    summarize_vehicle_day_file,
)


def _make_cache(tmp_path, day, vehicle, n_rows=3):
    """Write a tiny GPS parquet for one vehicle-day into the cache."""
    day_dir = tmp_path / day
    day_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "vehicle_id": pd.Series([vehicle] * n_rows, dtype="string"),
        "dt_utc": pd.to_datetime(
            [f"{day}T17:0{i}:00Z" for i in range(n_rows)], utc=True
        ),
        "dt_local": pd.to_datetime(
            [f"{day}T10:0{i}:00" for i in range(n_rows)]
        ).tz_localize("US/Pacific"),
        "lat": [32.70 + 0.01 * i for i in range(n_rows)],
        "lon": [-117.20 + 0.01 * i for i in range(n_rows)],
        "speed_mph": [10.0 + i for i in range(n_rows)],
    })[list(GPS_COLUMNS)]
    out = day_dir / f"{vehicle}.parquet"
    df.to_parquet(out, index=False)
    return out


def test_summarize_vehicle_day_file(tmp_path):
    p = _make_cache(tmp_path, "2026-01-18", "100421", n_rows=5)
    row = summarize_vehicle_day_file(p)
    assert row["day"] == "2026-01-18"
    assert row["vehicle_id"] == "100421"
    assert row["rows"] == 5
    assert row["lat_min"] == 32.70
    assert row["lat_max"] == 32.70 + 0.01 * 4
    assert row["file_path"].endswith("100421.parquet")


def test_build_day_index_writes_one_row_per_vehicle(tmp_path):
    _make_cache(tmp_path, "2026-01-18", "100421")
    _make_cache(tmp_path, "2026-01-18", "832111")

    out = build_day_index(tmp_path, "2026-01-18")
    assert out.name == "vehicle_day_index.parquet"
    idx = pd.read_parquet(out)
    assert tuple(idx.columns) == VEHICLE_DAY_INDEX_COLUMNS
    assert set(idx["vehicle_id"]) == {"100421", "832111"}


def test_build_all_day_indexes_idempotent(tmp_path):
    _make_cache(tmp_path, "2026-01-18", "100421")
    _make_cache(tmp_path, "2026-01-19", "100421")

    first_run = build_all_day_indexes(tmp_path)
    assert len(first_run) == 2

    # Second run with overwrite=False: nothing rebuilt (all already exist).
    second_run = build_all_day_indexes(tmp_path)
    assert len(second_run) == 0

    # Overwrite=True: rebuilds both.
    third_run = build_all_day_indexes(tmp_path, overwrite=True)
    assert len(third_run) == 2
