"""Routing engine — the architectural heart of the package.

This module does the **one spatial join the package needs**. Every GPS ping
gets joined, in a single pass, to:

- The route polygon it falls inside (or NULL if not on any route)
- The parcel it's nearest to (or NULL if not near any parcel)
- Whether it's inside any landfill polygon
- Whether it's at the depot

The output is a stream of **enriched pings**: the canonical 6-column GPS
shape plus four new columns (``route_id``, ``apn``, ``in_landfill``,
``at_depot``). Every downstream module — pattern detection, RouteView,
business reports — consumes enriched pings. None of them does its own
spatial join.

That's the rule that drives the v2 architecture:
**spatial joins are infrastructure; products are calculations.**

Implementation: DuckDB with the spatial extension. The whole enrichment
is one ``CREATE TABLE AS SELECT`` against pings + parcels_wkb (from
Lesson 7) + a few in-memory polygon tables. Bbox prefiltering on the
parcel join keeps it fast even on millions of pings.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .config import DEFAULT_CONFIG, EngineConfig

if TYPE_CHECKING:
    import geopandas as gpd
    import pandas as pd

# Canonical column order for an enriched-pings parquet. Downstream readers
# (segments, patterns, routeview) depend on this shape.
ENRICHED_COLUMNS: tuple[str, ...] = (
    # Original GPS columns (from adapters.gps.base.GPS_COLUMNS)
    "vehicle_id",
    "dt_utc",
    "dt_local",
    "lat",
    "lon",
    "speed_mph",
    # Engine-added enrichment columns
    "route_id",       # the route polygon containing the ping, or NULL
    "apn",            # the nearest parcel (within parcel_edge_ft), or NULL
    "in_landfill",    # bool: is the ping inside any landfill polygon?
    "at_depot",       # bool: is the ping within depot_radius_ft of the depot?
)


def enriched_path(out_root: str | Path, day: str, vehicle_id: str) -> Path:
    """Where one vehicle-day's enriched pings live on disk.

    Layout mirrors :func:`opentrash.cache.gps_cache.cache_path` —
    ``<out_root>/<YYYY-MM-DD>/<vehicle_id>.parquet`` — so each lesson's
    output can sit alongside its input on disk.
    """
    out_root = Path(out_root)
    return out_root / day / f"{vehicle_id}.parquet"


def enrich_pings(
    pings: pd.DataFrame,
    *,
    parcels_wkb_path: str | Path,
    routes_gdf: gpd.GeoDataFrame,
    landfills_gdf: gpd.GeoDataFrame,
    depot_gdf: gpd.GeoDataFrame,
    config: EngineConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Enrich a batch of GPS pings against all operational GIS layers.

    The single spatial-join entry point for the package. Joins each ping to:

    - The route polygon containing it (point-in-polygon, all routes)
    - The nearest parcel (within ``config.parcel_edge_ft``)
    - Any landfill polygon containing it
    - The depot, by centroid radius

    Parameters
    ----------
    pings:
        Canonical GPS shape: ``vehicle_id``, ``dt_utc``, ``dt_local``,
        ``lat``, ``lon``, ``speed_mph``. Typically one vehicle-day's
        cache parquet.
    parcels_wkb_path:
        Path to the WKB parcels parquet produced by
        :func:`opentrash.prep.parcels_wkb.write_parcels_wkb`. DuckDB
        reads it directly.
    routes_gdf:
        Route polygons in EPSG:4326, columns ``route_id``, ``geometry``.
        Output of :func:`opentrash.prep.static_layers.load_route_polygons`.
    landfills_gdf:
        Landfill polygons in EPSG:4326 (the ``"landfill"`` entry from
        :func:`opentrash.prep.static_layers.load_facilities`, reprojected
        to web CRS).
    depot_gdf:
        The depot polygon(s) in EPSG:4326 (the ``"depot"`` entry from
        :func:`opentrash.prep.static_layers.load_facilities`, reprojected
        to web CRS).
    config:
        Engine knobs. Defaults to :data:`DEFAULT_CONFIG`.

    Returns
    -------
    A DataFrame in :data:`ENRICHED_COLUMNS` order. Same number of rows as
    ``pings`` — enrichment columns are added per-row, never filtering.
    """
    import duckdb

    if pings.empty:
        return _empty_enriched_frame()

    parcels_wkb_path = Path(parcels_wkb_path)

    con = duckdb.connect(":memory:")
    try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")

        # Register inputs as DuckDB views.
        con.register("pings", pings)
        con.register("routes", _gdf_to_wkb_df(routes_gdf, ["route_id"]))
        con.register("landfills", _gdf_to_wkb_df(landfills_gdf, []))
        con.register("depot", _gdf_to_wkb_df(depot_gdf, []))

        # Pre-compute the depot centroid + radius (in degrees, approx) once.
        # 1 ft ≈ 1/364320 deg latitude; this is small over a county-scale
        # extent so the approximation is fine.
        depot_radius_deg = config.depot_radius_ft / 364_320.0
        parcel_edge_deg = config.parcel_edge_ft / 364_320.0

        # The single CTAS. One pass through pings; four enrichments per row.
        # Each layer is a LEFT JOIN so pings are never dropped — missing
        # matches become NULL/false.
        sql = f"""
        WITH ping_routes AS (
            SELECT
                p.vehicle_id, p.dt_utc, p.dt_local, p.lat, p.lon, p.speed_mph,
                r.route_id AS route_id
            FROM pings p
            LEFT JOIN routes r
                ON ST_Within(ST_Point(p.lon, p.lat), ST_GeomFromWKB(r.geom_wkb))
        ),
        ping_parcels AS (
            -- Parcel attribution: nearest parcel within parcel_edge_deg of the ping.
            -- Bbox prefilter via the precomputed min/max columns on parcels_wkb;
            -- then point-in-buffered-polygon for exactness. Pings moving faster
            -- than slow_mph_max get no parcel attribution.
            SELECT
                pr.vehicle_id, pr.dt_utc, pr.dt_local, pr.lat, pr.lon,
                pr.speed_mph, pr.route_id,
                pc.APN AS apn
            FROM ping_routes pr
            LEFT JOIN '{parcels_wkb_path.as_posix()}' pc
                ON pr.speed_mph <= {config.slow_mph_max}
               AND pr.lon BETWEEN pc.min_lon - {parcel_edge_deg}
                                AND pc.max_lon + {parcel_edge_deg}
               AND pr.lat BETWEEN pc.min_lat - {parcel_edge_deg}
                                AND pc.max_lat + {parcel_edge_deg}
               AND ST_DWithin(
                       ST_Point(pr.lon, pr.lat),
                       ST_GeomFromWKB(pc.geom_wkb),
                       {parcel_edge_deg}
                   )
        ),
        ping_landfills AS (
            SELECT
                pp.*,
                EXISTS (
                    SELECT 1 FROM landfills lf
                    WHERE ST_Within(ST_Point(pp.lon, pp.lat),
                                    ST_GeomFromWKB(lf.geom_wkb))
                ) AS in_landfill
            FROM ping_parcels pp
        ),
        ping_depot AS (
            SELECT
                pl.*,
                EXISTS (
                    SELECT 1 FROM depot dp
                    WHERE ST_DWithin(
                              ST_Point(pl.lon, pl.lat),
                              ST_Centroid(ST_GeomFromWKB(dp.geom_wkb)),
                              {depot_radius_deg}
                          )
                ) AS at_depot
            FROM ping_landfills pl
        )
        SELECT
            vehicle_id, dt_utc, dt_local, lat, lon, speed_mph,
            route_id, apn, in_landfill, at_depot
        FROM ping_depot
        ORDER BY vehicle_id, dt_utc
        """
        result = con.execute(sql).df()
    finally:
        con.close()

    # Coerce the boolean columns to plain Python bool (DuckDB returns numpy
    # bool which can be surprising downstream).
    result["in_landfill"] = result["in_landfill"].fillna(False).astype(bool)
    result["at_depot"] = result["at_depot"].fillna(False).astype(bool)

    return result[list(ENRICHED_COLUMNS)]


