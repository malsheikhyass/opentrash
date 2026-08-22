"""Tonnage ingest pipeline — orchestrates the full SCALE + FIELD run.

The pipeline takes a folder of raw exports and produces:

- One year-partitioned parquet per source (``SCALE_<year>.parquet``,
  ``FIELD_<year>.parquet``) under the output folder.
- A ``registry.parquet`` describing every file processed and its outcome.

It is **safe to re-run**. Files already represented in the output parquets are
detected by hash key and skipped. New files (or new rows in a refreshed file)
flow through cleanly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .cleaners import DEFAULT_FLEET_PREFIXES, clean_field_file, clean_scale_file
from .keys import add_keys_field, add_keys_scale
from .registry import scan_tonnage_folder
from .upsert import upsert_annual_parquet

if TYPE_CHECKING:
    import pandas as pd


def run_ingest(
    data_dir: str | Path,
    out_dir: str | Path,
    fleet_prefixes: tuple[str, ...] = DEFAULT_FLEET_PREFIXES,
) -> pd.DataFrame:
    """Run the full tonnage ingest end-to-end.

    Steps: discover files → clean each by source → add keys → in-batch dedupe
    by ``record_key`` → upsert into year-partitioned parquet → write registry.

    Returns the registry DataFrame describing the run.
    """
    import pandas as pd

    data_dir = Path(data_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    registry = scan_tonnage_folder(data_dir)
    arts_frames: list[pd.DataFrame] = []
    rad_frames: list[pd.DataFrame] = []

    for i, row in registry.iterrows():
        path = row["file_path"]
        src = row["source"]
        try:
            if src == "SCALE":
                df_clean, qa = clean_scale_file(path)
                df_clean = add_keys_scale(df_clean)
                registry.at[i, "rows_read"] = qa.get("rows_raw", pd.NA)
                registry.at[i, "rows_kept"] = qa.get("rows_out", pd.NA)
                registry.at[i, "status"] = "OK"
                arts_frames.append(df_clean)

            elif src == "FIELD":
                df_clean, qa = clean_field_file(path, fleet_prefixes=fleet_prefixes)
                df_clean = add_keys_field(df_clean)
                registry.at[i, "rows_read"] = qa.get("rows_raw", pd.NA)
                registry.at[i, "rows_kept"] = qa.get("rows_after_demo_drop", pd.NA)
                registry.at[i, "status"] = "OK"
                registry.at[i, "notes"] = (
                    f"fleet_col={qa.get('fleet_col_used')} tons_col={qa.get('tons_col_used')}"
                )
                rad_frames.append(df_clean)
            else:
                registry.at[i, "status"] = "SKIP"
                registry.at[i, "notes"] = "Unknown source prefix"
        except Exception as e:
            registry.at[i, "status"] = "FAIL"
            registry.at[i, "notes"] = f"{type(e).__name__}: {e}"

    # Combine + in-batch dedupe by record_key, then upsert.
    if arts_frames:
        arts_all = (
            pd.concat(arts_frames, ignore_index=True)
            .drop_duplicates(subset=["record_key"])
            .reset_index(drop=True)
        )
        upsert_annual_parquet(arts_all, "SCALE", out_dir)

    if rad_frames:
        rad_all = (
            pd.concat(rad_frames, ignore_index=True)
            .drop_duplicates(subset=["record_key"])
            .reset_index(drop=True)
        )
        upsert_annual_parquet(rad_all, "FIELD", out_dir)

    # Persist the registry alongside the year files.
    registry.to_parquet(out_dir / "registry.parquet", index=False)
    return registry
