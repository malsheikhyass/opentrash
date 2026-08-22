"""Idempotent year-partitioned parquet upsert.

Tonnage is stored as ``<SOURCE>_<YEAR>.parquet`` (e.g. ``SCALE_2026.parquet``,
``FIELD_2026.parquet``). Each batch of new records is added to the existing year
file by **set difference on ``record_key``** — rows whose key already exists
are silently skipped, so re-running the pipeline on the same files (or files
that partially overlap) is safe.

The simple read-merge-rewrite approach is intentional: a year's worth of
weighbridge events fits in memory easily, and rewriting the whole file
guarantees the result is internally deduplicated even if upstream produces a
duplicate in a single batch.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def upsert_annual_parquet(
    df: pd.DataFrame,
    source: str,
    out_dir: str | Path,
) -> dict[int, dict]:
    """Upsert a batch of cleaned, keyed records into year-partitioned parquet.

    Splits ``df`` by ``year`` and, for each year, writes (or extends) a single
    parquet file under ``out_dir`` named ``<source>_<year>.parquet``. Rows
    whose ``record_key`` already exists in the file are skipped.

    Returns a per-year summary: ``{2026: {"existing": N, "added": M, "total": T}, ...}``.
    """
    import pandas as pd

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if df.empty:
        return {}

    if "record_key" not in df.columns:
        raise ValueError(
            f"Cannot upsert {source}: input is missing 'record_key'. "
            "Run add_keys_scale() or add_keys_field() first."
        )

    summary: dict[int, dict] = {}

    for year, batch in df.groupby("year", dropna=True):
        year = int(year)
        out_path = out_dir / f"{source}_{year}.parquet"

        if out_path.exists():
            existing_keys = set(
                pd.read_parquet(out_path, columns=["record_key"])["record_key"]
                .astype("string")
                .tolist()
            )
            new_rows = batch[~batch["record_key"].isin(existing_keys)].copy()
            existing_full = pd.read_parquet(out_path)
            merged = pd.concat([existing_full, new_rows], ignore_index=True)
            # Final safety dedupe (covers any in-batch duplicates).
            merged = merged.drop_duplicates(subset=["record_key"]).reset_index(drop=True)
            merged.to_parquet(out_path, index=False)
            summary[year] = {
                "existing": len(existing_keys),
                "added": len(new_rows),
                "total": len(merged),
            }
        else:
            batch = batch.drop_duplicates(subset=["record_key"]).reset_index(drop=True)
            batch.to_parquet(out_path, index=False)
            summary[year] = {
                "existing": 0,
                "added": len(batch),
                "total": len(batch),
            }

    return summary
