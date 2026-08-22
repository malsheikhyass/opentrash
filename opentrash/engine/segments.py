"""Engine segments — load-organized workday timeline.

The routing engine (``engine.enrichment``) produced enriched pings: each ping
carries route, parcel, landfill, and depot context. This module aggregates
that stream into the workday's structural backbone: the sequence of
**segments** the truck moves through, organized by **load**.

The choreography of a typical residential-collection day:

::

    DEPOT_DEPARTURE
      ├── LOAD 1: windshield → collection → dump
      ├── LOAD 2: windshield → collection → dump
      ├── LOAD 3: windshield → collection → dump  (optional)
    DEPOT_ARRIVAL

A **load** is one canister cycle: travel to a route, work the route, tip at
a landfill. The whole day is bookended by dwell at the depot.

Every "active" segment carries a ``load_number`` (1, 2, 3, ...); the depot
bookends are unnumbered. The timeline is the canonical artifact: one row per
segment, ordered chronologically, with mileage (cumulative haversine between
constituent pings), duration, and ping count.

We also emit choreography violations — operational red flags surfaced from
the timeline. A truck that ends a day after a collection segment without a
dump is going home loaded (a big no-no); a truck that starts with a dump
instead of a collection started the day loaded from yesterday. The timeline
makes these patterns trivially detectable.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .config import DEFAULT_CONFIG, EngineConfig

if TYPE_CHECKING:
    import pandas as pd

# Canonical column order for a timeline parquet. Downstream readers
# (patterns aggregation, RouteView, business reports) depend on this shape.
TIMELINE_COLUMNS: tuple[str, ...] = (
    "vehicle_id",
    "segment_id",          # 1..N within a vehicle-day, chronological
    "segment_type",        # depot_departure / windshield / collection / dump / depot_arrival
    "load_number",         # 1..N; null for depot bookends
    "route_id",            # populated for collection segments; else null
    "start_dt_local",
    "end_dt_local",
    "duration_seconds",
    "miles",
    "n_pings",
)

# Phases the per-ping classifier emits. Internal — segments derive from these.
_PHASE_DEPOT = "depot"
_PHASE_LANDFILL = "landfill"
_PHASE_COLLECTION = "collection"
_PHASE_WINDSHIELD = "windshield"


# ---------------------------------------------------------------------------
# Mileage helpers (numpy-vectorized haversine)
# ---------------------------------------------------------------------------
_EARTH_RADIUS_MILES = 3958.7613


def _haversine_miles(
    lat1: np.ndarray, lon1: np.ndarray,
    lat2: np.ndarray, lon2: np.ndarray,
) -> np.ndarray:
    """Great-circle distance in miles between paired (lat,lon) arrays.

    Standard haversine formula. Accurate to ~0.5% at this scale (well under
    the systematic undercount of straight-line vs road-network distance).
    Vectorized over numpy arrays — handles millions of pings in one call.
    """
    lat1_r = np.radians(lat1)
    lat2_r = np.radians(lat2)
    dlat = lat2_r - lat1_r
    dlon = np.radians(lon2 - lon1)
    a = (np.sin(dlat / 2.0) ** 2
         + np.cos(lat1_r) * np.cos(lat2_r) * np.sin(dlon / 2.0) ** 2)
    c = 2.0 * np.arcsin(np.minimum(1.0, np.sqrt(a)))
    return _EARTH_RADIUS_MILES * c


def _cumulative_miles(lats: np.ndarray, lons: np.ndarray) -> float:
    """Sum of pairwise haversine distances along an ordered ping sequence.

    A segment with fewer than 2 pings has no movement — returns 0.0.
    """
    if len(lats) < 2:
        return 0.0
    miles = _haversine_miles(lats[:-1], lons[:-1], lats[1:], lons[1:])
    return float(miles.sum())


# ---------------------------------------------------------------------------
# Phase derivation
# ---------------------------------------------------------------------------
def derive_phase_per_ping(
    enriched: pd.DataFrame,
    config: EngineConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Add a ``phase`` column to each enriched ping.

    Phases are derived from the enrichment flags + speed. Priority order:

    1. ``at_depot=True`` -> ``depot``
    2. ``in_landfill=True`` -> ``landfill``
    3. ``route_id`` not null AND ``speed_mph <= slow_mph_max`` -> ``collection``
    4. otherwise -> ``windshield``

    Returns a *new* DataFrame with the added column; input is not mutated.
    """
    df = enriched.copy()
    phase = np.full(len(df), _PHASE_WINDSHIELD, dtype=object)
    phase[df["route_id"].notna()
          & (df["speed_mph"] <= config.slow_mph_max)] = _PHASE_COLLECTION
    # Landfill overrides collection (a ping at a landfill polygon shouldn't
    # be mistaken for slow collection if there happens to also be a route
    # polygon overlapping)
    phase[df["in_landfill"].fillna(False).astype(bool).values] = _PHASE_LANDFILL
    # Depot overrides everything else (start/end-of-shift dwell)
    phase[df["at_depot"].fillna(False).astype(bool).values] = _PHASE_DEPOT
    df["phase"] = phase
    return df


