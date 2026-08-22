"""Chunked pattern-detection runner — process the whole service area without blowing up RAM.

The detector ( :mod:`opentrash.patterns.detector` ) is a pure function: take
a slice of parcels, run the DuckDB CTAS pipeline, return a small DataFrame.
The runner is the orchestrator that chunks the service area's APN universe, calls
the detector once per chunk, and writes each chunk's result to a partitioned
parquet on disk.

Design properties:

- **Chunked**: chunks are organizational (one per route_id in
  ``parcels_wkb``), not algorithmic. The detection algorithm is
  route-agnostic; chunks just decide which parcels are in scope for one
  invocation. Each chunk is small enough for DuckDB to process without
  spilling.

- **Idempotent**: re-running a chunk overwrites its parquet cleanly. Same
  inputs, same outputs.

- **Resumable**: the runner skips chunks whose output parquet already
  exists and is newer than the enriched-pings glob's newest mtime. Pass
  ``refresh=True`` to force a re-run.

- **Parallel-safe at the caller layer**: chunks are independent. A caller
  can wrap :func:`run_patterns` to dispatch chunks across a
  ``multiprocessing.Pool`` or workflow runner without locking — each
  process opens its own DuckDB connection. The library stays
  single-threaded inside.

- **Manifest tracking**: every run appends to a ``_manifest.parquet`` log
  in the output directory recording which chunks ran when and with what
  config. Lightweight audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .config import DEFAULT_CONFIG, PatternConfig
from .detector import PATTERNS_COLUMNS, detect_patterns_chunk
from .window import Period, compute_window, window_label

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True, slots=True)
class ChunkResult:
    """One chunk's run result — what the runner records in the manifest."""
    route_id: str
    out_path: Path
    n_parcels: int
    ran: bool                  # False means "skipped (up to date)"
    ran_at_utc: datetime


def run_patterns(
    enriched_root: str | Path,
    parcels_wkb_path: str | Path,
    out_root: str | Path,
    *,
    period: Period = "past_year",
    anchor: date | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    routes: list[str] | None = None,
    refresh: bool = False,
    config: PatternConfig | None = None,
) -> pd.DataFrame:
    """Run pattern detection over the parcels universe in route-sized chunks.

    Parameters
    ----------
    enriched_root:
        Root directory of enriched-pings parquets. The runner scans
        ``<enriched_root>/**/*.parquet`` recursively. Typical layout from
        Lesson 8: ``enriched/YYYY-MM-DD/<vehicle>.parquet``.
    parcels_wkb_path:
        Path to the parcels_wkb parquet (output of L7). Provides the APN
        universe and the route_id partition.
    out_root:
        Root directory where chunk parquets land. Layout:
        ``<out_root>/<window_label>/by_route/<route_id>.parquet``.
    period:
        Named analysis window ("past_year", etc.) or "custom" with explicit
        ``start_date`` / ``end_date``. See :func:`opentrash.patterns.window.compute_window`.
    anchor:
        Reference date for the past-* periods. Defaults to today.
    start_date, end_date:
        Required when ``period="custom"``. Accept date objects or ISO strings.
    routes:
        Optional list of route_ids to process. ``None`` = all distinct
        route_refs in parcels_wkb.
    refresh:
        Force a re-run of every chunk even if its output is up to date.
    config:
        Optional :class:`PatternConfig`. Defaults to :data:`DEFAULT_CONFIG`.

    Returns
    -------
    A small manifest DataFrame describing what ran (one row per chunk).
    """
    import duckdb
    import pandas as pd

    cfg = config or DEFAULT_CONFIG
    start, end = compute_window(
        period, anchor=anchor, start_date=start_date, end_date=end_date,
    )

    enriched_root = Path(enriched_root)
    parcels_wkb_path = Path(parcels_wkb_path)
    out_root = Path(out_root)

    label = window_label(start, end)
    chunks_dir = out_root / label / "by_route"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / label / "_manifest.parquet"

    # Read parcels_wkb to determine the route partition.
    parcels = pd.read_parquet(parcels_wkb_path, columns=["APN", "route_ref"])
    if routes is None:
        routes = sorted(
            r for r in parcels["route_ref"].dropna().astype(str).unique() if r
        )

    enriched_glob = str(enriched_root / "**" / "*.parquet").replace("\\", "/")

    # Determine the newest enriched-pings mtime for skip-if-fresh logic.
    newest_input_mtime = _newest_mtime(enriched_root)

    results: list[ChunkResult] = []
    con = duckdb.connect(":memory:")
    try:
        for route_id in routes:
            chunk_apns = parcels.loc[
                parcels["route_ref"].astype(str) == route_id, "APN"
            ].astype(str).tolist()
            n_parcels = len(chunk_apns)
            out_path = chunks_dir / f"{route_id}.parquet"

            # Skip-if-fresh
            if (
                not refresh
                and out_path.exists()
                and (newest_input_mtime is None
                     or out_path.stat().st_mtime >= newest_input_mtime)
            ):
                results.append(ChunkResult(
                    route_id=route_id, out_path=out_path,
                    n_parcels=n_parcels, ran=False,
                    ran_at_utc=datetime.fromtimestamp(out_path.stat().st_mtime, tz=UTC),
                ))
                continue

            df = detect_patterns_chunk(
                con,
                enriched_glob=enriched_glob,
                parcels_wkb_path=parcels_wkb_path,
                start_date=start,
                end_date=end,
                chunk_apns=chunk_apns,
                config=cfg,
            )
            # Idempotent write — overwrite if exists.
            df.to_parquet(out_path, index=False)
            results.append(ChunkResult(
                route_id=route_id, out_path=out_path,
                n_parcels=n_parcels, ran=True,
                ran_at_utc=datetime.now(tz=UTC),
            ))
    finally:
        con.close()

    manifest = pd.DataFrame([
        {
            "route_id": r.route_id,
            "out_path": str(r.out_path),
            "n_parcels": r.n_parcels,
            "ran": r.ran,
            "ran_at_utc": r.ran_at_utc,
            "window_start": start,
            "window_end": end,
        }
        for r in results
    ])
    manifest.to_parquet(manifest_path, index=False)
    return manifest


def load_patterns(
    out_root: str | Path,
    window_label_str: str,
    *,
    routes: list[str] | None = None,
) -> pd.DataFrame:
    """Read back the partitioned patterns parquets as one combined frame.

    Convenience for downstream consumers (RouteView, business reports) that
    want all chunks concatenated. Filter to specific routes via ``routes``.
    """
    import pandas as pd

    out_root = Path(out_root)
    chunks_dir = out_root / window_label_str / "by_route"
    if not chunks_dir.exists():
        raise FileNotFoundError(f"No chunks directory at {chunks_dir}")

    files = sorted(chunks_dir.glob("*.parquet"))
    if routes is not None:
        wanted = set(routes)
        files = [f for f in files if f.stem in wanted]

    if not files:
        # Empty result in the canonical shape.
        return pd.DataFrame({c: [] for c in PATTERNS_COLUMNS})

    frames = [pd.read_parquet(f) for f in files]
    combined = pd.concat(frames, ignore_index=True)
    return combined[list(PATTERNS_COLUMNS)]


def _newest_mtime(root: Path) -> float | None:
    """The newest mtime under ``root`` (recursive), or None if empty/missing."""
    if not root.exists():
        return None
    mtimes = [p.stat().st_mtime for p in root.rglob("*.parquet")]
    return max(mtimes) if mtimes else None
