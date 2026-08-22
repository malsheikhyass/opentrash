"""Tests for opentrash.routeview.*.

Uses synthetic enriched pings, segments, parcels_wkb, and routes built
in tmp_path. Verifies each module produces the expected shape, and the
runner produces a valid self-contained HTML.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from opentrash.routeview import parcel_eval, rank, render, runner, trail


# ---------------------------------------------------------------------------
# Helpers — synthetic data
# ---------------------------------------------------------------------------
def _make_pings(rows: list[dict], out_path: Path) -> Path:
    defaults = {
        "vehicle_id": "100001",
        "lat": 32.7,
        "lon": -117.2,
        "speed_mph": 4.0,
        "route_id": "R1",
        "apn": None,
        "in_landfill": False,
        "at_depot": False,
    }
    rows_full = []
    for r in rows:
        row = {**defaults, **r}
        row.setdefault("dt_utc", row["dt_local"])
        rows_full.append(row)
    df = pd.DataFrame(rows_full)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return out_path


def _make_segments_timeline(rows: list[dict], out_path: Path) -> Path:
    df = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return out_path


def _make_parcels_wkb(parcels: list[dict], out_path: Path) -> Path:
    """Each parcel: APN, route_ref/org/rec, geometry (a tiny polygon)."""
    rows = []
    for i, p in enumerate(parcels):
        # Build a tiny square polygon at the supplied (lon, lat)
        lon = p.get("lon", -117.2 + 0.001 * i)
        lat = p.get("lat", 32.7 + 0.001 * i)
        poly = Polygon([
            (lon, lat), (lon + 0.0005, lat),
            (lon + 0.0005, lat + 0.0005), (lon, lat + 0.0005),
            (lon, lat),
        ])
        # Reproject to EPSG:2230 then dump WKB (parcels_wkb stores in working CRS)
        gs = gpd.GeoSeries([poly], crs="EPSG:4326").to_crs("EPSG:2230")
        rows.append({
            "APN": p["APN"],
            "route_ref": p.get("route_ref"),
            "route_org": p.get("route_org"),
            "route_rec": p.get("route_rec"),
            "wkb_2230": gs.iloc[0].wkb,
        })
    df = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return out_path


def _make_routes_parquet(routes: list[dict], out_path: Path) -> Path:
    rows = []
    for r in routes:
        bbox = r.get("bbox", [-117.21, 32.69, -117.19, 32.71])
        poly = Polygon([
            (bbox[0], bbox[1]), (bbox[2], bbox[1]),
            (bbox[2], bbox[3]), (bbox[0], bbox[3]),
            (bbox[0], bbox[1]),
        ])
        rows.append({"route_id": r["route_id"], "geometry": poly})
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326").to_crs("EPSG:2230")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out_path)
    return out_path


def _make_facilities_parquet(out_path: Path) -> Path:
    rows = [
        {"Name": "Central Yard",
         "geometry": Polygon([(-117.18, 32.69), (-117.17, 32.69),
                              (-117.17, 32.70), (-117.18, 32.70),
                              (-117.18, 32.69)])},
        {"Name": "Sycamore Landfill",
         "geometry": Polygon([(-117.16, 32.69), (-117.15, 32.69),
                              (-117.15, 32.70), (-117.16, 32.70),
                              (-117.16, 32.69)])},
    ]
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326").to_crs("EPSG:2230")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out_path)
    return out_path


def _make_patterns_chunk(rows: list[dict], out_path: Path) -> Path:
    """Write a tiny patterns parquet matching L10's PATTERNS_COLUMNS shape."""
    from opentrash.patterns.detector import PATTERNS_COLUMNS
    df = pd.DataFrame(rows)
    for c in PATTERNS_COLUMNS:
        if c not in df.columns:
            df[c] = None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df[list(PATTERNS_COLUMNS)].to_parquet(out_path, index=False)
    return out_path


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