# ---------------------------------------------------------------------------
# Timeline construction (gap-and-island over phase + route_id)
# ---------------------------------------------------------------------------
def _assign_segment_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Group adjacent rows sharing the same (phase, route_id) into segments.

    Standard gap-and-island: increment segment_id whenever the phase OR (for
    collection phases) the route_id changes. Collection on route A, then a
    quick swing into route B, then back to A is three segments, not one.
    """
    df = df.sort_values("dt_utc").reset_index(drop=True)
    # The "key" for change-detection: phase, plus route_id when in collection
    key_phase = df["phase"].astype(str).values
    key_route = df["route_id"].fillna("").astype(str).values
    # For non-collection phases, route_id doesn't matter for boundary detection;
    # mask it out so route-id flips inside windshield don't create false boundaries.
    is_collection = key_phase == _PHASE_COLLECTION
    effective_key = np.where(is_collection, key_phase + "|" + key_route, key_phase)
    # Increment whenever the key changes from the previous row
    boundaries = np.concatenate([[True], effective_key[1:] != effective_key[:-1]])
    df["segment_id"] = boundaries.cumsum().astype(int)
    return df


def _classify_segment_type(
    phase: str,
    segment_position: int,
    n_segments: int,
    saw_first_movement: bool,
) -> str:
    """Map a phase to a segment_type, distinguishing depot bookends.

    ``depot`` phases at the start of the day are ``depot_departure``; at the
    end ``depot_arrival``. Mid-day depot dwell (rare but possible — e.g. truck
    parks at depot for a break) is also classified as ``depot_arrival`` then
    a fresh ``depot_departure`` if movement resumes; we keep things simple
    here and let the load-numbering logic resolve mid-day cases.
    """
    if phase == _PHASE_DEPOT:
        return "depot_departure" if not saw_first_movement else "depot_arrival"
    return phase  # windshield, collection, landfill -> use as-is


def _assign_load_numbers(timeline: pd.DataFrame) -> pd.DataFrame:
    """Walk segments chronologically; number loads.

    Rules:
    - Depot bookends carry no load number (``NaN``).
    - The first active segment after ``depot_departure`` starts load 1.
    - A new load starts after a dump *only if* future active segments
      include another collection or dump. A windshield immediately after
      the final dump that leads to ``depot_arrival`` is "going home" and
      stays in the current load.
    - If the day ends without a final dump, the last active load stays
      assigned but a violation is flagged separately.
    """
    timeline = timeline.copy().reset_index(drop=True)
    load_numbers: list[float] = []
    current_load = 0
    just_dumped = False
    n = len(timeline)
    for i, row in timeline.iterrows():
        seg_type = row["segment_type"]
        if seg_type in ("depot_departure", "depot_arrival"):
            load_numbers.append(np.nan)
            just_dumped = False
            continue
        # An active (non-depot) segment.
        if just_dumped:
            # Decide whether THIS segment opens a new load or is just the
            # going-home trip. If any future segment is collection or dump,
            # we're continuing work -> new load. Otherwise we're heading
            # home -> stay in the current load.
            opens_new_load = False
            for j in range(i, n):
                next_type = timeline.iloc[j]["segment_type"]
                if next_type in ("collection", "dump"):
                    opens_new_load = True
                    break
                if next_type == "depot_arrival":
                    break
            if opens_new_load:
                current_load += 1
            just_dumped = False
        elif current_load == 0:
            current_load = 1
        load_numbers.append(float(current_load))
        if seg_type == "dump":
            just_dumped = True
    timeline["load_number"] = load_numbers
    return timeline


def build_timeline(
    enriched: pd.DataFrame,
    config: EngineConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Aggregate enriched pings into the workday's segment timeline.

    The single high-level entry point. Takes one vehicle-day of enriched
    pings and returns the segment-level timeline DataFrame, in
    :data:`TIMELINE_COLUMNS` order.

    Parameters
    ----------
    enriched:
        One vehicle-day of enriched pings (output of
        :func:`opentrash.engine.enrichment.enrich_pings`).
    config:
        Engine knobs. Defaults to :data:`DEFAULT_CONFIG`.

    Returns
    -------
    A DataFrame with one row per segment, columns matching
    :data:`TIMELINE_COLUMNS`.
    """
    import pandas as pd

    if enriched.empty:
        return _empty_timeline_frame()

    # 1. Phase per ping
    phased = derive_phase_per_ping(enriched, config)

    # 2. Group adjacent same-phase pings into segments
    phased = _assign_segment_ids(phased)

    # 3. Aggregate each segment to a single row
    rows = []
    saw_first_movement = False
    for seg_id, group in phased.groupby("segment_id", sort=True):
        phase = group["phase"].iloc[0]
        # Once we leave depot for the first time, depot dwell stops being
        # "departure" and becomes "arrival" for subsequent depot segments.
        if phase != _PHASE_DEPOT and not saw_first_movement:
            saw_first_movement = True
        seg_type = _classify_segment_type(
            phase, seg_id, len(phased), saw_first_movement
        )
        # If this is a depot segment, also map "landfill"->"dump" for the
        # external naming we promised.
        if phase == _PHASE_LANDFILL:
            seg_type = "dump"
        elif phase == _PHASE_WINDSHIELD:
            seg_type = "windshield"
        elif phase == _PHASE_COLLECTION:
            seg_type = "collection"

        start = group["dt_local"].iloc[0]
        end = group["dt_local"].iloc[-1]
        duration = (end - start).total_seconds()

        lats = group["lat"].to_numpy(dtype=float)
        lons = group["lon"].to_numpy(dtype=float)
        miles = _cumulative_miles(lats, lons)
        miles *= (1.0 + config.mileage_inflation_pct)

        route_id = (group["route_id"].iloc[0]
                    if phase == _PHASE_COLLECTION else None)

        rows.append({
            "vehicle_id": group["vehicle_id"].iloc[0],
            "segment_id": int(seg_id),
            "segment_type": seg_type,
            "load_number": None,                   # filled in step 4
            "route_id": route_id,
            "start_dt_local": start,
            "end_dt_local": end,
            "duration_seconds": float(duration),
            "miles": float(miles),
            "n_pings": int(len(group)),
        })

    timeline = pd.DataFrame(rows)

    # 4. Walk timeline; assign load_number
    timeline = _assign_load_numbers(timeline)

    return timeline[list(TIMELINE_COLUMNS)]


