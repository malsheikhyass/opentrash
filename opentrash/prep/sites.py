"""The sites layer — cleaning raw input and encoding business rules as data.

A *site* is a serviced address. A typical agency export carries dozens of columns of mixed
quality; this module turns it into a clean, validated table with canonical
**route IDs** for each of the three commodities collected: refuse (REF),
recycling (REC), and organics (ORG).

The interesting part is the **route ID**, which packs several facts into one
short code. Understanding it is the whole point of this module.

Route ID anatomy
----------------
A route ID looks like ``62081`` or ``62433O``. Read it left to right:

- **Digits 1-2 — collection zone (example schema — remap the constants below).**
  ``61-65`` and ``51-52`` zones handle refuse *and* recycling;
  ``71-75`` zones handle organics only. ``51`` and ``52`` are the
  **manual** collection routes (everything else is automated).
- **Digits 3-4 — sequence number**, which also disambiguates commodity when a
  zone handles more than one:
  - For the shared ranges, ``< 40`` means **refuse**, ``>= 40`` means
    **recycling**.
  - For manual routes (``51``/``52``): ``< 40`` refuse, ``40-59`` recycling,
    ``>= 60`` organics.
  - Organics zones (``71-75``) are always ``< 40``.
- **Digit 5 — collection day**, ``1``-``5`` (Monday-Friday).
- **Trailing letter — biweekly week**, ``O`` or ``B`` (the alternating week). Present
  **only for recycling**, which alternates every other week. Refuse and
  organics are weekly and carry no letter.

So ``62081`` = zone 62, sequence 08 (< 40 → refuse), day 1 (Monday).
And ``62433O`` = zone 62, sequence 43 (>= 40 → recycling), day 3
(Wednesday), orange week.

These rules are encoded below as data (the prefix sets and sequence thresholds)
so they're easy to read, audit, and adjust as operations change.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# ---------------------------------------------------------------------------
# Column map: raw export name -> clean snake_case name.
# Only these columns are carried forward.
# ---------------------------------------------------------------------------
COLUMN_MAP = {
    "Site: Site ID": "site_id",
    "Site Group": "site_group",
    "Status": "status",
    "Assessor's Parcel Number": "apn",
    "ADA": "ada",
    "Key Stop Indicator": "key_stop",
    "X-Coordinate": "x",
    "Y-Coordinate": "y",
    "Number Of Units On Parcel": "units",
    "Site Address": "site_address",
    # Refuse inputs
    "Refuse Route": "ref_route_raw",
    "Refuse Day Of Week From GIS": "ref_day_raw",
    # Recycling inputs
    "Recycle Route": "rec_route_raw",
    "Recycle Day Of Week From GIS": "rec_day_raw",
    "Recycle Week": "rec_week_raw",
    # Organics inputs
    "Organics Route": "org_route_raw",
    "Organics Day Of Week From GIS": "org_day_raw",
}

# The columns kept when partitioning by route (the rest are intermediate).
BASE_COLS = [
    "site_id", "site_group", "status", "apn",
    "ada", "key_stop", "x", "y", "units", "site_address",
]

# ---------------------------------------------------------------------------
# Business rules, as data.
# ---------------------------------------------------------------------------
# Zone prefixes valid for each commodity (example schema — remap for your agency).
# 51/52 are manual routes and appear in all three commodities.
REF_PREFIXES = {"61", "62", "63", "64", "65", "51", "52"}
REC_PREFIXES = {"61", "62", "63", "64", "65", "51", "52"}
ORG_PREFIXES = {"71", "72", "73", "74", "75", "51", "52"}

# Manual-route zones (sequence thresholds differ for these).
MANUAL_PREFIXES = {"51", "52"}

# Automated organics zones.
AUTO_ORG_PREFIXES = {"71", "72", "73", "74", "75"}

# A conservative bounding box for the example service region in EPSG:2230 (feet).
# Coordinates outside this are data errors (zeros, placeholders, typos).
REGION_BBOX = (5_800_000, 6_600_000, 1_700_000, 2_200_000)  # (xmin, xmax, ymin, ymax)

VALID_DAYS = {"1", "2", "3", "4", "5"}
VALID_WEEKS = {"B", "O"}


# ---------------------------------------------------------------------------
# Field cleaners.
# ---------------------------------------------------------------------------
def _clean_digits(series: pd.Series) -> pd.Series:
    """Strip everything to bare digits; empty becomes NA."""
    import pandas as pd

    s = series.astype(str).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True)   # drop a trailing ".0" from floats
    s = s.str.replace(r"\D+", "", regex=True)    # keep digits only
    return s.replace("", pd.NA)


def clean_route4(series: pd.Series) -> pd.Series:
    """Normalize a raw route field to its last 4 digits, zero-padded."""
    return _clean_digits(series).str[-4:].str.zfill(4)


def clean_day(series: pd.Series) -> pd.Series:
    """Normalize a raw day field to a single digit 1-5; anything else is NA."""
    import pandas as pd

    d = _clean_digits(series)
    return d.where(d.isin(VALID_DAYS), pd.NA)


def clean_week(series: pd.Series) -> pd.Series:
    """Normalize a raw recycling-week field to 'B' or 'O'; anything else is NA."""
    import pandas as pd

    w = series.astype(str).str.strip().str.upper()
    return w.where(w.isin(VALID_WEEKS), pd.NA)


# ---------------------------------------------------------------------------
# Route ID assembly + validation.
# ---------------------------------------------------------------------------
def _prefix(route_id: pd.Series) -> pd.Series:
    return route_id.astype(str).str.slice(0, 2)


def _seq(route_id: pd.Series) -> pd.Series:
    import pandas as pd

    return pd.to_numeric(route_id.astype(str).str.slice(2, 4), errors="coerce")


def build_route_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``route_id_ref/rec/org`` columns from the cleaned route/day/week parts.

    Expects the cleaned columns produced by :func:`clean_sites`. Refuse and
    organics IDs are ``route4 + day`` (5 chars); recycling is
    ``route4 + day + week`` (6 chars) because it alternates biweekly.
    """
    import pandas as pd

    out = df.copy()

    # Refuse: route + day
    out["route_id_ref"] = pd.NA
    m = out["ref_route4"].notna() & out["ref_day"].notna()
    out.loc[m, "route_id_ref"] = out.loc[m, "ref_route4"] + out.loc[m, "ref_day"]

    # Recycling: route + day + week
    out["route_id_rec"] = pd.NA
    m = out["rec_route4"].notna() & out["rec_day"].notna() & out["rec_week"].notna()
    out.loc[m, "route_id_rec"] = (
        out.loc[m, "rec_route4"] + out.loc[m, "rec_day"] + out.loc[m, "rec_week"]
    )

    # Organics: route + day
    out["route_id_org"] = pd.NA
    m = out["org_route4"].notna() & out["org_day"].notna()
    out.loc[m, "route_id_org"] = out.loc[m, "org_route4"] + out.loc[m, "org_day"]

    for c in ["route_id_ref", "route_id_rec", "route_id_org"]:
        out[c] = out[c].astype("string").str.strip()

    return out


