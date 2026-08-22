"""Source-specific cleaners for SCALE and FIELD tonnage exports.

Each source has its own column layout and quirks, but both produce a common
shape: a DataFrame with normalized event datetime, fleet ID, tons (float), and
material classification (``REFUSE`` / ``RECYCLING`` / ``ORGANIC`` / ``OTHER``),
plus lineage columns identifying the source file.

The cleaners are deliberately **tolerant of header variation** (whitespace
quirks, case, the occasional ``TRUCK`` instead of ``CO_FLEET_ID``) — Excel
exports drift over time and we don't want a renamed header to crash a run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.vehicle_ids import OUR_FLEET_PREFIXES
from .registry import parse_file_date_from_name

if TYPE_CHECKING:
    import pandas as pd

# Fleet prefixes that mark records as *ours* (the agency's own trucks). Used to
# filter FIELD, where the export includes other haulers too. This points at the
# canonical knob in core.vehicle_ids so there's one place to edit fleet rules.
DEFAULT_FLEET_PREFIXES = OUR_FLEET_PREFIXES


def _canon_header(x: object) -> str:
    """Canonicalize a header for matching: NBSP -> space, collapse, uppercase."""
    s = "" if x is None else str(x)
    s = s.replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s.strip())
    return s.upper()


def resolve_column(df: pd.DataFrame, preferred: list[str]) -> str | None:
    """Find the first column in ``df`` matching any name in ``preferred``.

    Matching is case- and whitespace-tolerant via canonical form.
    """
    canon_to_actual = {_canon_header(c): c for c in df.columns}
    for want in preferred:
        actual = canon_to_actual.get(_canon_header(want))
        if actual is not None:
            return actual
    return None


def _read_first_sheet(path: str | Path) -> tuple[str, pd.DataFrame]:
    """Read the first sheet of an .xlsx file with ``dtype=object`` (preserves IDs)."""
    import pandas as pd

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    xls = pd.ExcelFile(p)
    sheet = xls.sheet_names[0]
    df = pd.read_excel(p, sheet_name=sheet, dtype=object)
    return sheet, df


# ---------------------------------------------------------------------------
# Field-level cleaners.
# ---------------------------------------------------------------------------
def clean_fleet_id(series, index=None) -> pd.Series:
    """Normalize a fleet/truck ID to a clean string; bad values become NA.

    Strips commas and trailing ``.0`` (from float-formatted IDs), uppercases
    everything-or-nothing tokens like ``NaN``/``None`` to NA.
    """
    import pandas as pd

    if series is None:
        if index is None:
            return pd.Series(pd.NA, dtype="string")
        return pd.Series(pd.NA, index=index, dtype="string")

    s = series.astype("string")
    s = s.str.replace(",", "", regex=False).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True)
    s = s.str.replace(r"\..*$", "", regex=True)
    # Normalize empty and the various textual NA tokens we've seen in exports.
    null_tokens = {"", "NA", "NAN", "NONE", "NULL", "<NA>"}
    return s.where(~s.str.upper().isin(null_tokens), pd.NA)


def parse_dt(series: pd.Series) -> pd.Series:
    """Coerce a series to datetime; unparseable values become NaT."""
    import pandas as pd

    return pd.to_datetime(series, errors="coerce")


def to_float(series: pd.Series) -> pd.Series:
    """Coerce a series to float64; unparseable values become NaN."""
    import pandas as pd

    return pd.to_numeric(series, errors="coerce").astype("float64")


# ---------------------------------------------------------------------------
# Material classification.
# ---------------------------------------------------------------------------
SCALE_MATERIAL_MAP = {
    "RECYCLING": "RECYCLING",
    "REFUSE": "REFUSE",
    "COLLECTIONS ORGANICS": "ORGANIC",
}


def matl_norm_scale(matl_desc: pd.Series) -> pd.Series:
    """Normalize an SCALE material description into the canonical 4-class label."""
    s = matl_desc.astype("string").str.upper().str.strip()
    return s.map(SCALE_MATERIAL_MAP).fillna("OTHER")


def matl_norm_field(matl_desc: pd.Series) -> pd.Series:
    """Normalize a FIELD material description.

    FIELD is simpler: anything ``REFUSE`` is refuse, ``CHRISTMAS TREES`` is
    other, everything else is treated as organics.
    """
    import numpy as np
    import pandas as pd

    s = matl_desc.astype("string").str.upper().str.strip()
    out = pd.Series(
        np.where(
            s == "REFUSE", "REFUSE",
            np.where(s == "CHRISTMAS TREES", "OTHER", "ORGANIC"),
        ),
        index=s.index,
        dtype="string",
    )
    return out


# ---------------------------------------------------------------------------
# Cleaners — one per source.
# ---------------------------------------------------------------------------
SCALE_KEEP = [
    "ROUTE#", "TRANSACTION #", "COLL_DT", "RPT_DT", "LD_DT_TM",
    "CO_FLEET_ID", "CREW_CNT", "TONS", "TRUCK", "BLUE_ORANGE_WEEK_CD",
    "DAY_OF_WK", "MATL_TYP_CD", "MATL_TYP_DESC", "CREW NAME", "NBR_LOADS",
    "DUMP LOCATION",
]

FIELD_KEEP_BASE = [
    "TRAN_NUM", "LD_DT_TM", "RPT_DT", "CO_FLEET_ID", "TRUCK",
    "MATL_TYP_CD", "MATL_TYP_DESC", "LANDFILL_DEST_DESC",
]
FIELD_TONS_CANDIDATES = ["TONS", "L.NET_WGT_QTY/2000"]


def clean_scale_file(path: str | Path) -> tuple[pd.DataFrame, dict]:
    """Clean a single SCALE export. Returns (cleaned_df, qa_dict)."""
    sheet, df = _read_first_sheet(path)

    # Keep only known columns (tolerant of header drift).
    rename_map: dict[str, str] = {}
    cols_present: list[str] = []
    for want in SCALE_KEEP:
        actual = resolve_column(df, [want])
        if actual is not None:
            cols_present.append(actual)
            rename_map[actual] = want

    out = df[cols_present].copy().rename(columns=rename_map)

    file_name = Path(path).name
    out["source"] = "SCALE"
    out["file_name"] = file_name
    out["file_date"] = parse_file_date_from_name(file_name)

    if "ROUTE#" in out.columns:
        out["ROUTE#"] = out["ROUTE#"].astype("string").str.strip()

    out["rpt_dt"] = parse_dt(out.get("RPT_DT"))
    out["ld_dt_tm"] = parse_dt(out.get("LD_DT_TM"))

    # Fleet can be CO_FLEET_ID or TRUCK depending on the export.
    fleet_col = resolve_column(df, ["CO_FLEET_ID", "TRUCK"])
    if fleet_col is not None:
        out["co_fleet_id"] = clean_fleet_id(df.loc[out.index, fleet_col], index=out.index)
    else:
        import pandas as pd
        out["co_fleet_id"] = pd.Series(pd.NA, index=out.index, dtype="string")

    out["tons"] = to_float(out.get("TONS"))
    out["matl_raw"] = out.get("MATL_TYP_DESC").astype("string")
    out["matl_norm"] = matl_norm_scale(out["matl_raw"])

    qa = {
        "sheet": sheet,
        "rows_raw": len(df),
        "rows_out": len(out),
        "null_rpt_dt_pct": float(out["rpt_dt"].isna().mean()),
        "null_fleet_pct": float(out["co_fleet_id"].isna().mean()),
        "null_tons_pct": float(out["tons"].isna().mean()),
        "neg_tons_cnt": int((out["tons"] < 0).sum(skipna=True)),
        "matl_raw_values": sorted(out["matl_raw"].dropna().unique().tolist())[:50],
    }
    return out, qa


def clean_field_file(
    path: str | Path,
    fleet_prefixes: tuple[str, ...] = DEFAULT_FLEET_PREFIXES,
) -> tuple[pd.DataFrame, dict]:
    """Clean a single FIELD export. Returns (cleaned_df, qa_dict).

    Filters to *our* fleet (by prefix) and drops material ``DEMO`` rows.
    """
    import pandas as pd

    sheet, df = _read_first_sheet(path)

    tons_col = resolve_column(df, FIELD_TONS_CANDIDATES)
    fleet_col = resolve_column(df, ["CO_FLEET_ID", "TRUCK"])

    # Build kept-columns set tolerantly. We exclude TRUCK/CO_FLEET_ID from the
    # base loop because they're handled by the fleet_col logic below — otherwise
    # the rename to FLEET_RESOLVED never happens when TRUCK is the fleet column.
    base_minus_fleet = [c for c in FIELD_KEEP_BASE if c not in ("CO_FLEET_ID", "TRUCK")]
    keep_actual: list[str] = []
    rename_map: dict[str, str] = {}
    for want in base_minus_fleet:
        actual = resolve_column(df, [want])
        if actual is not None:
            keep_actual.append(actual)
            rename_map[actual] = want
    if tons_col is not None:
        keep_actual.append(tons_col)
        rename_map[tons_col] = "TONS_RESOLVED"
    if fleet_col is not None:
        keep_actual.append(fleet_col)
        rename_map[fleet_col] = "FLEET_RESOLVED"

    out = df[keep_actual].copy().rename(columns=rename_map)

    if "FLEET_RESOLVED" not in out.columns:
        out["FLEET_RESOLVED"] = pd.NA
    if "TONS_RESOLVED" not in out.columns:
        out["TONS_RESOLVED"] = pd.NA

    file_name = Path(path).name
    out["source"] = "FIELD"
    out["file_name"] = file_name
    out["file_date"] = parse_file_date_from_name(file_name)

    out["rpt_dt"] = parse_dt(out.get("RPT_DT"))
    out["ld_dt_tm"] = parse_dt(out.get("LD_DT_TM"))

    out["co_fleet_id"] = clean_fleet_id(out.get("FLEET_RESOLVED"))

    # Filter to our fleet by prefix.
    mask_fleet = out["co_fleet_id"].fillna("").str.startswith(fleet_prefixes)
    out = out[mask_fleet].copy()

    out["tons"] = to_float(out.get("TONS_RESOLVED"))
    out["matl_raw"] = out.get("MATL_TYP_DESC").astype("string")

    # Drop DEMO rows.
    drop_demo = out["matl_raw"].astype("string").str.upper().str.strip().eq("DEMO")
    out = out[~drop_demo].copy()

    out["matl_norm"] = matl_norm_field(out["matl_raw"])

    qa = {
        "sheet": sheet,
        "rows_raw": len(df),
        "rows_after_fleet_filter": int(mask_fleet.sum()),
        "rows_after_demo_drop": len(out),
        "fleet_col_used": fleet_col,
        "tons_col_used": tons_col,
        "null_ld_dt_tm_pct": float(out["ld_dt_tm"].isna().mean()) if len(out) else 0.0,
        "null_fleet_pct": float(out["co_fleet_id"].isna().mean()) if len(out) else 0.0,
        "null_tons_pct": float(out["tons"].isna().mean()) if len(out) else 0.0,
        "neg_tons_cnt": int((out["tons"] < 0).sum(skipna=True)) if len(out) else 0,
        "matl_raw_values": sorted(out["matl_raw"].dropna().unique().tolist())[:50],
    }
    return out, qa