# ---------------------------------------------------------------------------
# rank.py
# ---------------------------------------------------------------------------
def test_rank_returns_vehicles_sorted_by_ping_count(tmp_path):
    rows = (
        [{"vehicle_id": "V1", "dt_local": _ts(f"2026-01-18 {h:02d}:00:00"),
          "route_id": "R1"} for h in range(7, 17)]              # 10 pings on V1
        + [{"vehicle_id": "V2", "dt_local": _ts(f"2026-01-18 {h:02d}:00:00"),
            "route_id": "R1"} for h in range(7, 12)]            # 5 pings on V2
        + [{"vehicle_id": "V3", "dt_local": _ts("2026-01-18 09:00:00"),
            "route_id": "R1"}]                                  # 1 ping on V3
    )
    _make_pings(rows, tmp_path / "enriched" / "2026-01-18" / "all.parquet")
    ranked = rank.rank_vehicles_for_route_day(
        str(tmp_path / "enriched" / "2026-01-18" / "*.parquet"),
        route_id="R1", day="2026-01-18", min_pings=2,
    )
    assert ranked["vehicle_id"].tolist() == ["V1", "V2"]   # V3 dropped (< 2 pings)
    assert ranked.iloc[0]["n_pings"] == 10


def test_rank_empty_when_route_missing(tmp_path):
    _make_pings(
        [{"vehicle_id": "V1", "dt_local": _ts("2026-01-18 09:00:00"),
          "route_id": "R1"}],
        tmp_path / "enriched" / "2026-01-18" / "p.parquet",
    )
    ranked = rank.rank_vehicles_for_route_day(
        str(tmp_path / "enriched" / "**" / "*.parquet"),
        route_id="R_DOES_NOT_EXIST", day="2026-01-18",
    )
    assert ranked.empty


# ---------------------------------------------------------------------------
# trail.py
# ---------------------------------------------------------------------------
def test_trail_builds_geojson_points_colored_by_phase(tmp_path):
    rows = [
        {"vehicle_id": "V1", "dt_local": _ts("2026-01-18 07:00:00"),
         "route_id": "R1", "lat": 32.70, "lon": -117.20, "speed_mph": 4.0},
        {"vehicle_id": "V1", "dt_local": _ts("2026-01-18 09:00:00"),
         "route_id": "R1", "lat": 32.71, "lon": -117.21, "speed_mph": 5.0},
    ]
    _make_pings(rows, tmp_path / "enriched" / "2026-01-18" / "v1.parquet")
    seg_rows = [
        {"vehicle_id": "V1", "segment_id": 1, "segment_type": "collection",
         "load_number": 1.0, "route_id": "R1",
         "start_dt_local": _ts("2026-01-18 06:30:00"),
         "end_dt_local": _ts("2026-01-18 09:30:00"),
         "duration_seconds": 10800.0, "miles": 5.0, "n_pings": 2},
    ]
    _make_segments_timeline(seg_rows,
                            tmp_path / "segments" / "timeline"
                            / "2026-01-18" / "V1.parquet")
    geojson_str = trail.build_trail_geojson(
        enriched_glob=str(tmp_path / "enriched" / "2026-01-18" / "*.parquet"),
        segments_timeline_path=tmp_path / "segments" / "timeline"
                                / "2026-01-18" / "V1.parquet",
        route_id="R1", day="2026-01-18", vehicle_id="V1",
        downsample_seconds=0,
    )
    fc = json.loads(geojson_str)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2
    for feat in fc["features"]:
        assert feat["properties"]["phase"] == "collection"
        assert feat["properties"]["color"] == trail.PHASE_COLORS["collection"]