def valid_ref(route_id: pd.Series) -> pd.Series:
    """Boolean mask: which refuse route IDs satisfy the business rules.

    Refuse: a valid refuse prefix, day 1-5, sequence < 40.
    """
    p = _prefix(route_id)
    seq = _seq(route_id)
    day = route_id.str.slice(4, 5)
    return (
        route_id.notna()
        & p.isin(REF_PREFIXES)
        & day.isin(VALID_DAYS)
        & seq.notna()
        & (seq < 40)
    )


def valid_rec(route_id: pd.Series) -> pd.Series:
    """Boolean mask: which recycling route IDs satisfy the business rules.

    Recycling: a valid prefix, day 1-5, week B/O, and sequence >= 40. For manual
    routes (11/12) the recycling band is specifically 40-59 (>= 60 is organics).
    """
    p = _prefix(route_id)
    seq = _seq(route_id)
    day = route_id.str.slice(4, 5)
    week = route_id.str.slice(5, 6)
    return (
        route_id.notna()
        & p.isin(REC_PREFIXES)
        & day.isin(VALID_DAYS)
        & week.isin(VALID_WEEKS)
        & seq.notna()
        & (
            (~p.isin(MANUAL_PREFIXES) & (seq >= 40))
            | (p.isin(MANUAL_PREFIXES) & (seq >= 40) & (seq < 60))
        )
    )


