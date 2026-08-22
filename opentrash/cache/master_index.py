"""Master GPS index — STAC-like spatial-temporal lookup.

Joins the per-day **vehicle-day indexes** (one row per vehicle per day, with
that vehicle's bbox for that day) against the **route bbox index** (one row
per route, with its bbox) via simple bbox overlap. The result is a single
parquet with one row per ``(day, vehicle_id, route_id)`` triple where their
bboxes intersect.

Query pattern:

    "Which GPS files touch route 22064 between 2026-01-01 and 2026-01-31?"

becomes a millisecond DataFrame filter:

    master.query("route_id == '22064' and '2026-01-01' <= day <= '2026-01-31'")
          ["file_path"].unique()

A vehicle can match multiple routes (it drove through several); a route can
match many vehicle-days (the fleet covers it over time). One bbox compare,
millions of pings effectively narrowed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def build_master_index(
    vehicle_day_indexes: pd.DataFrame,
    route_bboxes: pd.DataFrame,
) -> pd.DataFrame:
    """Cross-join vehicle-day summaries with route bboxes; keep overlaps only.

    Both inputs are small tables (vehicle-days ~ hundreds of thousands of rows,
    routes ~ hundreds), so this is a fast operation in DuckDB. We push the
    overlap filter into the JOIN so we never materialize the full cross product.

    Parameters
    ----------
    vehicle_day_indexes:
        Concatenated output of :func:`opentrash.cache.gps_indexes.build_day_index`
        for every day in the cache (columns include ``day``, ``vehicle_id``,
        ``lat_min``, ``lat_max``, ``lon_min``, ``lon_max``, ``file_path``,
        ``min_local``, ``max_local``, ``rows``).
    route_bboxes:
        Output of :func:`opentrash.prep.static_layers.build_route_index`
        (columns ``route_id``, ``rte_min_lon``, ``rte_min_lat``, ``rte_max_lon``,
        ``rte_max_lat``).

    Returns
    -------
    The master index DataFrame.
    """
    import duckdb

    if vehicle_day_indexes.empty or route_bboxes.empty:
        # Return a DataFrame with the right columns but no rows.
        import pandas as pd
        return pd.DataFrame(columns=[
            *vehicle_day_indexes.columns,
            "route_id", "rte_min_lon", "rte_min_lat", "rte_max_lon", "rte_max_lat",
        ])

    con = duckdb.connect()
    con.register("vd", vehicle_day_indexes)
    con.register("rb", route_bboxes)
    master = con.execute(
        """
        SELECT
          v.*,
          r.route_id,
          r.rte_min_lon, r.rte_min_lat, r.rte_max_lon, r.rte_max_lat
        FROM vd v
        JOIN rb r
          ON NOT (
              v.lon_max < r.rte_min_lon OR v.lon_min > r.rte_max_lon
           OR v.lat_max < r.rte_min_lat OR v.lat_min > r.rte_max_lat
          )
        """
    ).fetchdf()
    con.close()
    return master


def load_vehicle_day_indexes(index_root: str | Path) -> pd.DataFrame:
    """Read and concatenate every ``vehicle_day_index.parquet`` under ``index_root``."""
    import pandas as pd

    index_root = Path(index_root)
    files = sorted(index_root.rglob("vehicle_day_index.parquet"))
    if not files:
        raise FileNotFoundError(
            f"No vehicle_day_index.parquet files under {index_root}. "
            "Run build_all_day_indexes(...) first."
        )
    frames = [pd.read_parquet(f) for f in files]
    return pd.concat(frames, ignore_index=True)


def files_for_route(
    master: pd.DataFrame,
    route_id: str,
    start_day: str | None = None,
    end_day: str | None = None,
) -> list[str]:
    """Return distinct ``file_path`` values for a route + (optional) date window."""
    df = master[master["route_id"] == str(route_id)]
    if start_day is not None:
        df = df[df["day"] >= str(start_day)]
    if end_day is not None:
        df = df[df["day"] <= str(end_day)]
    return df["file_path"].drop_duplicates().tolist()