def test_trail_downsamples_to_one_per_bucket(tmp_path):
    """30 pings 5 seconds apart -> with downsample_seconds=30 keep ~5 dots."""
    rows = [
        {"vehicle_id": "V1",
         "dt_local": _ts("2026-01-18 07:00:00") + pd.Timedelta(seconds=5 * i),
         "route_id": "R1", "lat": 32.70 + 0.0001 * i,
         "lon": -117.20, "speed_mph": 4.0}
        for i in range(30)
    ]
    _make_pings(rows, tmp_path / "enriched" / "2026-01-18" / "v1.parquet")
    fc = json.loads(trail.build_trail_geojson(
        enriched_glob=str(tmp_path / "enriched" / "2026-01-18" / "*.parquet"),
        segments_timeline_path=tmp_path / "non_existent.parquet",
        route_id="R1", day="2026-01-18", vehicle_id="V1",
        downsample_seconds=30,
    ))
    # 30 pings span 150 seconds; with a 30s bucket we keep ~6 dots
    assert 4 <= len(fc["features"]) <= 7


def test_trail_empty_when_no_match(tmp_path):
    rows = [
        {"vehicle_id": "V1", "dt_local": _ts("2026-01-18 07:00:00"),
         "route_id": "R1", "speed_mph": 4.0},
    ]
    _make_pings(rows, tmp_path / "enriched" / "2026-01-18" / "v1.parquet")
    fc = json.loads(trail.build_trail_geojson(
        enriched_glob=str(tmp_path / "enriched" / "2026-01-18" / "*.parquet"),
        segments_timeline_path=tmp_path / "no_segments.parquet",
        route_id="R_NONE", day="2026-01-18", vehicle_id="V1",
    ))
    assert fc["features"] == []


