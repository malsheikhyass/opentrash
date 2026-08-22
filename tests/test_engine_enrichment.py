"""Tests for opentrash.engine.enrichment.

Uses synthetic pings + tiny synthetic polygons (boxes in the example region). No real
data, no network. The DuckDB spatial extension is required at runtime; tests
that need it skip gracefully when the extension can't be loaded (e.g. in
sandboxed CI environments).
"""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from opentrash.engine.config import DEFAULT_CONFIG, EngineConfig
from opentrash.engine.enrichment import (
    ENRICHED_COLUMNS,
    _empty_enriched_frame,
    _gdf_to_wkb_df,
    enrich_pings,
    enrich_vehicle_day,
    enriched_path,
)


def _check_spatial_available():
    """Skip the test if DuckDB's spatial extension can't be loaded here."""
    try:
        import duckdb
        con = duckdb.connect()
        try:
            con.execute("INSTALL spatial; LOAD spatial;")
        finally:
            con.close()
    except Exception as e:
        pytest.skip(f"DuckDB spatial extension unavailable: {e}")


# ----- Pure / unit helpers (no DuckDB needed) -----
def test_enriched_columns_shape():
    assert ENRICHED_COLUMNS[:6] == (
        "vehicle_id", "dt_utc", "dt_local", "lat", "lon", "speed_mph",
    )
    assert "route_id" in ENRICHED_COLUMNS
    assert "apn" in ENRICHED_COLUMNS
    assert "in_landfill" in ENRICHED_COLUMNS
    assert "at_depot" in ENRICHED_COLUMNS


def test_engine_config_defaults_are_reasonable():
    cfg = DEFAULT_CONFIG
    assert cfg.parcel_edge_ft > 0
    assert cfg.slow_mph_max > 0
    assert cfg.landfill_buffer_ft > 0
    assert cfg.depot_radius_ft > 0
    assert cfg.output_crs == "EPSG:4326"


def test_engine_config_is_frozen():
    from dataclasses import FrozenInstanceError
    cfg = EngineConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.parcel_edge_ft = 999.0          # type: ignore[misc]


def test_engine_config_overrides_take():
    cfg = EngineConfig(parcel_edge_ft=10.0, slow_mph_max=20.0)
    assert cfg.parcel_edge_ft == 10.0
    assert cfg.slow_mph_max == 20.0
    # Defaults still in place for unset fields.
    assert cfg.depot_radius_ft == DEFAULT_CONFIG.depot_radius_ft


def test_enriched_path_shape(tmp_path):
    p = enriched_path(tmp_path / "enriched", "2026-01-18", "100001")
    assert p.name == "100001.parquet"
    assert p.parent.name == "2026-01-18"


def test_gdf_to_wkb_df_round_trip():
    gdf = gpd.GeoDataFrame(
        {"route_id": ["A", "B"]},
        geometry=[box(-117.20, 32.70, -117.10, 32.80),
                  box(-117.30, 32.60, -117.20, 32.65)],
        crs="EPSG:4326",
    )
    df = _gdf_to_wkb_df(gdf, ["route_id"])
    assert list(df.columns) == ["route_id", "geom_wkb"]
    assert len(df) == 2
    # WKB is bytes
    assert all(isinstance(b, (bytes, bytearray)) for b in df["geom_wkb"])


def test_gdf_to_wkb_df_empty():
    gdf = gpd.GeoDataFrame({"route_id": []}, geometry=[], crs="EPSG:4326")
    df = _gdf_to_wkb_df(gdf, ["route_id"])
    assert list(df.columns) == ["route_id", "geom_wkb"]
    assert len(df) == 0


def test_empty_enriched_frame_shape():
    df = _empty_enriched_frame()
    assert list(df.columns) == list(ENRICHED_COLUMNS)
    assert len(df) == 0


