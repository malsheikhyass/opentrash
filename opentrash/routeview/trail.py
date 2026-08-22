"""Build the trail layer — GPS pings as colored dots on the map.

The trail is rendered as **points**, not a polyline. Polylines lose
granularity and hide irregularities; dots preserve every ping and let the
viewer see the truck's actual stop-and-go behavior on the map. Each dot
is colored by the segment phase it belongs to (windshield = gray,
collection = blue, dump = red, depot = green), giving an instant visual
read of how the workday was spent.

Reads enriched pings (L8 output) and the L9 segments timeline. Joins them
so every ping carries its phase color. Returns a GeoJSON FeatureCollection
ready to be embedded in the rendered HTML.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# Phase -> hex color mapping. Used by both the trail GeoJSON properties
# and the rendered HTML's MapLibre style. Centralized here so render.py
# can import it and stay in sync.
PHASE_COLORS: dict[str, str] = {
    "depot_departure": "#22c55e",   # green
    "windshield":       "#9ca3af",  # gray
    "collection":       "#2563eb",  # blue
    "dump":             "#dc2626",  # red
    "depot_arrival":    "#16a34a",  # darker green
}
DEFAULT_PHASE_COLOR = "#6b7280"      # gray-500 for anything unclassified


def build_trail_geojson(
    enriched_glob: str | Path,
    segments_timeline_path: str | Path,
    route_id: str,
    day: str,
    vehicle_id: str,
    *,
    downsample_seconds: int = 30,
) -> str:
    """Return a GeoJSON FeatureCollection string of colored ping dots.

    Parameters
    ----------
    enriched_glob:
        Glob or path to enriched-pings parquets for the day. The function
        filters internally to ``route_id`` + ``vehicle_id`` + ``day``.
    segments_timeline_path:
        Path to the L9 segments timeline parquet for this vehicle-day —
        provides the phase color per ping (via start/end time lookup).
    route_id, day, vehicle_id:
        The (route, day, vehicle) tuple this trail represents.
    downsample_seconds:
        Keep at most one ping per ``downsample_seconds``. Default 30 keeps
        the HTML reasonable (a 10-hour shift -> ~1200 dots) while still
        showing real movement detail. Set to 0 to disable downsampling.

    Returns
    -------
    A JSON string. The properties on each Feature include ``dt_local``
    (ISO), ``speed_mph``, ``phase``, ``color``.
    """
    import glob as _glob

    import pandas as pd

    paths = _glob.glob(str(enriched_glob), recursive=True)
    if not paths:
        return _empty_fc()
    enriched = pd.concat(
        [pd.read_parquet(p,
                         columns=["vehicle_id", "dt_local", "lat", "lon",
                                  "speed_mph", "route_id", "apn",
                                  "in_landfill", "at_depot"])
         for p in paths],
        ignore_index=True,
    )
    target_date = pd.Timestamp(day).date()
    mask = (
        (enriched["vehicle_id"].astype(str) == str(vehicle_id))
        & (enriched["route_id"].astype(str) == str(route_id))
        & (enriched["dt_local"].dt.date == target_date)
    )
    pings = enriched[mask].sort_values("dt_local").reset_index(drop=True)

    if pings.empty:
        return _empty_fc()

    if downsample_seconds > 0:
        pings = _downsample(pings, downsample_seconds)

    # Load segments timeline and assign each ping a phase by time-range lookup.
    segments_timeline_path = Path(segments_timeline_path)
    if segments_timeline_path.exists():
        segments = pd.read_parquet(segments_timeline_path)
        pings = _attach_phase(pings, segments)
    else:
        pings["phase"] = "windshield"     # fallback: best-guess if no segments

    pings["color"] = pings["phase"].map(PHASE_COLORS).fillna(DEFAULT_PHASE_COLOR)

    features = []
    for row in pings.itertuples(index=False):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(row.lon), float(row.lat)]},
            "properties": {
                "dt_local": pd.Timestamp(row.dt_local).isoformat(),
                "speed_mph": float(row.speed_mph) if row.speed_mph is not None else None,
                "phase": str(row.phase),
                "color": str(row.color),
            },
        })

    return json.dumps({"type": "FeatureCollection", "features": features})


def _downsample(pings: pd.DataFrame, seconds: int) -> pd.DataFrame:
    """Keep at most one ping per ``seconds`` window, ordered by time."""
    pings = pings.copy()
    # Coerce to ns-resolution int64 so the bucket math is independent of the
    # source parquet's datetime precision (pyarrow often gives us us-precision).
    as_ns = pings["dt_local"].astype("datetime64[ns]").astype("int64")
    pings["_bucket"] = as_ns // (seconds * 1_000_000_000)
    kept = pings.drop_duplicates("_bucket", keep="first")
    return kept.drop(columns="_bucket").reset_index(drop=True)


def _attach_phase(
    pings: pd.DataFrame,
    segments: pd.DataFrame,
) -> pd.DataFrame:
    """For each ping, find the segment whose start..end contains its
    ``dt_local`` and assign that segment's ``segment_type`` as the ping's
    phase. Pings outside all segments get ``windshield`` (a safe default).
    """
    import pandas as pd

    pings = pings.copy()
    pings["phase"] = "windshield"

    if segments.empty:
        return pings

    # Vectorized assignment: for each segment, mask all pings in its range
    # and assign its type. Iterates segments (small, ~10-20 per day) not pings.
    for _, seg in segments.iterrows():
        start = pd.Timestamp(seg["start_dt_local"])
        end = pd.Timestamp(seg["end_dt_local"])
        mask = (pings["dt_local"] >= start) & (pings["dt_local"] <= end)
        pings.loc[mask, "phase"] = str(seg["segment_type"])

    return pings


def _empty_fc() -> str:
    return json.dumps({"type": "FeatureCollection", "features": []})
