"""Per-day vehicle-day GPS index builder.

For every local day in the GPS cache, summarize each vehicle's parquet into a
single row: bounding box, time range, row count, and the file path back to
the source. The result is one ``vehicle_day_index.parquet`` per day, sitting
alongside (or under) the cache.

These tiny summaries are the input to the **master GPS index** (next module):
they let us answer "which GPS files touch route X in date window Y" in
milliseconds via a bbox overlap, without ever opening the big per-vehicle
files. Classic indexing — small summaries to find big data cheaply.

Layout:

    cache/
      2026-01-18/
        100421.parquet            # raw pings for one vehicle, one day
        832111.parquet
      indexes/
        2026-01-18/
          vehicle_day_index.parquet   # one row per vehicle that day
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Columns produced for each vehicle-day. Stable — downstream master index reads them.
VEHICLE_DAY_INDEX_COLUMNS: tuple[str, ...] = (
    "day",          # 'YYYY-MM-DD' string
    "vehicle_id",   # the vehicle name as it appears in the filename
    "rows",         # number of pings that day
    "min_local",    # earliest local timestamp
    "max_local",    # latest local timestamp
    "lat_min", "lat_max", "lon_min", "lon_max",
    "file_path",    # absolute path back to the source parquet
)


def summarize_vehicle_day_file(parquet_path: Path) -> dict | None:
    """Summarize one vehicle-day parquet file into a single row dict.

    Returns ``None`` if the file is empty or unreadable (don't abort a whole
    indexing run for one bad file — record nothing and move on).
    """
    import pandas as pd

    try:
        # Only read the columns we need for the summary.
        df = pd.read_parquet(
            parquet_path,
            columns=["vehicle_id", "dt_local", "lat", "lon"],
        )
    except Exception:
        return None

    if df.empty:
        return None

    day = parquet_path.parent.name      # the YYYY-MM-DD directory
    vehicle_id = parquet_path.stem      # filename minus .parquet

    return {
        "day": day,
        "vehicle_id": vehicle_id,
        "rows": len(df),
        "min_local": df["dt_local"].min(),
        "max_local": df["dt_local"].max(),
        "lat_min": float(df["lat"].min()),
        "lat_max": float(df["lat"].max()),
        "lon_min": float(df["lon"].min()),
        "lon_max": float(df["lon"].max()),
        "file_path": str(parquet_path.resolve()).replace("\\", "/"),
    }


def build_day_index(
    cache_dir: str | Path,
    day: str,
    *,
    index_root: str | Path | None = None,
) -> Path:
    """Build the ``vehicle_day_index.parquet`` for one local day.

    Scans ``<cache_dir>/<day>/*.parquet``, summarizes each, and writes a single
    parquet to ``<index_root>/<day>/vehicle_day_index.parquet``. If
    ``index_root`` is omitted it defaults to ``<cache_dir>/indexes``.
    """
    import pandas as pd

    cache_dir = Path(cache_dir)
    day_dir = cache_dir / day
    if not day_dir.exists():
        raise FileNotFoundError(f"Cache day directory not found: {day_dir}")

    index_root = Path(index_root) if index_root else cache_dir / "indexes"
    out_dir = index_root / day
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "vehicle_day_index.parquet"

    rows = []
    for p in sorted(day_dir.glob("*.parquet")):
        summary = summarize_vehicle_day_file(p)
        if summary is not None:
            rows.append(summary)

    df = pd.DataFrame(rows, columns=list(VEHICLE_DAY_INDEX_COLUMNS))
    df.to_parquet(out_path, index=False)
    return out_path


def build_all_day_indexes(
    cache_dir: str | Path,
    *,
    index_root: str | Path | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """Build every missing day-index under ``cache_dir``. Idempotent.

    Walks every ``YYYY-MM-DD`` directory under ``cache_dir`` (skipping the
    ``indexes`` subdirectory itself) and builds an index for any day that
    doesn't already have one. Pass ``overwrite=True`` to rebuild everything.
    """
    import re

    cache_dir = Path(cache_dir)
    index_root = Path(index_root) if index_root else cache_dir / "indexes"

    day_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    day_dirs = sorted(
        p for p in cache_dir.iterdir()
        if p.is_dir() and day_re.match(p.name)
    )

    written = []
    for day_dir in day_dirs:
        target = index_root / day_dir.name / "vehicle_day_index.parquet"
        if target.exists() and not overwrite:
            continue
        written.append(build_day_index(cache_dir, day_dir.name, index_root=index_root))
    return written