# ----- Behavior tests (require DuckDB spatial) -----
def _build_substrate(tmp_path: Path):
    """Build a tiny synthetic substrate.

    Layout in lon/lat (EPSG:4326):
      route 'A' covers a 0.01-deg square around (-117.20, 32.70)
      route 'B' covers a different square
      parcel APN='P1' is a small box inside route A
      one landfill polygon far to the south
      one depot polygon at a known centroid
    """
    routes = gpd.GeoDataFrame(
        {"route_id": ["A", "B"]},
        geometry=[
            box(-117.205, 32.700, -117.195, 32.710),   # route A
            box(-117.300, 32.600, -117.290, 32.610),   # route B
        ],
        crs="EPSG:4326",
    )

    # One parcel inside route A
    parcels_wkb = pd.DataFrame({
        "APN": ["P1"],
        "route_id": ["A"],
        "min_lon": [-117.2005], "min_lat": [32.7005],
        "max_lon": [-117.1995], "max_lat": [32.7015],
        "geom_wkb": [
            box(-117.2005, 32.7005, -117.1995, 32.7015).wkb,
        ],
    })
    parcels_path = tmp_path / "parcels_wkb.parquet"
    parcels_wkb.to_parquet(parcels_path, index=False)

    # One landfill far to the south
    landfills = gpd.GeoDataFrame(
        {"name": ["Sycamore"]},
        geometry=[box(-117.05, 32.50, -117.00, 32.55)],
        crs="EPSG:4326",
    )

    # Depot near (-117.10, 32.80)
    depot = gpd.GeoDataFrame(
        {"name": ["Central Yard"]},
        geometry=[box(-117.101, 32.799, -117.099, 32.801)],
        crs="EPSG:4326",
    )

    return parcels_path, routes, landfills, depot


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def test_enrich_pings_empty_input():
    """Empty input → empty output, no DuckDB needed."""
    out = enrich_pings(
        pd.DataFrame({c: [] for c in (
            "vehicle_id", "dt_utc", "dt_local", "lat", "lon", "speed_mph"
        )}),
        parcels_wkb_path="nonexistent.parquet",
        routes_gdf=gpd.GeoDataFrame(
            {"route_id": []}, geometry=[], crs="EPSG:4326"
        ),
        landfills_gdf=gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"),
        depot_gdf=gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"),
    )
    assert list(out.columns) == list(ENRICHED_COLUMNS)
    assert len(out) == 0


def test_enrich_pings_route_attribution(tmp_path):
    """A ping inside route A's polygon gets route_id='A'."""
    _check_spatial_available()
    parcels_path, routes, landfills, depot = _build_substrate(tmp_path)

    pings = pd.DataFrame({
        "vehicle_id": ["100001"],
        "dt_utc": [_ts("2026-01-18 14:00:00")],
        "dt_local": [_ts("2026-01-18 06:00:00")],
        "lat": [32.705],
        "lon": [-117.200],
        "speed_mph": [5.0],
    })

    out = enrich_pings(
        pings,
        parcels_wkb_path=parcels_path,
        routes_gdf=routes,
        landfills_gdf=landfills,
        depot_gdf=depot,
    )
    assert len(out) == 1
    assert out["route_id"].iloc[0] == "A"


def test_enrich_pings_parcel_attribution_when_slow(tmp_path):
    """A slow ping inside a parcel's bbox+edge gets that parcel's APN."""
    _check_spatial_available()
    parcels_path, routes, landfills, depot = _build_substrate(tmp_path)

    pings = pd.DataFrame({
        "vehicle_id": ["100001"],
        "dt_utc": [_ts("2026-01-18 14:00:00")],
        "dt_local": [_ts("2026-01-18 06:00:00")],
        "lat": [32.7010],
        "lon": [-117.2000],
        "speed_mph": [5.0],     # slow → counts as service event
    })

    out = enrich_pings(
        pings,
        parcels_wkb_path=parcels_path,
        routes_gdf=routes,
        landfills_gdf=landfills,
        depot_gdf=depot,
    )
    assert len(out) == 1
    assert out["apn"].iloc[0] == "P1"


def test_enrich_pings_fast_pings_get_no_parcel(tmp_path):
    """A fast ping over a parcel gets route attribution but no APN."""
    _check_spatial_available()
    parcels_path, routes, landfills, depot = _build_substrate(tmp_path)

    pings = pd.DataFrame({
        "vehicle_id": ["100001"],
        "dt_utc": [_ts("2026-01-18 14:00:00")],
        "dt_local": [_ts("2026-01-18 06:00:00")],
        "lat": [32.7010],
        "lon": [-117.2000],
        "speed_mph": [35.0],   # fast → no APN
    })

    out = enrich_pings(
        pings,
        parcels_wkb_path=parcels_path,
        routes_gdf=routes,
        landfills_gdf=landfills,
        depot_gdf=depot,
    )
    assert len(out) == 1
    assert out["route_id"].iloc[0] == "A"     # route still attributed
    assert pd.isna(out["apn"].iloc[0])         # parcel not, because too fast