def enrich_vehicle_day(
    cache_path: str | Path,
    out_root: str | Path,
    *,
    parcels_wkb_path: str | Path,
    routes_gdf: gpd.GeoDataFrame,
    landfills_gdf: gpd.GeoDataFrame,
    depot_gdf: gpd.GeoDataFrame,
    config: EngineConfig = DEFAULT_CONFIG,
) -> Path:
    """Enrich one vehicle-day cache file → write to ``out_root/<day>/<vehicle>.parquet``.

    The day and vehicle_id are inferred from the input path's standard layout:
    ``cache/YYYY-MM-DD/<vehicle_id>.parquet``.

    Returns
    -------
    The output path written.
    """
    import pandas as pd

    cache_path = Path(cache_path)
    if not cache_path.exists():
        raise FileNotFoundError(f"GPS cache file not found: {cache_path}")

    day = cache_path.parent.name             # "YYYY-MM-DD"
    vehicle_id = cache_path.stem             # filename without extension

    pings = pd.read_parquet(cache_path)
    enriched = enrich_pings(
        pings,
        parcels_wkb_path=parcels_wkb_path,
        routes_gdf=routes_gdf,
        landfills_gdf=landfills_gdf,
        depot_gdf=depot_gdf,
        config=config,
    )

    out_path = enriched_path(out_root, day, vehicle_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_parquet(out_path, index=False)
    return out_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _gdf_to_wkb_df(
    gdf: gpd.GeoDataFrame,
    keep_cols: list[str],
) -> pd.DataFrame:
    """Convert a GeoDataFrame to a plain DataFrame with a ``geom_wkb`` column.

    DuckDB consumes geometry through ``ST_GeomFromWKB(bytes)``, so we ship
    the WKB representation across rather than letting DuckDB try to read a
    Shapely object directly.
    """
    import pandas as pd

    if gdf.empty:
        cols = list(keep_cols) + ["geom_wkb"]
        return pd.DataFrame({c: [] for c in cols})

    df = pd.DataFrame({c: gdf[c].values for c in keep_cols})
    df["geom_wkb"] = gdf.geometry.apply(lambda g: g.wkb if g is not None else None)
    return df


def _empty_enriched_frame() -> pd.DataFrame:
    """An empty DataFrame in :data:`ENRICHED_COLUMNS` shape, for empty inputs."""
    import pandas as pd

    return pd.DataFrame({c: [] for c in ENRICHED_COLUMNS})
