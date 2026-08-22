"""The parcel layer — the first real domain object.

A parcel is a unit of property: a lot with a boundary polygon. In waste
operations, parcels are how we reason about *what should be served* — each
serviced address sits on a parcel. This module loads the parcel layer, makes
sure it's in the working CRS, and offers a fast bounding-box prefilter.

The bbox prefilter matters for performance. A full parcel layer can be hundreds
of thousands of polygons. Most spatial questions ("which parcels are near this
route?") only care about a small region. Narrowing by bounding box first — a
cheap numeric comparison — before doing expensive exact-geometry work is the
core optimization that keeps the package fast on a laptop.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..core.crs import WORKING_CRS

if TYPE_CHECKING:
    import geopandas as gpd


def load_parcels(path: str | Path, working_crs: str = WORKING_CRS) -> gpd.GeoDataFrame:
    """Load a parcel layer from disk and ensure it's in the working CRS.

    Parameters
    ----------
    path:
        Path to a vector file readable by GeoPandas/pyogrio — GeoParquet
        (``.parquet``), GeoPackage (``.gpkg``), or similar.
    working_crs:
        The CRS to return the parcels in. Defaults to the package working CRS.

    Returns
    -------
    A GeoDataFrame of parcels in ``working_crs``.

    Notes
    -----
    If the file has no CRS recorded, this raises — we won't guess what the
    coordinates mean. Set the CRS on the source data first.
    """
    import geopandas as gpd

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Parcel file not found: {path}")

    gdf = gpd.read_file(path)

    if gdf.crs is None:
        raise ValueError(
            f"Parcel file {path} has no CRS. Set one on the source data "
            "before loading, so coordinates are unambiguous."
        )

    # Reproject to the working CRS only if needed (to_working handles the
    # already-correct case without a wasted conversion).
    if str(gdf.crs).upper() != working_crs.upper():
        gdf = gdf.to_crs(working_crs)

    return gdf


def bbox_filter(
    gdf: gpd.GeoDataFrame,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
) -> gpd.GeoDataFrame:
    """Return only the parcels whose geometry intersects a bounding box.

    This is the cheap first pass: a spatial-index-backed bounding-box query,
    far faster than exact geometry intersection over the whole layer. Narrow
    with this first, then do exact work on the much smaller result.

    The bounding box must be expressed in the same CRS as ``gdf`` (the working
    CRS, in feet, if the GeoDataFrame came from :func:`load_parcels`).
    """
    # GeopPandas' spatial index makes this a fast bounding-box lookup rather
    # than a full scan. cx is the coordinate-indexer: gdf.cx[xmin:xmax, ymin:ymax].
    return gdf.cx[minx:maxx, miny:maxy]


def bbox_from_geometry(geometry, buffer: float = 0.0):
    """Compute a bounding box ``(minx, miny, maxx, maxy)`` from a geometry.

    Optionally pad the box by ``buffer`` units (feet, in the working CRS) on all
    sides — useful when you want parcels *near* a route, not only those strictly
    inside its extent.
    """
    minx, miny, maxx, maxy = geometry.bounds
    if buffer:
        minx -= buffer
        miny -= buffer
        maxx += buffer
        maxy += buffer
    return (minx, miny, maxx, maxy)
