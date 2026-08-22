"""Stable hash keys for tonnage records.

The package's idempotency story rests on a **per-record stable hash**. Every
cleaned row gets a ``record_key``: a sha1 of a few normalized fields. If you
re-process the same source file (or a fresh export that overlaps a prior one),
duplicate rows produce identical keys and the upsert step skips them.

The fields chosen for each source are the ones that *together identify a
unique weighbridge event*:

- **SCALE** — minute-floored event datetime + fleet + route + load number +
  dump location + material + tons (rounded to 3 decimals).
- **FIELD**  — minute-floored event datetime + fleet + landfill destination +
  material + tons; plus ``TRAN_NUM`` when present (a strong stabilizer).

Rounding tons to 3 decimals and flooring datetimes to the minute prevents
tiny float / second-level wiggles from making two equivalent records look
different.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def _norm_text(s: pd.Series) -> pd.Series:
    """Uppercase, strip, collapse whitespace; NA -> empty string for hashing."""
    return (
        s.astype("string")
        .fillna("")
        .str.upper()
        .str.replace("\u00A0", " ", regex=False)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
    )


def _dt_floor_minute(s: pd.Series) -> pd.Series:
    """Coerce to datetime then floor to the minute (removes seconds/jitter)."""
    import pandas as pd

    return pd.to_datetime(s, errors="coerce").dt.floor("min")


def _round_tons(s: pd.Series, ndigits: int = 3) -> pd.Series:
    """Coerce to numeric and round; ndigits=3 is enough for ton-level events."""
    import pandas as pd

    return pd.to_numeric(s, errors="coerce").round(ndigits)


def _hash_columns(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Compute a sha1 hash per row over ``cols`` joined with ``||``.

    Missing columns are treated as empty strings so the hash is defined even
    when an optional field isn't present.
    """
    import pandas as pd

    parts: list[pd.Series] = []
    for c in cols:
        if c not in df.columns:
            parts.append(pd.Series([""] * len(df), index=df.index, dtype="string"))
        else:
            parts.append(df[c].astype("string").fillna(""))

    joined = parts[0]
    for p in parts[1:]:
        joined = joined + "||" + p
    return joined.map(lambda x: hashlib.sha1(x.encode("utf-8")).hexdigest()).astype("string")


def add_keys_scale(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``event_dt``, ``year``, and stable ``record_key`` to an SCALE frame."""
    df = df.copy()
    df["event_dt"] = _dt_floor_minute(df["rpt_dt"])
    df["year"] = df["event_dt"].dt.year

    k = {
        "k_event_min": df["event_dt"].astype("string"),
        "k_fleet": _norm_text(df["co_fleet_id"]),
        "k_route": _norm_text(df.get("ROUTE#", _empty_like(df))),
        "k_load":  _norm_text(df.get("NBR_LOADS", _empty_like(df))),
        "k_dump":  _norm_text(df.get("DUMP LOCATION", _empty_like(df))),
        "k_matl":  _norm_text(df["matl_norm"]),
        "k_tons":  _round_tons(df["tons"], 3).astype("string"),
    }
    tmp = df.assign(**k)
    df["record_key"] = _hash_columns(tmp, list(k.keys()))
    return df


def add_keys_field(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``event_dt``, ``year``, and stable ``record_key`` to a FIELD frame."""
    df = df.copy()
    df["event_dt"] = _dt_floor_minute(df["ld_dt_tm"])
    df["year"] = df["event_dt"].dt.year

    k = {
        "k_event_min": df["event_dt"].astype("string"),
        "k_fleet":    _norm_text(df["co_fleet_id"]),
        "k_landfill": _norm_text(df.get("LANDFILL_DEST_DESC", _empty_like(df))),
        "k_matl":     _norm_text(df["matl_norm"]),
        "k_tons":     _round_tons(df["tons"], 3).astype("string"),
    }
    # TRAN_NUM, when present, is a very strong stabilizer (transaction id).
    if "TRAN_NUM" in df.columns:
        k["k_tran"] = _norm_text(df["TRAN_NUM"])

    tmp = df.assign(**k)
    df["record_key"] = _hash_columns(tmp, list(k.keys()))
    return df


def _empty_like(df: pd.DataFrame) -> pd.Series:
    """An all-NA string series aligned to ``df``'s index — for missing optional cols."""
    import pandas as pd

    return pd.Series(pd.NA, index=df.index, dtype="string")