# ---------------------------------------------------------------------------
# parcel_eval.py
# ---------------------------------------------------------------------------
def test_parcel_eval_served_and_missed(tmp_path):
    _make_parcels_wkb([
        {"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
        {"APN": "P2", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
    ], tmp_path / "parcels_wkb.parquet")
    rows = [
        {"vehicle_id": "V1", "dt_local": _ts("2026-01-18 07:00:00"),
         "route_id": "R1", "apn": "P1", "speed_mph": 4.0},
    ]
    _make_pings(rows, tmp_path / "enriched" / "2026-01-18" / "v1.parquet")
    fc = json.loads(parcel_eval.build_parcels_geojson(
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        enriched_glob=str(tmp_path / "enriched" / "2026-01-18" / "*.parquet"),
        route_id="R1", day="2026-01-18", vehicle_id="V1",
    ))
    statuses = {f["properties"]["APN"]: f["properties"]["status"]
                for f in fc["features"]}
    assert statuses["P1"] == "served"
    # P2 has no L10 expectation either, so it's 'unknown' not 'missed'
    assert statuses["P2"] == "unknown"


def test_parcel_eval_missed_when_patterns_expects_but_no_service(tmp_path):
    _make_parcels_wkb([
        {"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1"},
    ], tmp_path / "parcels_wkb.parquet")
    # No pings today
    rows = [
        {"vehicle_id": "V_OTHER", "dt_local": _ts("2026-01-18 07:00:00"),
         "route_id": "R1", "apn": None, "speed_mph": 4.0},
    ]
    _make_pings(rows, tmp_path / "enriched" / "2026-01-18" / "v1.parquet")
    _make_patterns_chunk([
        {"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1",
         "weekly1_vehicle": "V_EXPECTED", "weekly1_dow": "Tue",
         "weekly1_hour": 8, "weekly1_regularity": 0.95},
    ], tmp_path / "patterns" / "win" / "by_route" / "R1.parquet")
    fc = json.loads(parcel_eval.build_parcels_geojson(
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        enriched_glob=str(tmp_path / "enriched" / "2026-01-18" / "*.parquet"),
        route_id="R1", day="2026-01-18", vehicle_id="V1",
        patterns_chunk_path=tmp_path / "patterns" / "win" / "by_route" / "R1.parquet",
    ))
    props = fc["features"][0]["properties"]
    assert props["status"] == "missed"
    assert props["expected_vehicle_weekly1"] == "V_EXPECTED"
    assert props["actual_vehicle"] is None


# ---------------------------------------------------------------------------
# render.py
# ---------------------------------------------------------------------------
def test_render_template_substitutes_all_placeholders():
    """Plug minimal inputs into the template and confirm the output renders."""
    html = render.build_routeview_html(
        route_id="R1",
        vehicle="100001",
        day=dt.date(2026, 1, 18),
        day_time_label="06:00 → 17:00",
        route_json='{"type":"FeatureCollection","features":[]}',
        lf_json='{"type":"FeatureCollection","features":[]}',
        pts_json='{"type":"FeatureCollection","features":[]}',
        parcels_json='{"type":"FeatureCollection","features":[]}',
        sites_json='{"type":"FeatureCollection","features":[]}',
        summary_json='{}',
        stats_json_enriched='{"loads":[]}',
        center_lon=-117.2,
        center_lat=32.7,
    )
    # The known stable placeholder
    assert "Route R1" in html
    assert "100001" in html
    assert "2026-01-18" in html
    # The MapLibre tile-URL literals survived
    assert "{x}" in html and "{y}" in html and "{z}" in html
    # The doctype is intact
    assert html.startswith("<!DOCTYPE html>")
    # And no unfilled placeholders left
    assert "{ROUTE_ID}" not in html
    assert "{vehicle}" not in html
    assert "{day_iso}" not in html


def test_render_default_use_code_includes_three_commodities():
    """The default USE_CODE dict has R, O, C entries."""
    html = render.build_routeview_html(
        route_id="x", vehicle="y", day="2026-01-18", day_time_label="—",
        route_json="{}", lf_json="{}", pts_json="{}",
        parcels_json="{}", sites_json="{}", summary_json="{}",
        stats_json_enriched='{"loads":[]}',
        center_lon=0.0, center_lat=0.0,
    )
    # USE_CODE rendered as a JSON dict somewhere in the HTML
    assert '"R"' in html and '"O"' in html and '"C"' in html


# ---------------------------------------------------------------------------
# runner.py — the end-to-end smoke test
# ---------------------------------------------------------------------------
def test_runner_render_routeview_end_to_end(tmp_path):
    """Full pipeline: enriched + segments + parcels + routes → one HTML."""
    # 1. Routes
    _make_routes_parquet(
        [{"route_id": "R1", "bbox": [-117.21, 32.69, -117.19, 32.71]}],
        tmp_path / "routes.parquet",
    )
    # 2. Facilities
    _make_facilities_parquet(tmp_path / "facilities.parquet")
    # 3. Parcels
    _make_parcels_wkb([
        {"APN": "P1", "route_ref": "R1", "route_org": "O1", "route_rec": "C1",
         "lon": -117.20, "lat": 32.70},
        {"APN": "P2", "route_ref": "R1", "route_org": "O1", "route_rec": "C1",
         "lon": -117.201, "lat": 32.701},
    ], tmp_path / "parcels_wkb.parquet")
    # 4. Enriched pings
    rows = [
        {"vehicle_id": "V1", "dt_local": _ts("2026-01-18 06:00:00"),
         "route_id": None, "apn": None, "at_depot": True, "speed_mph": 0.0},
        {"vehicle_id": "V1", "dt_local": _ts("2026-01-18 07:00:00"),
         "route_id": "R1", "apn": "P1", "speed_mph": 4.0,
         "lat": 32.70, "lon": -117.20},
        {"vehicle_id": "V1", "dt_local": _ts("2026-01-18 09:00:00"),
         "route_id": None, "apn": None, "in_landfill": True, "speed_mph": 1.0},
    ]
    _make_pings(rows, tmp_path / "enriched" / "2026-01-18" / "V1.parquet")
    # 5. Segments timeline
    seg_rows = [
        {"vehicle_id": "V1", "segment_id": 1, "segment_type": "depot_departure",
         "load_number": None, "route_id": None,
         "start_dt_local": _ts("2026-01-18 06:00:00"),
         "end_dt_local": _ts("2026-01-18 06:30:00"),
         "duration_seconds": 1800.0, "miles": 0.0, "n_pings": 1},
        {"vehicle_id": "V1", "segment_id": 2, "segment_type": "collection",
         "load_number": 1.0, "route_id": "R1",
         "start_dt_local": _ts("2026-01-18 07:00:00"),
         "end_dt_local": _ts("2026-01-18 08:00:00"),
         "duration_seconds": 3600.0, "miles": 4.0, "n_pings": 1},
        {"vehicle_id": "V1", "segment_id": 3, "segment_type": "dump",
         "load_number": 1.0, "route_id": None,
         "start_dt_local": _ts("2026-01-18 09:00:00"),
         "end_dt_local": _ts("2026-01-18 09:30:00"),
         "duration_seconds": 1800.0, "miles": 0.0, "n_pings": 1},
    ]
    _make_segments_timeline(
        seg_rows,
        tmp_path / "segments" / "timeline" / "2026-01-18" / "V1.parquet",
    )

    out_path = runner.render_routeview(
        enriched_root=tmp_path / "enriched",
        segments_root=tmp_path / "segments",
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        routes_path=tmp_path / "routes.parquet",
        facilities_path=tmp_path / "facilities.parquet",
        out_root=tmp_path / "routeview",
        route_id="R1", day="2026-01-18", vehicle_id="V1",
    )
    assert out_path.exists()
    assert out_path.name == "R1__V1.parquet" or out_path.name == "R1__V1.html"
    assert out_path.suffix == ".html"
    html = out_path.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "R1" in html
    assert "V1" in html


def test_runner_render_all_routes_for_day(tmp_path):
    """Two routes, two vehicles, one day → two HTML files."""
    _make_routes_parquet([
        {"route_id": "R1", "bbox": [-117.21, 32.69, -117.19, 32.71]},
        {"route_id": "R2", "bbox": [-117.19, 32.69, -117.17, 32.71]},
    ], tmp_path / "routes.parquet")
    _make_facilities_parquet(tmp_path / "facilities.parquet")
    _make_parcels_wkb([
        {"APN": "P1", "route_ref": "R1", "route_org": None, "route_rec": None,
         "lon": -117.20, "lat": 32.70},
        {"APN": "P2", "route_ref": "R2", "route_org": None, "route_rec": None,
         "lon": -117.18, "lat": 32.70},
    ], tmp_path / "parcels_wkb.parquet")
    rows = []
    for i in range(10):
        rows.append({"vehicle_id": "V1",
                     "dt_local": _ts("2026-01-18 07:00:00") + pd.Timedelta(minutes=i),
                     "route_id": "R1", "apn": "P1", "speed_mph": 4.0,
                     "lat": 32.70, "lon": -117.20})
    for i in range(10):
        rows.append({"vehicle_id": "V2",
                     "dt_local": _ts("2026-01-18 09:00:00") + pd.Timedelta(minutes=i),
                     "route_id": "R2", "apn": "P2", "speed_mph": 4.0,
                     "lat": 32.70, "lon": -117.18})
    _make_pings(rows, tmp_path / "enriched" / "2026-01-18" / "all.parquet")

    written = runner.render_all_routes_for_day(
        enriched_root=tmp_path / "enriched",
        segments_root=tmp_path / "segments",       # no segments — function handles missing
        parcels_wkb_path=tmp_path / "parcels_wkb.parquet",
        routes_path=tmp_path / "routes.parquet",
        facilities_path=tmp_path / "facilities.parquet",
        out_root=tmp_path / "routeview",
        day="2026-01-18",
    )
    assert len(written) == 2
    names = sorted(p.name for p in written)
    assert names == ["R1__V1.html", "R2__V2.html"]
