"""Registry — discover tonnage files on disk and describe each one.

Each tonnage drop is an Excel file named with a source prefix and a date:

- ``SCALE_021926.xlsx``  — external-landfill weighbridge exports (SCALE system)
- ``FIELD_021926.xlsx``   — the landfill (FIELD system)

The 6-digit date is ``MMDDYY``. The registry scans a folder, builds one row
per file describing what it is (source, file_date, modified_time), and is the
input to the cleaning + upsert pipeline.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

SCALE_GLOB = "SCALE_*.xlsx"
FIELD_GLOB = "FIELD_*.xlsx"


def detect_source_from_name(name: str) -> str:
    """Return the source tag for a filename: ``SCALE``, ``FIELD``, or ``UNKNOWN``."""
    n = name.upper()
    if n.startswith("SCALE_"):
        return "SCALE"
    if n.startswith("FIELD_"):
        return "FIELD"
    return "UNKNOWN"


def parse_file_date_from_name(name: str):
    """Parse the trailing ``MMDDYY`` date from a tonnage filename.

    Returns a :class:`pandas.Timestamp` (naive) or :data:`pandas.NaT` if not
    parseable. Examples:

    >>> parse_file_date_from_name("SCALE_021926.xlsx")  # doctest: +SKIP
    Timestamp('2026-02-19 00:00:00')
    """
    import pandas as pd

    m = re.search(r"_(\d{6})(?:\.\w+)?$", name)
    if not m:
        return pd.NaT
    try:
        return pd.to_datetime(m.group(1), format="%m%d%y", errors="raise")
    except Exception:
        return pd.NaT


def _safe_mtime(path: Path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception:
        return None


def scan_tonnage_folder(data_dir: str | Path) -> pd.DataFrame:
    """Scan a folder for SCALE and FIELD exports and return a registry DataFrame.

    Columns: ``file_path``, ``file_name``, ``source``, ``file_date``,
    ``modified_time``, ``status`` (initialized to ``"NEW"``), and empty
    placeholders for downstream pipeline bookkeeping (``rows_read``,
    ``rows_kept``, ``notes``).
    """
    import pandas as pd

    data_dir = Path(data_dir)
    if not data_dir.exists():
        raise FileNotFoundError(f"Tonnage folder not found: {data_dir.resolve()}")

    files: list[Path] = []
    files.extend(sorted(data_dir.glob(SCALE_GLOB)))
    files.extend(sorted(data_dir.glob(FIELD_GLOB)))

    rows = []
    for p in files:
        rows.append({
            "file_path": str(p),
            "file_name": p.name,
            "source": detect_source_from_name(p.name),
            "file_date": parse_file_date_from_name(p.name),
            "modified_time": _safe_mtime(p),
            "status": "NEW",
            "rows_read": pd.NA,
            "rows_kept": pd.NA,
            "notes": "",
        })

    reg = pd.DataFrame(rows)
    if not reg.empty:
        reg = reg.sort_values(
            ["source", "file_date", "file_name"], kind="mergesort"
        ).reset_index(drop=True)
    return reg


def pick_latest_file(registry: pd.DataFrame, source: str) -> str:
    """Return the file_path of the most recent file for a given source."""
    df = registry[registry["source"] == source].copy()
    if df.empty:
        raise ValueError(f"No files in registry for source={source}")
    df = df.sort_values(["file_date", "modified_time", "file_name"], na_position="last")
    return str(df.iloc[-1]["file_path"])
