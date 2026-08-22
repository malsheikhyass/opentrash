"""Tests for opentrash.prep.parcels."""

import geopandas as gpd
import pytest
from shapely.geometry import box

from opentrash.core.crs import WEB_CRS, WORKING_CRS
from opentrash.prep.parcels import bbox_filter, bbox_from_geometry, load_parcels


def _three_parcels_4326():
    return gpd.GeoDataFrame(
        {"APN": ["A1", "A2", "A3"]},
        geometry=[
            box(-117.205, 32.705, -117.200, 32.710),
            box(-117.190, 32.715, -117.185, 32.720),
            box(-117.170, 32.730, -117.165, 32.735),
        ],
        crs=WEB_CRS,
    )


def test_load_parcels_round_trips_and_reprojects(tmp_path):
    src = _three_parcels_4326()
    path = tmp_path / "parcels.gpkg"
    src.to_file(path)

    out = load_parcels(path)
    assert len(out) == 3
    # Default working CRS is EPSG:2230.
    assert str(out.crs).upper().endswith("2230")


def test_load_parcels_keeps_same_crs_when_already_correct(tmp_path):
    src = _three_parcels_4326().to_crs(WORKING_CRS)
    path = tmp_path / "parcels_in_working.gpkg"
    src.to_file(path)

    out = load_parcels(path, working_crs=WORKING_CRS)
    assert str(out.crs).upper() == str(src.crs).upper()


def test_load_parcels_no_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_parcels(tmp_path / "nope.gpkg")


def test_load_parcels_no_crs_raises(tmp_path, monkeypatch):
    """If the loaded file has no CRS, load_parcels refuses to guess.

    We mock geopandas.read_file rather than trying to write a no-CRS file
    (most geo IO backends refuse to do that).
    """
    import geopandas as gpd
    no_crs_gdf = gpd.GeoDataFrame(
        {"APN": ["A1"]},
        geometry=[box(-117.20, 32.70, -117.19, 32.71)],
        crs=None,
    )
    path = tmp_path / "anything.gpkg"
    path.touch()                                          # so the existence check passes
    monkeypatch.setattr(gpd, "read_file", lambda p: no_crs_gdf)
    with pytest.raises(ValueError, match="no CRS"):
        load_parcels(path)


def test_bbox_filter_narrows_to_box():
    g = _three_parcels_4326()
    # Box covering only the first parcel
    out = bbox_filter(g, -117.21, 32.70, -117.20, 32.711)
    assert set(out["APN"]) == {"A1"}


def test_bbox_filter_empty_when_no_overlap():
    g = _three_parcels_4326()
    out = bbox_filter(g, 0, 0, 1, 1)
    assert len(out) == 0


def test_bbox_from_geometry_no_buffer():
    geom = box(0, 0, 10, 20)
    assert bbox_from_geometry(geom) == (0.0, 0.0, 10.0, 20.0)


def test_bbox_from_geometry_with_buffer():
    geom = box(0, 0, 10, 20)
    out = bbox_from_geometry(geom, buffer=5)
    assert out == (-5.0, -5.0, 15.0, 25.0)
