"""RouteView runner — the file-in / HTML-out driver.

Three modes:

- :func:`render_routeview` — one (route, day, vehicle) → one HTML.
- :func:`render_all_routes_for_day` — all routes touched on a given day →
  many HTMLs (one per route × top vehicle).
- :func:`render_top_n_vehicles` — one day, top-N vehicles across all the
  routes they touched → one HTML per (route, vehicle) pair.

All three call :func:`render_routeview` under the hood; they differ only
in how they iterate the (route, day, vehicle) tuples.

Output layout::

    routeview/<YYYY-MM-DD>/<route_id>__<vehicle_id>.html
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .parcel_eval import build_parcels_geojson
from .rank import rank_vehicles_for_route_day
from .render import build_routeview_html
from .trail import build_trail_geojson

if TYPE_CHECKING:
    pass


def render_routeview(
    *,
    enriched_root: str | Path,
    segments_root: str | Path,
    parcels_wkb_path: str | Path,
    routes_path: str | Path,
    facilities_path: str | Path,
    out_root: str | Path,
    route_id: str,
    day: dt.date | str,
    vehicle_id: str,
    patterns_root: str | Path | None = None,
    patterns_window: str | None = None,
    tonnage_root: str | Path | None = None,
    field_note: str = "",
    scale_note: str = "",
) -> Path:
    """Render one (route, day, vehicle) HTML to disk; return the path.

    All inputs are paths/roots produced by prior lessons. The function
    locates the right sub-paths, builds the four JSON blobs the template
    needs, calls :func:`build_routeview_html`, writes the result.
    """
    import geopandas as gpd

    day_iso = day.isoformat() if isinstance(day, dt.date) else str(day)
    day_date = dt.date.fromisoformat(day_iso)

    enriched_root = Path(enriched_root)
    segments_root = Path(segments_root)
    out_root = Path(out_root)

    # 1. Enriched-pings glob for the day
    enriched_day = enriched_root / day_iso
    enriched_glob = str(enriched_day / "*.parquet")

    # 2. L9 segments timeline for this vehicle-day
    segments_path = segments_root / "timeline" / day_iso / f"{vehicle_id}.parquet"

    # 3. Route polygon (subset of routes_path to this route_id, web CRS)
    routes_gdf = gpd.read_parquet(routes_path)
    route_geom = routes_gdf[routes_gdf["route_id"].astype(str) == str(route_id)]
    if route_geom.empty:
        raise ValueError(f"Route {route_id!r} not found in {routes_path}")
    route_web = route_geom.to_crs("EPSG:4326")
    route_json = route_web.to_json()

    # 4. Facilities (landfills + depot), web CRS
    facilities_gdf = gpd.read_parquet(facilities_path).to_crs("EPSG:4326")
    landfill = facilities_gdf[facilities_gdf["Name"] != "Central Yard"]
    sites = facilities_gdf
    lf_json = landfill.to_json()
    sites_json = sites.to_json()

    # 5. Trail GeoJSON (colored ping dots)
    pts_json = build_trail_geojson(
        enriched_glob=enriched_glob,
        segments_timeline_path=segments_path,
        route_id=route_id,
        day=day_iso,
        vehicle_id=vehicle_id,
    )

    # 6. Parcels GeoJSON with served/expected props
    patterns_chunk_path = None
    if patterns_root is not None and patterns_window is not None:
        candidate = (Path(patterns_root) / patterns_window
                     / "by_route" / f"{route_id}.parquet")
        if candidate.exists():
            patterns_chunk_path = candidate
    parcels_json = build_parcels_geojson(
        parcels_wkb_path=parcels_wkb_path,
        enriched_glob=enriched_glob,
        route_id=route_id,
        day=day_iso,
        vehicle_id=vehicle_id,
        patterns_chunk_path=patterns_chunk_path,
    )

    # 7. Summary + stats panels from L9 segments
    summary_json, stats_json_enriched, day_time_label = _build_summary_stats(
        segments_path, vehicle_id=vehicle_id, day=day_iso,
        tonnage_root=tonnage_root,
    )

    # 8. Map center: route polygon centroid
    centroid = route_web.geometry.iloc[0].centroid
    center_lon, center_lat = float(centroid.x), float(centroid.y)

    html = build_routeview_html(
        route_id=str(route_id),
        vehicle=str(vehicle_id),
        day=day_date,
        day_time_label=day_time_label,
        route_json=route_json,
        lf_json=lf_json,
        pts_json=pts_json,
        parcels_json=parcels_json,
        sites_json=sites_json,
        summary_json=summary_json,
        stats_json_enriched=stats_json_enriched,
        center_lon=center_lon,
        center_lat=center_lat,
        field_note=field_note,
        scale_note=scale_note,
    )

    out_dir = out_root / day_iso
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{route_id}__{vehicle_id}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def render_all_routes_for_day(
    *,
    enriched_root: str | Path,
    segments_root: str | Path,
    parcels_wkb_path: str | Path,
    routes_path: str | Path,
    facilities_path: str | Path,
    out_root: str | Path,
    day: dt.date | str,
    routes: list[str] | None = None,
    patterns_root: str | Path | None = None,
    patterns_window: str | None = None,
    tonnage_root: str | Path | None = None,
    min_pings: int = 5,
) -> list[Path]:
    """Render every route touched on a given day → one HTML per (route, top vehicle).

    For each route, the top-1 vehicle (by ping count, via
    :func:`rank_vehicles_for_route_day`) is picked. Returns a list of
    written paths.
    """
    import geopandas as gpd

    day_iso = day.isoformat() if isinstance(day, dt.date) else str(day)
    enriched_root = Path(enriched_root)
    enriched_glob = str(enriched_root / day_iso / "*.parquet")

    if routes is None:
        routes_gdf = gpd.read_parquet(routes_path)
        routes = (
            routes_gdf["route_id"].astype(str).dropna().unique().tolist()
        )

    written: list[Path] = []
    for route_id in routes:
        ranked = rank_vehicles_for_route_day(
            enriched_glob, route_id=route_id, day=day_iso,
            min_pings=min_pings,
        )
        if ranked.empty:
            continue
        top_vehicle = str(ranked.iloc[0]["vehicle_id"])
        path = render_routeview(
            enriched_root=enriched_root,
            segments_root=segments_root,
            parcels_wkb_path=parcels_wkb_path,
            routes_path=routes_path,
            facilities_path=facilities_path,
            out_root=out_root,
            route_id=route_id,
            day=day_iso,
            vehicle_id=top_vehicle,
            patterns_root=patterns_root,
            patterns_window=patterns_window,
            tonnage_root=tonnage_root,
        )
        written.append(path)
    return written


def render_top_n_vehicles(
    *,
    enriched_root: str | Path,
    segments_root: str | Path,
    parcels_wkb_path: str | Path,
    routes_path: str | Path,
    facilities_path: str | Path,
    out_root: str | Path,
    day: dt.date | str,
    top_n: int = 5,
    patterns_root: str | Path | None = None,
    patterns_window: str | None = None,
    tonnage_root: str | Path | None = None,
    min_pings: int = 5,
) -> list[Path]:
    """For one day, render the top-N vehicles across all the routes they touched.

    Picks the N vehicles with the most pings across the whole day, then
    for each, renders an HTML for each route they meaningfully touched
    (>= min_pings on that route).
    """
    import glob as _glob

    import pandas as pd

    day_iso = day.isoformat() if isinstance(day, dt.date) else str(day)
    enriched_root = Path(enriched_root)
    enriched_glob = str(enriched_root / day_iso / "*.parquet")

    # Rank vehicles across the whole day
    paths = _glob.glob(enriched_glob)
    if not paths:
        return []
    all_pings = pd.concat(
        [pd.read_parquet(p, columns=["vehicle_id", "dt_local", "route_id"])
         for p in paths],
        ignore_index=True,
    )
    target = pd.Timestamp(day_iso).date()
    today = all_pings[all_pings["dt_local"].dt.date == target]
    if today.empty:
        return []
    top_vehicles = (
        today.groupby("vehicle_id").size().sort_values(ascending=False)
        .head(top_n).index.tolist()
    )

    written: list[Path] = []
    for vehicle_id in top_vehicles:
        # For each vehicle, find which routes they actually touched today
        veh_pings = today[today["vehicle_id"] == vehicle_id]
        route_counts = (
            veh_pings.groupby("route_id").size()
            .loc[lambda s: s >= min_pings].index.dropna().tolist()
        )
        for route_id in route_counts:
            path = render_routeview(
                enriched_root=enriched_root,
                segments_root=segments_root,
                parcels_wkb_path=parcels_wkb_path,
                routes_path=routes_path,
                facilities_path=facilities_path,
                out_root=out_root,
                route_id=str(route_id),
                day=day_iso,
                vehicle_id=str(vehicle_id),
                patterns_root=patterns_root,
                patterns_window=patterns_window,
                tonnage_root=tonnage_root,
            )
            written.append(path)
    return written


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_summary_stats(
    segments_path: Path,
    *,
    vehicle_id: str,
    day: str,
    tonnage_root: str | Path | None,
) -> tuple[str, str, str]:
    """Build ``summary_json``, ``stats_json_enriched``, and ``day_time_label``.

    Reads L9 segments to compute load count, shift start/end, total miles.
    Optionally enriches with tonnage from the L5 output (joined by
    vehicle + dump-segment time window).
    """
    import pandas as pd

    if not segments_path.exists():
        return (
            json.dumps({}),
            json.dumps({"loads": []}),
            "—",
        )

    seg = pd.read_parquet(segments_path)
    if seg.empty:
        return json.dumps({}), json.dumps({"loads": []}), "—"

    # Shift bounds: earliest start, latest end
    start = pd.Timestamp(seg["start_dt_local"].min())
    end = pd.Timestamp(seg["end_dt_local"].max())
    day_time_label = f"{start.strftime('%H:%M')} → {end.strftime('%H:%M')}"

    active = seg[~seg["segment_type"].isin(["depot_departure", "depot_arrival"])]
    n_loads = int(seg["load_number"].dropna().max() or 0)
    total_miles = float(active["miles"].sum())

    summary = {
        "vehicle_id": vehicle_id,
        "day": day,
        "shift_start": start.strftime("%H:%M"),
        "shift_end": end.strftime("%H:%M"),
        "n_loads": n_loads,
        "n_segments": int(len(seg)),
        "total_miles": round(total_miles, 2),
    }

    # Per-load breakdown
    loads = []
    for load_num, group in active.groupby("load_number"):
        if pd.isna(load_num):
            continue
        load_start = pd.Timestamp(group["start_dt_local"].min())
        load_end = pd.Timestamp(group["end_dt_local"].max())
        dump = group[group["segment_type"] == "dump"]
        load_entry = {
            "load_number": int(load_num),
            "start": load_start.strftime("%H:%M"),
            "end": load_end.strftime("%H:%M"),
            "miles": round(float(group["miles"].sum()), 2),
            "duration_seconds": float((load_end - load_start).total_seconds()),
            "tonnage_tons": None,           # filled below if tonnage available
            "landfill": None,
        }
        if not dump.empty:
            load_entry["landfill"] = "Landfill"     # placeholder; real lookup below
            if tonnage_root is not None:
                tonnage = _lookup_load_tonnage(
                    tonnage_root=Path(tonnage_root),
                    vehicle_id=vehicle_id,
                    dump_start=pd.Timestamp(dump["start_dt_local"].iloc[0]),
                    dump_end=pd.Timestamp(dump["end_dt_local"].iloc[0]),
                )
                if tonnage is not None:
                    load_entry["tonnage_tons"] = round(tonnage, 2)
        loads.append(load_entry)
    stats_enriched = {"loads": loads}

    return (
        json.dumps(summary),
        json.dumps(stats_enriched),
        day_time_label,
    )


def _lookup_load_tonnage(
    *,
    tonnage_root: Path,
    vehicle_id: str,
    dump_start,
    dump_end,
    pad_minutes: int = 45,
) -> float | None:
    """Look up tonnage for one load by matching vehicle + tip timestamp.

    Scans the tonnage parquets at ``<tonnage_root>/<commodity>/year=YYYY/...``
    and finds rows where the vehicle matches and the tip datetime falls
    within ``[dump_start - pad, dump_end + pad]``. Returns tons (sum if
    multiple matches), or None.

    This is the same approach the notebook used: match by vehicle + time
    window with a pad to account for clock-drift between GPS and scale
    systems.
    """
    import pandas as pd

    if not tonnage_root.exists():
        return None

    pad = pd.Timedelta(minutes=pad_minutes)
    lo = dump_start - pad
    hi = dump_end + pad

    # Try to find tonnage rows: walk all parquet files under tonnage_root
    total = 0.0
    found = False
    for parquet in tonnage_root.rglob("*.parquet"):
        try:
            df = pd.read_parquet(parquet)
        except Exception:
            continue
        if df.empty or "vehicle_id" not in df.columns:
            continue
        cols = df.columns.tolist()
        tip_col = next(
            (c for c in ("tip_dt_local", "tip_dt", "datetime_local",
                         "dt_local", "tip_time")
             if c in cols),
            None,
        )
        ton_col = next(
            (c for c in ("net_tons", "tons", "net_weight_tons", "weight_tons")
             if c in cols),
            None,
        )
        if not tip_col or not ton_col:
            continue
        match = df[
            (df["vehicle_id"].astype(str) == str(vehicle_id))
            & (pd.to_datetime(df[tip_col]) >= lo)
            & (pd.to_datetime(df[tip_col]) <= hi)
        ]
        if not match.empty:
            total += float(match[ton_col].sum())
            found = True
    return total if found else None