def valid_org(route_id: pd.Series) -> pd.Series:
    """Boolean mask: which organics route IDs satisfy the business rules.

    Organics: automated organics zones (71-75) with sequence < 40, OR
    manual routes (11/12) with sequence >= 60.
    """
    p = _prefix(route_id)
    seq = _seq(route_id)
    day = route_id.str.slice(4, 5)
    return (
        route_id.notna()
        & p.isin(ORG_PREFIXES)
        & day.isin(VALID_DAYS)
        & seq.notna()
        & (
            (p.isin(AUTO_ORG_PREFIXES) & (seq < 40))
            | (p.isin(MANUAL_PREFIXES) & (seq >= 60))
        )
    )


# ---------------------------------------------------------------------------
# Top-level pipeline.
# ---------------------------------------------------------------------------
def in_region_bbox(df: pd.DataFrame, bbox: tuple = REGION_BBOX) -> pd.Series:
    """Boolean mask: rows whose x/y fall inside the configured region envelope.

    Catches placeholder/garbage coordinates (zeros, tiny values, typos) that
    would otherwise pollute spatial work.
    """
    xmin, xmax, ymin, ymax = bbox
    return df["x"].between(xmin, xmax) & df["y"].between(ymin, ymax)


def clean_sites(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Run the full sites cleaning pipeline on a raw export DataFrame.

    Steps: select + rename columns, clean route/day/week fields, parse
    coordinates, build route IDs, and drop rows with out-of-region coordinates.

    Returns a cleaned ("master") DataFrame retaining both raw and normalized
    columns, so the result is auditable. Validation masks
    (:func:`valid_ref` etc.) are applied by the caller when partitioning.
    """
    import pandas as pd

    missing = [c for c in COLUMN_MAP if c not in df_raw.columns]
    if missing:
        raise ValueError(f"Missing required columns in input: {missing}")

    df = df_raw[list(COLUMN_MAP)].rename(columns=COLUMN_MAP).copy()

    # Clean the route / day / week parts for each commodity.
    df["ref_route4"] = clean_route4(df["ref_route_raw"])
    df["rec_route4"] = clean_route4(df["rec_route_raw"])
    df["org_route4"] = clean_route4(df["org_route_raw"])
    df["ref_day"] = clean_day(df["ref_day_raw"])
    df["rec_day"] = clean_day(df["rec_day_raw"])
    df["org_day"] = clean_day(df["org_day_raw"])
    df["rec_week"] = clean_week(df["rec_week_raw"])

    # Coordinates to numeric.
    df["x"] = pd.to_numeric(df["x"], errors="coerce")
    df["y"] = pd.to_numeric(df["y"], errors="coerce")

    # Build the route IDs.
    df = build_route_ids(df)

    # Drop rows with no coordinates, then those outside the region.
    df = df.dropna(subset=["x", "y"]).copy()
    df = df.loc[in_region_bbox(df)].copy()

    return df


def load_sites(path: str | Path) -> pd.DataFrame:
    """Read a raw sites CSV and return the cleaned master DataFrame."""
    import pandas as pd

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Sites file not found: {path}")
    df_raw = pd.read_csv(path, low_memory=False)
    return clean_sites(df_raw)


# ---------------------------------------------------------------------------
# Per-route parquet writer.
# ---------------------------------------------------------------------------
# A small, focused writer that partitions cleaned sites by route ID and writes
# one parquet file per route. The pattern is **wipe-and-rewrite**: existing
# *.parquet files in the output folder are cleared first, so re-running on a
# fresh cleaning is deterministic and safe.
#
# Why partition? Each downstream tool (RouteView for one route + day) only
# needs *its* route's sites — not the whole 225k-row master. One small parquet
# per route is fast to read and easy to ship.

def write_routes_by_commodity(
    df_master: pd.DataFrame,
    route_col: str,
    valid_mask: pd.Series,
    out_dir: str | Path,
    *,
    columns: list[str] | None = None,
) -> int:
    """Write one parquet per valid route_id under ``out_dir``.

    Parameters
    ----------
    df_master:
        The cleaned master DataFrame from :func:`clean_sites`.
    route_col:
        Which route-ID column to partition on (``route_id_ref``,
        ``route_id_rec``, or ``route_id_org``).
    valid_mask:
        Boolean mask of valid rows for this commodity (from :func:`valid_ref`,
        :func:`valid_rec`, or :func:`valid_org`). Rows where the mask is False
        are dropped before writing.
    out_dir:
        Folder to write into. Created if missing. Existing ``*.parquet`` files
        are removed first so the result is a clean snapshot.
    columns:
        Which columns to keep in each per-route file. Defaults to
        :data:`BASE_COLS` + ``route_id``.

    Returns
    -------
    Number of route files written.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    # Wipe-and-rewrite: clear stale partitions for a deterministic snapshot.
    for f in out_path.glob("*.parquet"):
        f.unlink()

    df = df_master.loc[valid_mask & df_master[route_col].notna()].copy()
    if df.empty:
        return 0

    df["route_id"] = df[route_col].astype(str).str.strip()
    keep = (columns if columns is not None else BASE_COLS) + ["route_id"]
    df = df[keep]

    written = 0
    for rid, group in df.groupby("route_id"):
        group.to_parquet(out_path / f"{rid}.parquet", index=False)
        written += 1
    return written


# ---------------------------------------------------------------------------
# Geometry: sites as a points layer + spatial join to parcels
# ---------------------------------------------------------------------------
# Sites arrive as a tabular CSV (typically from Salesforce) with lat/lon
# columns per site. To answer "which parcel does this site sit on?" we
# build a points GeoDataFrame and run a point-in-polygon spatial join
# against the parcel layer from prep.parcels. The parcel's APN comes back
# attached to each site — without needing the source CSV to carry one.

# Default column names in the Salesforce export. Override via arguments.
DEFAULT_LON_COL = "Longitude"
DEFAULT_LAT_COL = "Latitude"


def sites_to_geo(
    df: pd.DataFrame,
    *,
    lon_col: str = DEFAULT_LON_COL,
    lat_col: str = DEFAULT_LAT_COL,
    source_crs: str = "EPSG:4326",
):
    """Turn a tabular sites DataFrame into a points GeoDataFrame.

    The Salesforce export typically has ``Longitude`` and ``Latitude`` columns
    in WGS84 (EPSG:4326). This function builds a points GeoDataFrame in
    ``source_crs`` — callers can reproject as needed (use
    :func:`opentrash.core.crs.to_working` if you need feet for distance work).

    Rows with missing or non-finite coordinates are dropped: a point with no
    location can't be spatially joined to anything, so keeping it would just
    pollute downstream output with nulls.

    Parameters
    ----------
    df:
        The cleaned sites DataFrame (typically the output of :func:`clean_sites`).
    lon_col, lat_col:
        Column names carrying longitude and latitude. Defaults match the
        Salesforce export shape; override for other sources.
    source_crs:
        The CRS the coordinates are expressed in. Default is WGS84
        (lon/lat degrees), which is virtually always right for a CSV.
    """
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point

    if lon_col not in df.columns or lat_col not in df.columns:
        raise KeyError(
            f"Sites DataFrame is missing geometry columns: "
            f"need {lon_col!r} and {lat_col!r}, "
            f"have {[c for c in df.columns if c in (lon_col, lat_col)]}."
        )

    lon = pd.to_numeric(df[lon_col], errors="coerce")
    lat = pd.to_numeric(df[lat_col], errors="coerce")
    valid = lon.notna() & lat.notna()
    valid &= lon.between(-180, 180) & lat.between(-90, 90)

    sub = df.loc[valid].copy()
    geometry = [Point(xy) for xy in zip(lon[valid], lat[valid], strict=True)]
    return gpd.GeoDataFrame(sub, geometry=geometry, crs=source_crs)


def attach_apns_via_spatial_join(
    sites_gdf,
    parcels_gdf,
    *,
    apn_col: str = "APN",
):
    """Attach the parcel's APN to each site via point-in-polygon spatial join.

    Sites are points; parcels are polygons. A point sits on at most one
    parcel in practice (parcels are non-overlapping property lots), so we use
    GeoPandas' ``sjoin`` with ``predicate="within"``. Sites that fall in no
    parcel keep ``APN`` as NaN — useful to surface as a data-quality flag.

    Both inputs must be in the same CRS. If they aren't, this function
    reprojects ``parcels_gdf`` to match the sites CRS (we keep sites in their
    original CRS so any subsequent rendering stays in the same coordinate
    space the user worked in).

    Parameters
    ----------
    sites_gdf:
        A points GeoDataFrame (typically produced by :func:`sites_to_geo`).
    parcels_gdf:
        A polygons GeoDataFrame carrying at least an APN column (the output
        of :func:`opentrash.prep.parcels.load_parcels`).
    apn_col:
        The column in ``parcels_gdf`` carrying the parcel identifier.

    Returns
    -------
    A GeoDataFrame: the sites with an ``APN`` column attached. The geometry
    stays the original point geometry; we only add the parcel's identifier.
    """
    import geopandas as gpd

    if apn_col not in parcels_gdf.columns:
        raise KeyError(
            f"Parcels GeoDataFrame is missing the APN column {apn_col!r}. "
            f"Available columns: {list(parcels_gdf.columns)}"
        )
    if sites_gdf.crs is None or parcels_gdf.crs is None:
        raise ValueError(
            "Both sites and parcels must have a CRS set before spatial join."
        )

    # Match CRS so coordinates mean the same thing on both sides.
    if str(sites_gdf.crs).upper() != str(parcels_gdf.crs).upper():
        parcels_gdf = parcels_gdf.to_crs(sites_gdf.crs)

    # Keep only the APN column from parcels to avoid name collisions on
    # other columns (sites and parcels often share names like "ADDRESS").
    parcels_min = parcels_gdf[[apn_col, "geometry"]].copy()

    joined = gpd.sjoin(
        sites_gdf,
        parcels_min,
        how="left",
        predicate="within",
    )

    # sjoin adds an "index_right" column we don't need; APN now carries the
    # spatial join's payload. If a site fell on no parcel, APN is NaN.
    drop_cols = [c for c in ("index_right",) if c in joined.columns]
    return joined.drop(columns=drop_cols)


def load_sites_with_apns(
    sites_path: str | Path,
    parcels_gdf,
    *,
    lon_col: str = DEFAULT_LON_COL,
    lat_col: str = DEFAULT_LAT_COL,
    apn_col: str = "APN",
):
    """The full sites pipeline: load → clean → geom → spatial join → APNs attached.

    The one-call entry point most callers want: take a sites CSV/parquet and a
    parcels GeoDataFrame, get back a GeoDataFrame of sites with parcel APNs
    attached via spatial join.

    Equivalent to::

        df    = load_sites(sites_path)
        geo   = sites_to_geo(df, lon_col=lon_col, lat_col=lat_col)
        out   = attach_apns_via_spatial_join(geo, parcels_gdf, apn_col=apn_col)
    """
    df = load_sites(sites_path)
    gdf = sites_to_geo(df, lon_col=lon_col, lat_col=lat_col)
    return attach_apns_via_spatial_join(gdf, parcels_gdf, apn_col=apn_col)
