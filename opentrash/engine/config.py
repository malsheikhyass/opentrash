"""Engine configuration — all the knobs in one frozen place.

The routing engine has a small number of tunable values: how close to a
parcel boundary counts as "near it", how big a landfill buffer is, how
fast you have to be moving for a ping to *not* count as a service event,
and so on. Rather than scatter these as function arguments or magic
numbers, we collect them in :class:`EngineConfig` — a frozen dataclass
that makes the rule set visible and changeable in one place.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """All engine knobs in one immutable place.

    Defaults are illustrative starting points. Override
    fields for a different agency or a different commodity:

    .. code-block:: python

        cfg = EngineConfig(parcel_edge_ft=20.0, slow_mph_max=15.0)

    Distances are in feet (working CRS, EPSG:2230). Speeds in mph. Times
    in seconds.
    """

    # ---- Parcel proximity ----
    # How close a ping has to be to a parcel boundary (or inside) to count
    # as "near the parcel." Service events happen on the parcel; this is the
    # tolerance for "near enough."
    parcel_edge_ft: float = 25.0

    # Pings moving faster than this don't count as service events (the truck
    # is just driving past). Helps filter false positives on collector trucks
    # that drive across many parcels at speed.
    slow_mph_max: float = 12.0

    # ---- Landfill / depot proximity ----
    # Buffer applied around landfill polygons (matches prep.static_layers
    # default, kept here for engine-local visibility).
    landfill_buffer_ft: float = 50.0

    # Radius around the depot's centroid to count as "at the depot." A
    # depot is typically a yard with imprecise boundary; a small radius
    # keeps things simple.
    depot_radius_ft: float = 250.0

    # ---- Optional overrides ----
    # Override the default web CRS for output (rarely needed).
    output_crs: str = "EPSG:4326"

    # Number of pings to process per DuckDB batch when streaming through
    # large multi-year vehicle-day caches. Tune up on big-RAM machines.
    batch_size: int = 200_000

    # ---- Segment mileage ----
    # Mileage is computed by summing haversine distances between consecutive
    # GPS pings within each segment. That straight-line cumulative is a
    # systematic *undercount* of real road-network mileage because trucks
    # follow streets (which curve) while the line cuts corners — typically
    # 5-15% short depending on ping cadence. ``mileage_inflation_pct`` is
    # an opt-in calibration knob: 0.0 reports honest straight-line miles;
    # 0.08 inflates every segment's miles by 8% to approximate road
    # network distance. Default is 0.0 (no inflation) — never silently
    # adjust numbers.
    mileage_inflation_pct: float = 0.0


# A reasonable default callers can reach for without constructing one.
DEFAULT_CONFIG = EngineConfig()
