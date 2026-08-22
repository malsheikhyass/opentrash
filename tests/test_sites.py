"""Tests for opentrash.prep.sites.

Covers the field cleaners, route-ID assembly, the three commodity validation
rules (including the manual-route 11/12 edge cases), and the bbox sanity
filter. Uses tiny synthetic frames — no real data.
"""

import pandas as pd
import pytest

from opentrash.prep.sites import (
    build_route_ids,
    clean_day,
    clean_route4,
    clean_week,
    in_region_bbox,
    valid_org,
    valid_rec,
    valid_ref,
)


# ----- cleaners -----
def test_clean_route4_pads_and_trims():
    s = pd.Series(["22", "1234567", "  081 ", "abc12", ""])
    out = clean_route4(s)
    assert out.iloc[0] == "0022"      # padded to 4
    assert out.iloc[1] == "4567"      # last 4 of 1234567
    assert out.iloc[2] == "0081"      # stripped + padded
    assert out.iloc[3] == "0012"      # digits only, padded
    assert pd.isna(out.iloc[4])       # empty -> NA


def test_clean_day_only_1_to_5():
    out = clean_day(pd.Series(["1", "5", "0", "6", "x"]))
    assert out.iloc[0] == "1"
    assert out.iloc[1] == "5"
    assert pd.isna(out.iloc[2])       # 0 invalid
    assert pd.isna(out.iloc[3])       # 6 invalid
    assert pd.isna(out.iloc[4])       # non-digit invalid


def test_clean_week_only_b_or_o():
    out = clean_week(pd.Series(["b", "O", " o ", "x", ""]))
    assert out.iloc[0] == "B"
    assert out.iloc[1] == "O"
    assert out.iloc[2] == "O"
    assert pd.isna(out.iloc[3])
    assert pd.isna(out.iloc[4])


# ----- route id assembly -----
def test_build_route_ids():
    df = pd.DataFrame({
        "ref_route4": ["6208", pd.NA],
        "ref_day": ["1", "1"],
        "rec_route4": ["6243", "6243"],
        "rec_day": ["3", "3"],
        "rec_week": ["O", pd.NA],     # second row missing week -> no rec id
        "org_route4": ["7105", "7105"],
        "org_day": ["2", "2"],
    })
    out = build_route_ids(df)
    assert out["route_id_ref"].iloc[0] == "62081"
    assert pd.isna(out["route_id_ref"].iloc[1])     # missing route4
    assert out["route_id_rec"].iloc[0] == "62433O"  # route+day+week
    assert pd.isna(out["route_id_rec"].iloc[1])     # missing week
    assert out["route_id_org"].iloc[0] == "71052"


# ----- validation rules -----
def test_valid_ref():
    # 62081: prefix 62, seq 08 (<40), day 1 -> valid refuse
    # 62433: seq 43 (>=40) -> NOT refuse (that's recycling territory)
    # 99081: bad prefix
    # 22086: day 6 -> invalid day
    ids = pd.Series(["62081", "62433", "99081", "62086"])
    out = valid_ref(ids)
    assert out.iloc[0]           # valid
    assert not out.iloc[1]       # seq >= 40, not refuse
    assert not out.iloc[2]       # bad prefix
    assert not out.iloc[3]       # day 6 invalid


def test_valid_rec_auto_and_manual():
    # Auto (62): seq >= 40 ok. Manual (51): seq must be 40-59.
    ids = pd.Series(["62433O", "51453O", "51653O", "62433"])
    out = valid_rec(ids)
    assert out.iloc[0]            # auto, seq 43, week O -> valid
    assert out.iloc[1]            # manual, seq 45 (40-59) -> valid
    assert not out.iloc[2]        # manual, seq 65 (>=60) -> organics, not rec
    assert not out.iloc[3]        # no week letter -> invalid rec


def test_valid_org_auto_and_manual():
    # Auto organics (71-75): seq < 40. Manual (51/52): seq >= 60.
    ids = pd.Series(["71052", "51652", "51452", "62052"])
    out = valid_org(ids)
    assert out.iloc[0]            # auto org, seq 05 (<40) -> valid
    assert out.iloc[1]            # manual, seq 65 (>=60) -> valid org
    assert not out.iloc[2]        # manual, seq 45 -> that's recycling, not org
    assert not out.iloc[3]        # prefix 22 not an organics prefix


# ----- bbox -----
def test_in_region_bbox():
    df = pd.DataFrame({
        "x": [6_300_000, 0, 10, 6_300_000],
        "y": [1_900_000, 0, 10, 100],     # last row y out of range
    })
    out = in_region_bbox(df)
    assert out.iloc[0]            # inside
    assert not out.iloc[1]       # (0,0) junk
    assert not out.iloc[2]       # tiny values
    assert not out.iloc[3]       # y below envelope


def test_write_routes_by_commodity(tmp_path):
    """Writer partitions cleaned sites by route, wipes stale files first."""
    from opentrash.prep.sites import valid_ref, write_routes_by_commodity

    # Two valid refuse routes (62081, 62082) + one invalid that should be skipped.
    df = pd.DataFrame({
        "site_id": ["s1", "s2", "s3", "s4"],
        "site_group": ["G"] * 4, "status": ["A"] * 4, "apn": ["1"] * 4,
        "ada": [0] * 4, "key_stop": [0] * 4,
        "x": [6_300_000] * 4, "y": [1_900_000] * 4,
        "units": [1] * 4, "site_address": ["a"] * 4,
        "route_id_ref": ["62081", "62081", "62082", "99999"],   # last is invalid prefix
    })
    out = tmp_path / "ref"
    # leave a stale file in out_dir to confirm wipe-and-rewrite
    out.mkdir()
    (out / "stale.parquet").write_text("stale")

    written = write_routes_by_commodity(df, "route_id_ref", valid_ref(df["route_id_ref"]), out)

    assert written == 2     # two valid routes
    files = sorted(p.name for p in out.glob("*.parquet"))
    assert files == ["62081.parquet", "62082.parquet"]
    assert not (out / "stale.parquet").exists()  # wiped
    # verify content
    r1 = pd.read_parquet(out / "62081.parquet")
    assert len(r1) == 2 and (r1["route_id"] == "62081").all()


