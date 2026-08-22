"""Rank vehicles that touched a route on a given day.

A small upstream helper for RouteView. Given the enriched-pings stream for
one day and one route_id, return a DataFrame of vehicles ordered by how
many pings each one contributed (a proxy for "who actually drove this
route"). The runner uses this to pick which vehicle to render
(typically the top-1, or top-N for the multi-HTML mode).

This is **pure aggregation** on the engine's output. No spatial work,
no DuckDB connection needed — just a pandas groupby.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def rank_vehicles_for_route_day(
    enriched_glob: str | Path,
    route_id: str,
    day: str | None = None,
    *,
    min_pings: int = 5,
) -> pd.DataFrame:
    """Read enriched pings, filter to (route_id [, day]), rank vehicles.

    Parameters
    ----------
    enriched_glob:
        Glob or single path matching enriched-pings parquets. Typical:
        ``"enriched/2026-01-18/*.parquet"`` for one day, or
        ``"enriched/**/*.parquet"`` recursively.
    route_id:
        The route polygon ID to filter to (matches ``route_id`` column on
        enriched pings).
    day:
        Optional ISO date string ("YYYY-MM-DD") to filter ``dt_local`` to
        a single day. None means whatever's matched by the glob.
    min_pings:
        Drop vehicles with fewer than this many pings on the (route, day) —
        these are usually pass-bys that touched the route polygon while
        in transit elsewhere, not actual service.

    Returns
    -------
    A DataFrame with one row per vehicle, columns ``vehicle_id``,
    ``n_pings``, ``first_dt_local``, ``last_dt_local`` sorted descending
    by ``n_pings``. Empty if no vehicles cleared ``min_pings``.
    """
    import glob as _glob

    import pandas as pd

    enriched_glob = str(enriched_glob)
    paths = _glob.glob(enriched_glob, recursive=True)
    if not paths:
        return pd.DataFrame(columns=[
            "vehicle_id", "n_pings", "first_dt_local", "last_dt_local",
        ])
    df = pd.concat(
        [pd.read_parquet(p, columns=["vehicle_id", "dt_local", "route_id"])
         for p in paths],
        ignore_index=True,
    )
    df = df[df["route_id"].astype(str) == str(route_id)]
    if day is not None:
        target = pd.Timestamp(day).date()
        df = df[df["dt_local"].dt.date == target]

    if df.empty:
        return pd.DataFrame(columns=[
            "vehicle_id", "n_pings", "first_dt_local", "last_dt_local",
        ])

    ranked = (
        df.groupby("vehicle_id")
          .agg(
              n_pings=("dt_local", "size"),
              first_dt_local=("dt_local", "min"),
              last_dt_local=("dt_local", "max"),
          )
          .reset_index()
    )
    ranked = ranked[ranked["n_pings"] >= min_pings]
    ranked = ranked.sort_values("n_pings", ascending=False).reset_index(drop=True)
    return ranked
