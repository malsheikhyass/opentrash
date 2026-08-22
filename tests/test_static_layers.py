"""Tests for opentrash.prep.static_layers."""

import geopandas as gpd
import pytest
from shapely.geometry import box

from opentrash.core.crs import WEB_CRS, WORKING_CRS
from opentrash.prep.static_layers import (
    FacilitiesConfig,
    buffer_in_working_crs,
    build_route_index,
    load_facilities,
    load_route_polygons,
    route_bboxes,
)


def _routes_4326(rows):
    """rows: [(route_id, minx, miny, maxx, maxy)] in 4326."""
    return gpd.GeoDataFrame(
        {"route_id": [r[0] for r in rows]},
        geometry=[box(r[1], r[2], r[3], r[4]) for r in rows],
        crs=WEB_CRS,
    )


# ----- route bbox math -----
def test_route_bboxes_one_row_per_route():
    routes = _routes_4326([
        ("A", -117.20, 32.70, -117.10, 32.80),
        ("B", -117.30, 32.60, -117.25, 32.65),
    ])
    out = route_bboxes(routes, edge_buffer_deg=0)
    assert set(out["route_id"]) == {"A", "B"}
    a = out[out["route_id"] == "A"].iloc[0]
    assert a["rte_min_lon"] == pytest.approx(-117.20)
    assert a["rte_max_lat"] == pytest.approx(32.80)


def test_route_bboxes_dissolves_multi_polygon_routes():
    # Same route_id, two disjoint pieces -> one encompassing bbox.
    routes = _routes_4326([
        ("A", -117.20, 32.70, -117.15, 32.75),
        ("A", -117.10, 32.80, -117.05, 32.82),
    ])
    out = route_bboxes(routes, edge_buffer_deg=0)
    assert len(out) == 1
    a = out.iloc[0]
    assert a["rte_min_lon"] == pytest.approx(-117.20)
    assert a["rte_max_lon"] == pytest.approx(-117.05)
    assert a["rte_max_lat"] == pytest.approx(32.82)


def test_route_bboxes_edge_buffer_applied():
    routes = _routes_4326([("A", -117.20, 32.70, -117.10, 32.80)])
    out = route_bboxes(routes, edge_buffer_deg=0.001)
    a = out.iloc[0]
    assert a["rte_min_lon"] == pytest.approx(-117.201)
    assert a["rte_max_lat"] == pytest.approx(32.801)


def test_buffer_in_feet_grows_geometry():
    routes = _routes_4326([("A", -117.20, 32.70, -117.10, 32.80)])
    before = routes.geometry.iloc[0].area
    buffered = buffer_in_working_crs(routes, buffer_ft=500)
    after = buffered.geometry.iloc[0].area
    assert after > before
    assert str(buffered.crs).upper() == str(routes.crs).upper()


# ----- route loading from disk -----
def test_load_route_polygons_round_trip(tmp_path):
    src = gpd.GeoDataFrame(
        {"ROUTE_ID": ["A", "B"]},
        geometry=[box(-117.2, 32.7, -117.1, 32.8), box(-117.3, 32.6, -117.25, 32.65)],
        crs=WORKING_CRS,
    )
    path = tmp_path / "routes.gpkg"
    src.to_file(path)

    out = load_route_polygons(path, "ROUTE_ID")
    assert list(out.columns) == ["route_id", "geometry"]
    assert str(out.crs).upper().endswith("4326")
    assert set(out["route_id"]) == {"A", "B"}


def test_load_route_polygons_missing_column_raises(tmp_path):
    src = gpd.GeoDataFrame(
        {"WRONG_COL": ["A"]},
        geometry=[box(0, 0, 1, 1)],
        crs=WEB_CRS,
    )
    path = tmp_path / "routes.gpkg"
    src.to_file(path)
    with pytest.raises(KeyError, match="WRONG"):
        load_route_polygons(path, "MISSING_COL")


# ----- build_route_index: AUTO only and AUTO + MANUAL -----
def test_build_route_index_auto_only(tmp_path):
    auto = gpd.GeoDataFrame(
        {"ROUTE_ID": ["A", "B"]},
        geometry=[box(-117.2, 32.7, -117.1, 32.8), box(-117.3, 32.6, -117.25, 32.65)],
        crs=WEB_CRS,
    )
    auto_path = tmp_path / "auto.gpkg"
    auto.to_file(auto_path)

    out = build_route_index(auto_path)
    assert set(out["route_id"]) == {"A", "B"}


def test_build_route_index_manual_only_routes_added(tmp_path):
    """A route present only in MANUAL gets included (with buffer applied)."""
    auto = gpd.GeoDataFrame(
        {"ROUTE_ID": ["A"]},
        geometry=[box(-117.2, 32.7, -117.1, 32.8)],
        crs=WEB_CRS,
    )
    manual = gpd.GeoDataFrame(
        {"ROUTE_ID": ["B"]},   # only in MANUAL, not AUTO
        geometry=[box(-117.30, 32.60, -117.29, 32.61)],
        crs=WEB_CRS,
    )
    auto_path = tmp_path / "auto.gpkg"
    manual_path = tmp_path / "manual.gpkg"
    auto.to_file(auto_path)
    manual.to_file(manual_path)

    out = build_route_index(auto_path, manual_path, manual_buffer_ft=150)
    assert set(out["route_id"]) == {"A", "B"}

    # The MANUAL route B should have a wider bbox than its original (due to buffer).
    b = out[out["route_id"] == "B"].iloc[0]
    assert b["rte_max_lon"] - b["rte_min_lon"] > 0.01    # bigger than the 0.01 original