def test_enrich_pings_landfill_flag(tmp_path):
    """A ping inside a landfill polygon flags in_landfill=True."""
    _check_spatial_available()
    parcels_path, routes, landfills, depot = _build_substrate(tmp_path)

    pings = pd.DataFrame({
        "vehicle_id": ["100001"],
        "dt_utc": [_ts("2026-01-18 14:00:00")],
        "dt_local": [_ts("2026-01-18 06:00:00")],
        "lat": [32.525],    # inside the synthetic landfill
        "lon": [-117.025],
        "speed_mph": [3.0],
    })

    out = enrich_pings(
        pings,
        parcels_wkb_path=parcels_path,
        routes_gdf=routes,
        landfills_gdf=landfills,
        depot_gdf=depot,
    )
    assert out["in_landfill"].iloc[0] is True or out["in_landfill"].iloc[0]


def test_enrich_pings_depot_flag(tmp_path):
    """A ping near the depot centroid flags at_depot=True."""
    _check_spatial_available()
    parcels_path, routes, landfills, depot = _build_substrate(tmp_path)

    pings = pd.DataFrame({
        "vehicle_id": ["100001"],
        "dt_utc": [_ts("2026-01-18 14:00:00")],
        "dt_local": [_ts("2026-01-18 06:00:00")],
        "lat": [32.800],    # depot centroid
        "lon": [-117.100],
        "speed_mph": [0.0],
    })

    out = enrich_pings(
        pings,
        parcels_wkb_path=parcels_path,
        routes_gdf=routes,
        landfills_gdf=landfills,
        depot_gdf=depot,
    )
    assert bool(out["at_depot"].iloc[0]) is True


def test_enrich_pings_ping_outside_everything(tmp_path):
    """A ping outside all layers gets NULL/false for every enrichment."""
    _check_spatial_available()
    parcels_path, routes, landfills, depot = _build_substrate(tmp_path)

    pings = pd.DataFrame({
        "vehicle_id": ["100001"],
        "dt_utc": [_ts("2026-01-18 14:00:00")],
        "dt_local": [_ts("2026-01-18 06:00:00")],
        "lat": [40.0],     # way outside everything
        "lon": [-122.0],
        "speed_mph": [25.0],
    })

    out = enrich_pings(
        pings,
        parcels_wkb_path=parcels_path,
        routes_gdf=routes,
        landfills_gdf=landfills,
        depot_gdf=depot,
    )
    assert len(out) == 1
    assert pd.isna(out["route_id"].iloc[0])
    assert pd.isna(out["apn"].iloc[0])
    assert bool(out["in_landfill"].iloc[0]) is False
    assert bool(out["at_depot"].iloc[0]) is False


def test_enrich_vehicle_day_writes_to_correct_layout(tmp_path):
    """The vehicle-day pipeline writes to <out>/<day>/<vehicle>.parquet."""
    _check_spatial_available()
    parcels_path, routes, landfills, depot = _build_substrate(tmp_path)

    # Create a cache file in the expected layout
    cache_dir = tmp_path / "cache" / "2026-01-18"
    cache_dir.mkdir(parents=True)
    pings = pd.DataFrame({
        "vehicle_id": ["100001"],
        "dt_utc": [_ts("2026-01-18 14:00:00")],
        "dt_local": [_ts("2026-01-18 06:00:00")],
        "lat": [32.705],
        "lon": [-117.200],
        "speed_mph": [5.0],
    })
    pings.to_parquet(cache_dir / "100001.parquet", index=False)

    out_path = enrich_vehicle_day(
        cache_dir / "100001.parquet",
        tmp_path / "enriched",
        parcels_wkb_path=parcels_path,
        routes_gdf=routes,
        landfills_gdf=landfills,
        depot_gdf=depot,
    )
    assert out_path.exists()
    assert out_path.parent.name == "2026-01-18"
    assert out_path.name == "100001.parquet"
    written = pd.read_parquet(out_path)
    assert list(written.columns) == list(ENRICHED_COLUMNS)
    assert len(written) == 1


def test_enrich_vehicle_day_missing_file_raises(tmp_path):
    routes = gpd.GeoDataFrame(
        {"route_id": []}, geometry=[], crs="EPSG:4326"
    )
    empty = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    with pytest.raises(FileNotFoundError):
        enrich_vehicle_day(
            tmp_path / "nope.parquet",
            tmp_path / "out",
            parcels_wkb_path=tmp_path / "nope_parcels.parquet",
            routes_gdf=routes,
            landfills_gdf=empty,
            depot_gdf=empty,
        )
