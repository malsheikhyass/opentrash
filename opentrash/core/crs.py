"""Coordinate reference systems and reprojection helpers.

The package follows one simple convention:

- **Work in a projected CRS** (here, EPSG:2230 — California State Plane Zone 6,
  US survey feet) for anything involving distances, areas, or spatial joins.
  Projected coordinates are in feet, so distance math is meaningful and fast.
- **Output in EPSG:4326** (WGS84 lat/lon) for anything headed to a web map.
  MapLibre and friends expect lon/lat degrees.

Storing projected and converting to 4326 only at the rendering boundary keeps
every spatial calculation correct and avoids repeated, lossy reprojections.

If your parcels arrive in a different projection, change ``WORKING_CRS`` below
to match your data's CRS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import geopandas as gpd

# The projected CRS the package computes in. EPSG:2230 is California State Plane
# Zone 6 (NAD83), US survey feet — a Southern California example; change
# WORKING_CRS to the appropriate projected CRS for your region.
# Change this to match your own data if you're working elsewhere.
WORKING_CRS = "EPSG:2230"

# The geographic CRS used for web-map output (WGS84 lon/lat degrees).
WEB_CRS = "EPSG:4326"


def to_working(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return ``gdf`` reprojected to the working (projected) CRS.

    Use this before any distance, area, or spatial-join operation.
    """
    return _reproject(gdf, WORKING_CRS)


def to_web(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return ``gdf`` reprojected to the web CRS (WGS84 lon/lat).

    Use this only at the rendering boundary, just before producing map output.
    """
    return _reproject(gdf, WEB_CRS)


def _reproject(gdf: gpd.GeoDataFrame, target: str) -> gpd.GeoDataFrame:
    """Reproject a GeoDataFrame to ``target``, with a clear error if it has no CRS.

    A GeoDataFrame with no CRS is ambiguous — we cannot know what its
    coordinates mean — so we refuse to guess and raise instead.
    """
    if gdf.crs is None:
        raise ValueError(
            "GeoDataFrame has no CRS set. Set one explicitly (e.g. "
            'gdf.set_crs("EPSG:2230")) before reprojecting, so coordinates '
            "are unambiguous."
        )
    if str(gdf.crs).upper() == target.upper():
        # Already in the target CRS; nothing to do.
        return gdf
    return gdf.to_crs(target)
