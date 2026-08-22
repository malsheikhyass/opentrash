"""Tests for opentrash.core.crs."""

import geopandas as gpd
import pytest
from shapely.geometry import box

from opentrash.core.crs import WEB_CRS, WORKING_CRS, to_web, to_working


def _square(crs):
    """A tiny GeoDataFrame in the example region, set to a specific CRS."""
    return gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[box(-117.20, 32.70, -117.19, 32.71)],
        crs=crs,
    )


def test_constants_are_what_we_expect():
    assert WORKING_CRS == "EPSG:2230"
    assert WEB_CRS == "EPSG:4326"


def test_to_working_reprojects_from_web():
    g = _square(WEB_CRS)
    out = to_working(g)
    assert str(out.crs).upper().endswith("2230")
    # Coordinates should now be in feet (large numbers), not degrees.
    minx, miny, _, _ = out.geometry.iloc[0].bounds
    assert abs(minx) > 1000      # feet, not lon degrees


def test_to_web_reprojects_from_working():
    g = _square(WEB_CRS).to_crs(WORKING_CRS)
    out = to_web(g)
    assert str(out.crs).upper().endswith("4326")
    # Back to lon/lat degrees.
    minx, miny, _, _ = out.geometry.iloc[0].bounds
    assert -180 <= minx <= 180
    assert -90 <= miny <= 90


def test_to_working_noop_when_already_in_working():
    g = _square(WEB_CRS).to_crs(WORKING_CRS)
    out = to_working(g)
    # Same CRS in, same CRS out, no error.
    assert str(out.crs).upper() == str(g.crs).upper()


def test_reproject_raises_without_crs():
    g = _square(WEB_CRS)
    g.crs = None
    with pytest.raises(ValueError, match="no CRS"):
        to_working(g)
