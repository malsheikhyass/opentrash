"""Evaluate each parcel on a route for one (day, vehicle).

For each parcel polygon on the route:

- **Served**: did any enriched ping for this (day, vehicle) carry this APN?
- **Expected** (optional, from L10 patterns): what does the long-period
  pattern analysis say the typical vehicle is for this parcel?

The output is a parcels GeoJSON FeatureCollection with one Feature per
parcel, properties carrying the served/expected/actual story for popups.
The L11 template's parcel popups read these properties.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Color per status. Centralized so render.py can reference identical values
# in the MapLibre style.
SERVED_COLOR = "#16a34a"        # green-600
MISSED_COLOR = "#dc2626"        # red-600
UNKNOWN_COLOR = "#94a3b8"       # slate-400 (no expectation, not served)


def build_parcels_geojson(
    parcels_wkb_path: str | Path,
    enriched_glob: str | Path,
    route_id: str,
    day: str,
    vehicle_id: str,
    *,
    patterns_chunk_path: str | Path | None = None,
) -> str:
    """Return a GeoJSON FeatureCollection of parcels with served/expected props.

    Parameters
    ----------
    parcels_wkb_path:
        Path to the parcels_wkb parquet (L7 output). Provides parcel
        geometries + route_ref/org/rec sidecar columns.
    enriched_glob:
        Glob or path matching enriched-pings parquets for the day. We
        filter internally to (vehicle_id, route_id, day, apn IS NOT NULL).
    route_id, day, vehicle_id:
        The triple this parcel evaluation is scoped to.
    patterns_chunk_path:
        Optional path to the L10 patterns parquet for this route (typically
        ``patterns/<window>/by_route/<route_id>.parquet``). If provided,
        the popup gets ``expected_vehicle_weekly1`` /
        ``expected_vehicle_weekly2`` / ``expected_vehicle_biweekly`` fields.

    Returns
    -------
    A JSON string. Each Feature carries: ``APN``, ``route_ref``,
    ``route_org``, ``route_rec``, ``served`` (bool), ``status`` (one of
    ``served`` / ``missed`` / ``unknown``), ``fill_color``, optional
    expected-vehicle fields, optional ``actual_vehicle``.
    """
    import glob as _glob

    import geopandas as gpd
    import pandas as pd
    from shapely import wkb

    # 1. Load parcels for this route only
    parcels_wkb_path = Path(parcels_wkb_path)
    parcels = pd.read_parquet(parcels_wkb_path)
    on_route = (
        (parcels["route_ref"].astype(str) == str(route_id))
        | (parcels["route_org"].astype(str) == str(route_id))
        | (parcels["route_rec"].astype(str) == str(route_id))
    )
    route_parcels = parcels[on_route].copy()

    if route_parcels.empty:
        return _empty_fc()

    # 2. Identify which APNs were served today by this vehicle
    paths = _glob.glob(str(enriched_glob), recursive=True)
    if not paths:
        served_apns: set[str] = set()
    else:
        enriched = pd.concat(
            [pd.read_parquet(p, columns=["vehicle_id", "dt_local",
                                          "apn", "route_id"])
             for p in paths],
            ignore_index=True,
        )
        target_date = pd.Timestamp(day).date()
        today_hits = enriched[
            (enriched["vehicle_id"].astype(str) == str(vehicle_id))
            & (enriched["dt_local"].dt.date == target_date)
            & enriched["apn"].notna()
        ]
        served_apns = set(today_hits["apn"].astype(str).unique())

    # 3. Optional: load patterns chunk for this route -> expected vehicles
    expected: dict[str, dict[str, str | None]] = {}
    if patterns_chunk_path is not None:
        patterns_chunk_path = Path(patterns_chunk_path)
        if patterns_chunk_path.exists():
            patt = pd.read_parquet(
                patterns_chunk_path,
                columns=["APN", "weekly1_vehicle", "weekly2_vehicle", "biweekly_vehicle"],
            )
            for row in patt.itertuples(index=False):
                expected[str(row.APN)] = {
                    "weekly1": (str(row.weekly1_vehicle)
                                if row.weekly1_vehicle is not None
                                and str(row.weekly1_vehicle) != "nan"
                                else None),
                    "weekly2": (str(row.weekly2_vehicle)
                                if row.weekly2_vehicle is not None
                                and str(row.weekly2_vehicle) != "nan"
                                else None),
                    "biweekly": (str(row.biweekly_vehicle)
                                 if row.biweekly_vehicle is not None
                                 and str(row.biweekly_vehicle) != "nan"
                                 else None),
                }

    # 4. Build the GeoJSON features
    features = []
    for row in route_parcels.itertuples(index=False):
        apn = str(row.APN)
        geom = wkb.loads(row.wkb_2230)         # working CRS
        # Reproject to web CRS for MapLibre
        geom_web = (
            gpd.GeoSeries([geom], crs="EPSG:2230")
            .to_crs("EPSG:4326")
            .iloc[0]
        )
        served = apn in served_apns
        # Did today's vehicle do this parcel?
        actual_today = str(vehicle_id) if served else None

        exp = expected.get(apn, {})
        # Status: 'served' if vehicle was there today, 'missed' if patterns
        # expected someone here but no one came, 'unknown' otherwise.
        if served:
            status = "served"
            fill = SERVED_COLOR
        elif any(exp.get(k) for k in ("weekly1", "weekly2", "biweekly")):
            status = "missed"
            fill = MISSED_COLOR
        else:
            status = "unknown"
            fill = UNKNOWN_COLOR

        properties = {
            "APN": apn,
            "route_ref": (str(row.route_ref) if row.route_ref is not None else None),
            "route_org": (str(row.route_org) if row.route_org is not None else None),
            "route_rec": (str(row.route_rec) if row.route_rec is not None else None),
            "served": served,
            "status": status,
            "fill_color": fill,
            "actual_vehicle": actual_today,
            "expected_vehicle_weekly1": exp.get("weekly1"),
            "expected_vehicle_weekly2": exp.get("weekly2"),
            "expected_vehicle_biweekly": exp.get("biweekly"),
            # Legacy compat for the bundled template's popup which expects
            # is_served (0/1) and served_kind:
            "is_served": 1 if served else 0,
            "served_kind": "Today" if served else "",
        }
        features.append({
            "type": "Feature",
            "geometry": _shapely_to_geojson(geom_web),
            "properties": properties,
        })

    return json.dumps({"type": "FeatureCollection", "features": features})


def _shapely_to_geojson(geom) -> dict:
    """Tiny shapely -> GeoJSON dict adapter."""
    import shapely.geometry
    return shapely.geometry.mapping(geom)


def _empty_fc() -> str:
    return json.dumps({"type": "FeatureCollection", "features": []})