# ---------------------------------------------------------------------------
# Geometry + spatial join with parcels (added in Lesson 4)
# ---------------------------------------------------------------------------
def test_sites_to_geo_builds_points():
    import geopandas as gpd

    from opentrash.prep.sites import sites_to_geo

    df = pd.DataFrame({
        "Account_Name": ["A", "B"],
        "Longitude": [-117.20, -117.19],
        "Latitude":  [32.70, 32.71],
    })
    gdf = sites_to_geo(df)
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 2
    assert all(g.geom_type == "Point" for g in gdf.geometry)
    assert str(gdf.crs).upper().endswith("4326")


def test_sites_to_geo_drops_bad_coordinates():
    from opentrash.prep.sites import sites_to_geo

    df = pd.DataFrame({
        "Account_Name": ["good", "missing_lat", "bad_range", "non_numeric"],
        "Longitude": [-117.20, -117.21, 9999.0, "huh"],
        "Latitude":  [32.70, None, 32.72, 32.73],
    })
    gdf = sites_to_geo(df)
    assert len(gdf) == 1
    assert gdf["Account_Name"].iloc[0] == "good"


def test_sites_to_geo_missing_columns_raises():
    from opentrash.prep.sites import sites_to_geo

    df = pd.DataFrame({"Account_Name": ["A"]})
    with pytest.raises(KeyError, match="missing geometry columns"):
        sites_to_geo(df)


def test_attach_apns_via_spatial_join_finds_parcels():
    import geopandas as gpd
    from shapely.geometry import Point, box

    from opentrash.prep.sites import attach_apns_via_spatial_join

    # Two parcels and three sites: A1 contains site1, A2 contains site2,
    # site3 is in no parcel.
    parcels = gpd.GeoDataFrame(
        {"APN": ["A1", "A2"]},
        geometry=[
            box(-117.205, 32.700, -117.195, 32.710),
            box(-117.195, 32.710, -117.185, 32.720),
        ],
        crs="EPSG:4326",
    )
    sites = gpd.GeoDataFrame(
        {"Account_Name": ["site1", "site2", "site3"]},
        geometry=[
            Point(-117.200, 32.705),    # inside A1
            Point(-117.190, 32.715),    # inside A2
            Point(-117.180, 32.730),    # outside both
        ],
        crs="EPSG:4326",
    )
    out = attach_apns_via_spatial_join(sites, parcels)
    assert len(out) == 3
    # Two matches, one NaN.
    by_name = out.set_index("Account_Name")["APN"]
    assert by_name["site1"] == "A1"
    assert by_name["site2"] == "A2"
    assert pd.isna(by_name["site3"])


def test_attach_apns_reprojects_parcels_to_match_sites_crs():
    """If parcels are in a different CRS, the function reprojects parcels in.

    We keep sites in their original CRS so any caller-side rendering stays
    in the same coordinate space they were working in.
    """
    import geopandas as gpd
    from shapely.geometry import Point, box

    from opentrash.prep.sites import attach_apns_via_spatial_join

    parcels_web = gpd.GeoDataFrame(
        {"APN": ["A1"]},
        geometry=[box(-117.205, 32.700, -117.195, 32.710)],
        crs="EPSG:4326",
    )
    # Reproject parcels to the working CRS to set up the cross-CRS case.
    parcels_working = parcels_web.to_crs("EPSG:2230")

    sites_web = gpd.GeoDataFrame(
        {"Account_Name": ["site1"]},
        geometry=[Point(-117.200, 32.705)],
        crs="EPSG:4326",
    )
    out = attach_apns_via_spatial_join(sites_web, parcels_working)
    assert out["APN"].iloc[0] == "A1"
    # Output stays in the sites' original CRS.
    assert str(out.crs).upper().endswith("4326")


def test_attach_apns_missing_apn_column_raises():
    import geopandas as gpd
    from shapely.geometry import Point, box

    from opentrash.prep.sites import attach_apns_via_spatial_join

    parcels = gpd.GeoDataFrame(
        {"PARCEL_NO": ["X"]},     # not "APN"
        geometry=[box(0, 0, 1, 1)],
        crs="EPSG:4326",
    )
    sites = gpd.GeoDataFrame(
        {"Account_Name": ["x"]},
        geometry=[Point(0.5, 0.5)],
        crs="EPSG:4326",
    )
    with pytest.raises(KeyError, match="APN"):
        attach_apns_via_spatial_join(sites, parcels)


def test_attach_apns_no_crs_raises():
    import geopandas as gpd
    from shapely.geometry import Point, box

    from opentrash.prep.sites import attach_apns_via_spatial_join

    parcels = gpd.GeoDataFrame(
        {"APN": ["A1"]},
        geometry=[box(0, 0, 1, 1)],
        crs=None,
    )
    sites = gpd.GeoDataFrame(
        {"Account_Name": ["x"]},
        geometry=[Point(0.5, 0.5)],
        crs="EPSG:4326",
    )
    with pytest.raises(ValueError, match="CRS"):
        attach_apns_via_spatial_join(sites, parcels)