# ---------------------------------------------------------------------------
# Choreography violations
# ---------------------------------------------------------------------------
VIOLATION_COLUMNS: tuple[str, ...] = (
    "vehicle_id",
    "segment_id",
    "violation_type",
    "severity",
    "description",
)


def flag_choreography_violations(timeline: pd.DataFrame) -> pd.DataFrame:
    """Walk a timeline; surface operational red flags.

    Flags returned:

    - ``loaded_depot_return`` (severity ``high``): the last active segment
      before ``depot_arrival`` was a ``collection`` (not a ``dump``). The
      truck went home loaded.
    - ``loaded_depot_departure`` (severity ``high``): the first active
      segment of the day was ``dump`` (not ``windshield``/``collection``).
      The truck started the day loaded from yesterday.
    - ``overrun_loads`` (severity ``medium``): more than 3 loads in a day —
      worth review for shift overrun.
    - ``mid_load_route_switch`` (severity ``low``): inside a single load,
      collection switches between route_ids (A → B → A). Could be normal
      (MANUAL parcels off a route) or could indicate routing inefficiency.

    Returns
    -------
    A DataFrame in :data:`VIOLATION_COLUMNS` order. Empty if no violations.
    """
    import pandas as pd

    violations: list[dict] = []
    if timeline.empty:
        return _empty_violations_frame()

    vehicle_id = timeline["vehicle_id"].iloc[0]
    active = timeline[~timeline["segment_type"].isin(
        ["depot_departure", "depot_arrival"]
    )]

    if not active.empty:
        first = active.iloc[0]
        last = active.iloc[-1]

        if first["segment_type"] == "dump":
            violations.append({
                "vehicle_id": vehicle_id,
                "segment_id": int(first["segment_id"]),
                "violation_type": "loaded_depot_departure",
                "severity": "high",
                "description": (
                    "Day started with a dump instead of windshield/collection — "
                    "truck likely departed depot loaded from prior day."
                ),
            })

        if last["segment_type"] == "collection":
            violations.append({
                "vehicle_id": vehicle_id,
                "segment_id": int(last["segment_id"]),
                "violation_type": "loaded_depot_return",
                "severity": "high",
                "description": (
                    "Day ended on a collection segment without a closing dump — "
                    "truck likely returned to depot loaded."
                ),
            })

    n_loads = int(timeline["load_number"].max() or 0)
    if n_loads > 3:
        violations.append({
            "vehicle_id": vehicle_id,
            "segment_id": -1,
            "violation_type": "overrun_loads",
            "severity": "medium",
            "description": f"Day had {n_loads} loads (more than typical 3); review for shift overrun.",
        })

    # Per-load route-switching check
    collection = timeline[timeline["segment_type"] == "collection"]
    for load_num, group in collection.groupby("load_number"):
        routes = list(group["route_id"].dropna())
        if len(set(routes)) > 1:
            # The route sequence had more than one unique route in a single load
            violations.append({
                "vehicle_id": vehicle_id,
                "segment_id": int(group["segment_id"].iloc[0]),
                "violation_type": "mid_load_route_switch",
                "severity": "low",
                "description": (
                    f"Load {int(load_num)} touched multiple routes: {routes}."
                ),
            })

    if not violations:
        return _empty_violations_frame()

    return pd.DataFrame(violations)[list(VIOLATION_COLUMNS)]


