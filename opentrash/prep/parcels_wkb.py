"""Parcels with WKB geometry + per-route assignment + bbox columns.

The refined-hits builder (Lesson 7) needs per-parcel work that's awkward for
GeoPandas at scale: nearest-point distance via Shapely's C extensions, plus
fast bbox prefilters in DuckDB. The compact way to ship that is a **parquet
file with WKB geometry** and pre-computed bbox columns:

- ``APN`` — the parcel ID
- ``UNITQTY`` — units on the parcel (used downstream for reporting)
- ``route_ref`` / ``route_org`` / ``route_rec`` — which routes serve this parcel
  for each commodity (already on the cleaned sites layer)
- ``min_lon`` / ``min_lat`` / ``max_lon`` / ``max_lat`` — parcel bbox in 4326
- ``geom_wkb`` — the polygon as WKB bytes; Shapely loads it lazily when needed

The result is a single parquet that downstream tools open with ``read_parquet``
(no geo dependencies) and only round-trip through Shapely for the exact
distance step.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..core.crs import WEB_CRS

if TYPE_CHECKING:
    import geopandas as gpd
    import pandas as pd

# The columns expected on the parcels-with-WKB output. Downstream code reads
# these by name, so keep them stable.
PARCEL_WKB_COLUMNS: tuple[str, ...] = (
    "APN",
    "UNITQTY",
    "route_ref",
    "route_org",
    "route_rec",
    "min_lon",
    "min_lat",
    "max_lon",
    "max_lat",
    "geom_wkb",
)


def build_parcels_wkb(
    parcels: gpd.GeoDataFrame,
    *,
    apn_col: str = "APN",
    units_col: str = "UNITQTY",
    route_cols: tuple[str, str, str] = ("route_ref", "route_org", "route_rec"),
) -> pd.DataFrame:
    """Build the parcels-with-WKB DataFrame from a GeoPandas parcel layer.

    The input ``parcels`` must already carry the per-commodity route
    assignments (joined on by the prep pipeline upstream — typically from the
    sites layer in Lesson 3). The output is in EPSG:4326 (web CRS) since both
    the master index and refined hits work in lon/lat.
    """
    import pandas as pd
    from shapely import wkb as shp_wkb  # type: ignore

    if parcels.crs is None:
        raise ValueError("parcels has no CRS. Set one before building WKB output.")
    if str(parcels.crs).upper() != WEB_CRS.upper():
        parcels = parcels.to_crs(WEB_CRS)

    required = (apn_col, units_col, *route_cols)
    missing = [c for c in required if c not in parcels.columns]
    if missing:
        raise KeyError(f"parcels is missing required columns: {missing}")

    bounds = parcels.geometry.bounds
    out = pd.DataFrame({
        "APN": parcels[apn_col].astype("string"),
        "UNITQTY": parcels[units_col].values,
        "route_ref": parcels[route_cols[0]].astype("string"),
        "route_org": parcels[route_cols[1]].astype("string"),
        "route_rec": parcels[route_cols[2]].astype("string"),
        "min_lon": bounds["minx"].values,
        "min_lat": bounds["miny"].values,
        "max_lon": bounds["maxx"].values,
        "max_lat": bounds["maxy"].values,
        "geom_wkb": parcels.geometry.apply(shp_wkb.dumps).values,
    })
    return out[list(PARCEL_WKB_COLUMNS)]


def write_parcels_wkb(
    parcels: gpd.GeoDataFrame,
    out_path: str | Path,
    **kwargs,
) -> Path:
    """Build and write the parcels-with-WKB parquet. Returns the written path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = build_parcels_wkb(parcels, **kwargs)
    df.to_parquet(out_path, index=False)
    return out_path
