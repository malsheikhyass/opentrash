"""Tests for opentrash.prep.parcels_wkb."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely import wkb as shp_wkb
from shapely.geometry import box

from opentrash.core.crs import WEB_CRS
from opentrash.prep.parcels_wkb import (
    PARCEL_WKB_COLUMNS,
    build_parcels_wkb,
    write_parcels_wkb,
)


def _synth_parcels(crs=WEB_CRS):
    return gpd.GeoDataFrame(
        {
            "APN":     ["A1", "A2", "A3"],
            "UNITQTY": [1, 4, 2],
            "route_ref": ["22011", "22011", "22021"],
            "route_org": ["32011", "32011", "32021"],
            "route_rec": ["22411O", "22411O", "22421B"],
        },
        geometry=[
            box(-117.20, 32.700, -117.199, 32.701),
            box(-117.21, 32.710, -117.209, 32.711),
            box(-117.22, 32.720, -117.219, 32.721),
        ],
        crs=crs,
    )


def test_build_parcels_wkb_columns_and_types():
    parcels = _synth_parcels()
    out = build_parcels_wkb(parcels)
    assert tuple(out.columns) == PARCEL_WKB_COLUMNS
    assert len(out) == 3
    # bbox columns are populated
    assert out["min_lon"].iloc[0] == pytest.approx(-117.20)
    # APN is a string dtype
    assert out["APN"].dtype.name in ("string", "object", "string[python]")
    # WKB round-trips
    geom = shp_wkb.loads(out["geom_wkb"].iloc[0])
    assert not geom.is_empty


def test_build_parcels_wkb_requires_columns():
    parcels = _synth_parcels().drop(columns=["UNITQTY"])
    with pytest.raises(KeyError, match="UNITQTY"):
        build_parcels_wkb(parcels)


def test_build_parcels_wkb_no_crs_raises():
    parcels = _synth_parcels()
    parcels.crs = None
    with pytest.raises(ValueError, match="no CRS"):
        build_parcels_wkb(parcels)


def test_write_parcels_wkb_round_trips(tmp_path):
    parcels = _synth_parcels()
    out_path = write_parcels_wkb(parcels, tmp_path / "parcels.parquet")
    assert out_path.exists()
    loaded = pd.read_parquet(out_path)
    assert tuple(loaded.columns) == PARCEL_WKB_COLUMNS
    assert len(loaded) == 3