def test_build_route_index_auto_wins_when_route_in_both(tmp_path):
    """A route in both AUTO and MANUAL keeps AUTO geometry (no extra MANUAL buffer)."""
    big = box(-117.20, 32.70, -117.10, 32.80)
    small = box(-117.190, 32.705, -117.189, 32.706)
    auto = gpd.GeoDataFrame({"ROUTE_ID": ["A"]}, geometry=[big], crs=WEB_CRS)
    manual  = gpd.GeoDataFrame({"ROUTE_ID": ["A"]}, geometry=[small], crs=WEB_CRS)
    auto.to_file(tmp_path / "auto.gpkg")
    manual.to_file(tmp_path / "manual.gpkg")

    out = build_route_index(tmp_path / "auto.gpkg", tmp_path / "manual.gpkg", manual_buffer_ft=150)
    a = out[out["route_id"] == "A"].iloc[0]
    # bbox should match the big AUTO polygon, not the small MANUAL one.
    assert a["rte_min_lon"] == pytest.approx(-117.20, abs=1e-2)
    assert a["rte_max_lon"] == pytest.approx(-117.10, abs=1e-2)


# ----- facilities loading + split by depot name -----
def _facilities_in_working():
    """Synthetic facilities layer in the working CRS."""
    # Geometries don't matter for the split logic; pick small boxes.
    return gpd.GeoDataFrame(
        {"Name": ["Central Yard", "Sycamore Landfill", "Otay Landfill", "Some Other Site"]},
        geometry=[
            box(6_280_000, 1_840_000, 6_281_000, 1_841_000),
            box(6_290_000, 1_850_000, 6_291_000, 1_851_000),
            box(6_270_000, 1_830_000, 6_271_000, 1_831_000),
            box(6_260_000, 1_820_000, 6_261_000, 1_821_000),
        ],
        crs=WORKING_CRS,
    )


def test_load_facilities_splits_depot_from_landfills(tmp_path):
    src = _facilities_in_working()
    path = tmp_path / "facilities.parquet"
    src.to_parquet(path)

    out = load_facilities(path)
    assert set(out.keys()) == {"landfill", "depot"}
    # Depot: exactly one row with the matching name.
    assert len(out["depot"]) == 1
    assert out["depot"]["Name"].iloc[0] == "Central Yard"
    # Landfills: the other three rows.
    assert len(out["landfill"]) == 3
    assert "Central Yard" not in set(out["landfill"]["Name"])


def test_load_facilities_buffers_landfills():
    """Landfills get the configured buffer; depot does not."""
    # Run directly on an in-memory file to skip a tmp_path step.
    import tempfile
    src = _facilities_in_working()
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path
        p = Path(td) / "fac.parquet"
        src.to_parquet(p)
        # Use a large buffer so the size change is unambiguous.
        cfg = FacilitiesConfig(landfill_buffer_ft=200)
        out = load_facilities(p, config=cfg)

    landfill_after = out["landfill"].geometry.iloc[0].area
    landfill_before = src.geometry.iloc[1].area     # row 1 = "Sycamore Landfill"
    assert landfill_after > landfill_before


def test_load_facilities_missing_depot_raises(tmp_path):
    src = gpd.GeoDataFrame(
        {"Name": ["Just A Landfill"]},
        geometry=[box(6_290_000, 1_850_000, 6_291_000, 1_851_000)],
        crs=WORKING_CRS,
    )
    path = tmp_path / "no_depot.parquet"
    src.to_parquet(path)
    with pytest.raises(ValueError, match="Depot row not found"):
        load_facilities(path)


def test_load_facilities_custom_config(tmp_path):
    """Different agencies can override the name column and depot name."""
    src = gpd.GeoDataFrame(
        {"FAC_NAME": ["Main Depot", "Landfill A"]},
        geometry=[
            box(6_280_000, 1_840_000, 6_281_000, 1_841_000),
            box(6_290_000, 1_850_000, 6_291_000, 1_851_000),
        ],
        crs=WORKING_CRS,
    )
    path = tmp_path / "fac.parquet"
    src.to_parquet(path)

    cfg = FacilitiesConfig(name_col="FAC_NAME", depot_name="Main Depot")
    out = load_facilities(path, config=cfg)
    assert len(out["depot"]) == 1
    assert out["depot"]["FAC_NAME"].iloc[0] == "Main Depot"
    assert len(out["landfill"]) == 1


def test_load_facilities_no_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_facilities(tmp_path / "nope.parquet")
