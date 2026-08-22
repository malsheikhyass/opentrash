"""GPS cache — fetch once, reuse forever.

GPS pulls are slow and rate-limited, and historical data never changes. So we
cache every (vehicle, day) pull as a parquet file and read from disk on repeat
requests. The cache layout mirrors how the work is organized — **one directory
per day, one file per vehicle**:

    cache/
      2025-08-18/
        100421.parquet
        832111.parquet
      2025-08-19/
        100421.parquet

The date directory uses ``YYYY-MM-DD`` so directories sort chronologically and
match the layout produced by the Postgres extractor (Lesson 6).

The cache is *adapter-agnostic*: give it any :class:`GPSAdapter` and it handles
the fetch-on-miss / read-on-hit logic. Swap Geotab for another vendor and the
cache doesn't change.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

    from ..adapters.gps.base import GPSAdapter


def _as_date(d: date | str | datetime) -> date:
    """Coerce a date / datetime / 'YYYY-MM-DD' string to a ``date``."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        return datetime.strptime(d.strip(), "%Y-%m-%d").date()
    raise TypeError(f"Unsupported date type: {type(d)!r}")


def cache_path(cache_dir: str | Path, vehicle: str, day: date | str) -> Path:
    """Return the parquet path for one vehicle on one day: ``<cache>/YYYY-MM-DD/<vehicle>.parquet``."""
    d = _as_date(day)
    return Path(cache_dir) / d.isoformat() / f"{str(vehicle).strip()}.parquet"


def get_gps_day(
    adapter: GPSAdapter,
    vehicle: str,
    day: date | str,
    cache_dir: str | Path,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return one vehicle's GPS pings for one local day, using the cache.

    On a cache **hit** (the parquet exists and ``refresh`` is False), reads from
    disk. On a **miss**, calls ``adapter.fetch(vehicle, day, day)``, writes the
    result to the cache, and returns it.

    Parameters
    ----------
    adapter:
        Any object satisfying the :class:`GPSAdapter` Protocol.
    vehicle:
        Vehicle identifier (as the adapter expects it).
    day:
        The local calendar day to fetch.
    cache_dir:
        Root of the cache tree.
    refresh:
        If True, ignore any cached file and re-fetch (then overwrite the cache).
    """
    import pandas as pd

    path = cache_path(cache_dir, vehicle, day)

    if path.exists() and not refresh:
        return pd.read_parquet(path)

    df = adapter.fetch(vehicle, day, day)

    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


def get_gps_fleet_day(
    adapter: GPSAdapter,
    vehicles: list[str],
    day: date | str,
    cache_dir: str | Path,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Fetch a whole fleet for one day, concatenated into a single DataFrame.

    Each vehicle is cached independently (one file each), so a partially-cached
    fleet only fetches the missing vehicles. Vehicles that return no data
    contribute nothing to the result.
    """
    import pandas as pd

    frames = [
        get_gps_day(adapter, v, day, cache_dir, refresh=refresh)
        for v in vehicles
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        from ..adapters.gps.base import GPS_COLUMNS
        return pd.DataFrame(columns=list(GPS_COLUMNS))
    return pd.concat(frames, ignore_index=True)