# ---------------------------------------------------------------------------
# Driver: build all artifacts from one enriched parquet
# ---------------------------------------------------------------------------
def build_all_segments(
    enriched_path: str | Path,
    out_root: str | Path,
    *,
    config: EngineConfig = DEFAULT_CONFIG,
) -> dict[str, Path]:
    """Read one enriched vehicle-day parquet; write timeline + violations parquets.

    Output layout (mirrors enrichment output and the cache):

    ::

        <out_root>/timeline/<YYYY-MM-DD>/<vehicle_id>.parquet
        <out_root>/violations/<YYYY-MM-DD>/<vehicle_id>.parquet

    Returns
    -------
    A dict with keys ``"timeline"`` and ``"violations"`` mapping to the
    paths written.
    """
    import pandas as pd

    enriched_path = Path(enriched_path)
    if not enriched_path.exists():
        raise FileNotFoundError(f"Enriched parquet not found: {enriched_path}")

    day = enriched_path.parent.name      # "YYYY-MM-DD"
    vehicle_id = enriched_path.stem
    enriched = pd.read_parquet(enriched_path)

    timeline = build_timeline(enriched, config)
    violations = flag_choreography_violations(timeline)

    out_root = Path(out_root)
    timeline_path = out_root / "timeline" / day / f"{vehicle_id}.parquet"
    violations_path = out_root / "violations" / day / f"{vehicle_id}.parquet"

    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    violations_path.parent.mkdir(parents=True, exist_ok=True)

    timeline.to_parquet(timeline_path, index=False)
    violations.to_parquet(violations_path, index=False)

    return {"timeline": timeline_path, "violations": violations_path}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _empty_timeline_frame() -> pd.DataFrame:
    import pandas as pd
    return pd.DataFrame({c: [] for c in TIMELINE_COLUMNS})


def _empty_violations_frame() -> pd.DataFrame:
    import pandas as pd
    return pd.DataFrame({c: [] for c in VIOLATION_COLUMNS})
