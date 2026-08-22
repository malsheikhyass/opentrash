"""Static layers — the operational world.

These layers don't change at GPS speeds: routes redrawn when ops adjust them,
landfills and depots when a facility opens or closes. They sit on disk as
shapefiles or GeoParquet and feed the routing engine downstream.

Two layer families this module handles:

- **Routes** — collection-route polygons. Two flavors:
    - **AUTO routes** — large, *engulfing* polygons that wrap ~1,200+ parcels
      each. A truck driving any served street is inside the polygon.
    - **MANUAL routes** (Hard-to-Collect / manual) — the polygons are
      *parcel-shape copies*, not engulfing. A ping in the street next to a
      parcel can fall *outside*. To compensate, MANUAL routes get a buffer
      (default 150 ft in the working CRS) so they behave like AUTO routes
      for spatial-join purposes.

- **Facilities** — one dataset that mixes landfills and depots (and any
  other operational sites). Rows are distinguished by a name column: the row
  whose name matches the configured ``depot_name`` is the depot; everything
  else is a landfill. Landfills get an optional buffer too, so a truck
  *near* a tip (not exactly on the polygon) still registers as tipping.

This module produces the loaded GeoDataFrames; the routing engine in
``opentrash.engine`` consumes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.crs import WEB_CRS, WORKING_CRS

if TYPE_CHECKING:
    import geopandas as gpd
    import pandas as pd

# Default buffer (feet, working CRS) applied to MANUAL routes before bbox extraction.
# AUTO routes are engulfing — no buffer needed. MANUAL needs compensation.
DEFAULT_MANUAL_BUFFER_FT: float = 150.0

# Default buffer (feet, working CRS) applied to landfill polygons.
# Trucks tip on or just outside the polygon footprint; a small buffer keeps
# the "in_tip" flag honest without false positives.
DEFAULT_LANDFILL_BUFFER_FT: float = 50.0


@dataclass(frozen=True)
class FacilitiesConfig:
    """Knobs for facility loading and classification.

    The defaults are illustrative — ``Name`` is the
    column carrying the facility name, and ``Central Yard`` is the depot.
    Override for a different agency's data.
    """

    name_col: str = "Name"
    depot_name: str = "Central Yard"
    landfill_buffer_ft: float = DEFAULT_LANDFILL_BUFFER_FT


def load_route_polygons(
    path: str | Path,
    route_id_col: str,
    *,
    target_crs: str = WEB_CRS,
) -> gpd.GeoDataFrame:
    """Read a route polygon layer (Shapefile / GeoPackage / GeoParquet).

    Drops empty/invalid geometries (attempting :func:`shapely.validation.make_valid`
    first). Returns a GeoDataFrame with two columns: ``route_id`` (as a
    string) and ``geometry``, in ``target_crs``.

    Parameters
    ----------
    path:
        Path to the route layer on disk.
    route_id_col:
        The column in the source file that carries the route identifier.
        Common in municipal data: ``ROUTE_ID``, ``ROUTE_NO``, etc.
    target_crs:
        CRS to return the output in. Default is the web CRS (EPSG:4326),
        matching what the route-bbox index downstream expects.
    """
    import geopandas as gpd
    from shapely.validation import make_valid

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Route layer not found: {path}")

    gdf = gpd.read_file(path)
    if route_id_col not in gdf.columns:
        raise KeyError(
            f"Route ID column {route_id_col!r} not in {path.name}. "
            f"Available columns: {list(gdf.columns)}"
        )

    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = gdf.geometry.apply(lambda g: make_valid(g) if not g.is_valid else g)
    gdf = gdf[[route_id_col, "geometry"]].rename(columns={route_id_col: "route_id"})
    gdf["route_id"] = gdf["route_id"].astype(str)

    if gdf.crs is None:
        raise ValueError(
            f"Route layer {path.name} has no CRS. Set one on the source data "
            "before loading."
        )
    if str(gdf.crs).upper() != target_crs.upper():
        gdf = gdf.to_crs(target_crs)

    return gdf


def buffer_in_working_crs(
    gdf: gpd.GeoDataFrame,
    buffer_ft: float,
) -> gpd.GeoDataFrame:
    """Buffer geometries by feet, returning a GeoDataFrame in the input CRS.

    Buffers are only meaningful in a projected (units = feet) CRS. We round-trip
    through :data:`opentrash.core.crs.WORKING_CRS` to do the math, then return
    to the input CRS for caller compatibility.
    """
    src_crs = gdf.crs
    work = gdf.to_crs(WORKING_CRS) if str(src_crs).upper() != WORKING_CRS.upper() else gdf.copy()
    work["geometry"] = work.geometry.buffer(buffer_ft)
    return work.to_crs(src_crs) if str(src_crs).upper() != WORKING_CRS.upper() else work


def route_bboxes(
    routes: gpd.GeoDataFrame,
    *,
    edge_buffer_deg: float = 100 / 335000.0,
) -> pd.DataFrame:
    """Reduce route polygons to one bounding box per ``route_id``.

    Returns a plain DataFrame with columns ``route_id``, ``rte_min_lon``,
    ``rte_min_lat``, ``rte_max_lon``, ``rte_max_lat``. A small
    ``edge_buffer_deg`` (default ~100 ft in lat-degree units) is added on
    every side so a ping exactly on a boundary still matches — bbox math is
    cheap, being a hair generous costs nothing.

    Multiple polygons sharing a ``route_id`` are dissolved into the
    encompassing bbox (a route can span several disjoint pieces).
    """
    import pandas as pd

    bounds = routes.geometry.bounds
    rbbox = pd.DataFrame({
        "route_id": routes["route_id"].astype(str).values,
        "rte_min_lon": bounds["minx"].values,
        "rte_min_lat": bounds["miny"].values,
        "rte_max_lon": bounds["maxx"].values,
        "rte_max_lat": bounds["maxy"].values,
    })
    out = rbbox.groupby("route_id").agg(
        rte_min_lon=("rte_min_lon", "min"),
        rte_min_lat=("rte_min_lat", "min"),
        rte_max_lon=("rte_max_lon", "max"),
        rte_max_lat=("rte_max_lat", "max"),
    ).reset_index()

    out["rte_min_lon"] -= edge_buffer_deg
    out["rte_min_lat"] -= edge_buffer_deg
    out["rte_max_lon"] += edge_buffer_deg
    out["rte_max_lat"] += edge_buffer_deg

    return out


def build_route_index(
    auto_path: str | Path,
    manual_path: str | Path | None = None,
    *,
    auto_route_col: str = "ROUTE_ID",
    manual_route_col: str = "ROUTE_ID",
    manual_buffer_ft: float = DEFAULT_MANUAL_BUFFER_FT,
) -> pd.DataFrame:
    """Build the route bbox index that the engine joins GPS pings against.

    Loads the AUTO layer (engulfing — no buffer) and, if provided, the MANUAL
    layer (parcel-shape copies — buffered by ``manual_buffer_ft`` to compensate).
    Routes appearing in both layers keep their AUTO geometry; AUTO takes
    precedence.

    Returns a DataFrame with one row per ``route_id``, bbox in EPSG:4326.
    """
    import pandas as pd

    auto = load_route_polygons(auto_path, auto_route_col, target_crs=WEB_CRS)

    if manual_path is None:
        return route_bboxes(auto)

    manual = load_route_polygons(manual_path, manual_route_col, target_crs=WEB_CRS)
    # Keep only MANUAL routes not already in AUTO; buffer them in working-CRS feet.
    manual_only = manual[~manual["route_id"].isin(auto["route_id"])].copy()
    if not manual_only.empty:
        manual_only = buffer_in_working_crs(manual_only, manual_buffer_ft)

    combined = pd.concat([auto, manual_only], ignore_index=True)
    return route_bboxes(combined)


def load_facilities(
    path: str | Path,
    *,
    config: FacilitiesConfig | None = None,
    target_crs: str = WORKING_CRS,
) -> dict[str, gpd.GeoDataFrame]:
    """Load facilities and split into ``landfill`` and ``depot`` GeoDataFrames.

    The source file is a single layer holding both landfills and depots (and
    optionally other organization sites). Rows are split by the name column:
    the row whose ``config.name_col`` matches ``config.depot_name`` is the
    depot; every other row is treated as a landfill.

    Landfills are optionally buffered by ``config.landfill_buffer_ft`` so a
    truck *near* the polygon (not exactly on it) still registers as tipping.

    Returns
    -------
    A dict with keys ``"landfill"`` and ``"depot"``, each a GeoDataFrame in
    ``target_crs`` (default: working CRS).

    Raises
    ------
    ValueError
        If the depot row isn't found by name, or the source file has no CRS.
    """
    import geopandas as gpd

    cfg = config or FacilitiesConfig()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Facilities layer not found: {path}")

    fac = gpd.read_file(path) if path.suffix.lower() != ".parquet" else gpd.read_parquet(path)

    if fac.crs is None:
        raise ValueError(
            f"Facilities layer {path.name} has no CRS. Set one on the source data."
        )
    if cfg.name_col not in fac.columns:
        raise KeyError(
            f"Name column {cfg.name_col!r} not in {path.name}. "
            f"Available columns: {list(fac.columns)}"
        )

    # Work in the working CRS for accurate buffer math; reproject once at the end.
    if str(fac.crs).upper() != WORKING_CRS.upper():
        fac = fac.to_crs(WORKING_CRS)

    name_series = fac[cfg.name_col].astype(str)
    depot = fac[name_series == cfg.depot_name].copy()
    landfill = fac[name_series != cfg.depot_name].copy()

    if depot.empty:
        raise ValueError(
            f"Depot row not found in {path.name}: no row where "
            f"{cfg.name_col!r} == {cfg.depot_name!r}."
        )

    if not landfill.empty and cfg.landfill_buffer_ft:
        landfill["geometry"] = landfill.geometry.buffer(cfg.landfill_buffer_ft)

    # Reproject to target_crs at the very end.
    if target_crs.upper() != WORKING_CRS.upper():
        landfill = landfill.to_crs(target_crs)
        depot = depot.to_crs(target_crs)

    return {"landfill": landfill, "depot": depot}
