"""Pattern detection — turn enriched pings into per-parcel service signatures.

The detection is **route-agnostic by design**. We look at every (parcel,
vehicle) pair across the analysis window and rank vehicles by visit
regularity. The top-3 vehicles per parcel naturally map to the 3
commodities (refuse, organics, recycling) because in operations one
vehicle is dedicated to one commodity.

For every parcel that's been visited often enough, find:

- **weekly1** — the most-regular vehicle/day-of-week visit (top pick).
- **weekly2** — the second-most-regular (often same DOW, different truck).
- **biweekly** — visits *not* every week but every other week, with a
  clean phase (the "Tuesday on even weeks" pattern). Detected via a
  composite score over gap regularity and phase share.

The pipeline is a chain of **CTAS steps** (CREATE TABLE AS SELECT) in
DuckDB, each one a small set-based transformation. Reading them in
order is a tour of how to do real analytics with SQL instead of pandas
loops::

    enriched pings + parcels_wkb (sidecar metadata)
      WHERE apn IS NOT NULL
        AND dt_local BETWEEN start AND end
        [AND apn IN (chunk's APNs)]
      -> refined            (the analysis universe)
      -> stops              (dwell-debounced via stop_bin_minutes)
      -> daily_visits       (one row per parcel/vehicle/day)
      -> dow_stats          (regularity per parcel/vehicle/dow)
      -> best_dow           (the top DOW per parcel/vehicle)
      -> best_days          (the daily_visits rows for that best DOW)

    Branch A — biweekly:
      -> biweekly_gaps      (gap-day analysis between consecutive visits)
      -> biweekly_phase     (even/odd week share around an anchor)
      -> biweekly_scored    (composite score, filtered by thresholds)
      -> biweekly_best      (top parcel/vehicle pair per APN)

    Branch B — weekly ranking (excludes biweekly winners):
      -> weekly_pool        (best_dow rows minus biweekly winners)
      -> weekly_ranked      (row_number per APN)
      -> weekly1, weekly2   (rank 1 and 2)

    Final:
      -> patterns           (parcels_wkb LEFT JOIN weekly1 weekly2 biweekly)

The route columns (``route_ref``, ``route_org``, ``route_rec``) carried
through from ``parcels_wkb`` are **sidecar metadata** — they help a
human read the output but never enter the detection logic. The
algorithm is purely about (parcel, vehicle) regularity over time.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from .config import DEFAULT_CONFIG, PatternConfig

if TYPE_CHECKING:
    import duckdb
    import pandas as pd

# Canonical column order of the final patterns table. Downstream readers
# (RouteView, business reports) depend on this shape.
PATTERNS_COLUMNS: tuple[str, ...] = (
    "APN", "route_ref", "route_org", "route_rec",
    # weekly1 (top weekly pick)
    "weekly1_vehicle", "weekly1_dow", "weekly1_hour",
    "weekly1_regularity", "weekly1_visits", "weekly1_weeks_present",
    # weekly2 (runner-up weekly pick)
    "weekly2_vehicle", "weekly2_dow", "weekly2_hour",
    "weekly2_regularity", "weekly2_visits", "weekly2_weeks_present",
    # biweekly (separate detector)
    "biweekly_vehicle", "biweekly_dow", "biweekly_hour",
    "biweekly_weeks_present", "biweekly_regularity", "biweekly_visit_days",
    "biweekly_med_gap", "biweekly_pct14", "biweekly_phase_share",
    "biweekly_score", "biweekly_even_hits", "biweekly_odd_hits",
    # analysis-window metadata (carried so the output is self-describing)
    "analysis_min_day", "analysis_max_day", "analysis_total_weeks",
)


def detect_patterns_chunk(
    con: duckdb.DuckDBPyConnection,
    enriched_glob: str | Path,
    parcels_wkb_path: str | Path,
    start_date: date | str,
    end_date: date | str,
    *,
    chunk_apns: list[str] | None = None,
    config: PatternConfig | None = None,
) -> pd.DataFrame:
    """Run pattern detection on one chunk of parcels.

    A "chunk" is just a slice of the APN universe (typically all parcels
    on one route, used for organizational batching). The detection
    algorithm itself is route-agnostic — chunking only restricts WHICH
    parcels we look at, never how we look at them.

    Parameters
    ----------
    con:
        A live DuckDB connection. The function creates and drops many
        intermediate tables on this connection.
    enriched_glob:
        Glob pattern or single path matching one or more enriched-pings
        parquet files (output of :func:`opentrash.engine.enrichment.enrich_pings`).
        Example: ``"enriched/*/815*.parquet"``.
    parcels_wkb_path:
        Path to the parcels_wkb parquet (output of
        :func:`opentrash.prep.parcels_wkb.write_parcels_wkb`). Provides the
        APN + route_ref/org/rec sidecar columns carried into the final output.
    start_date, end_date:
        Explicit analysis window. The denominator for regularity scores is
        derived from this window (``total_weeks``), not from the data, so a
        parcel with sparse data still gets compared against the full window.
    chunk_apns:
        Optional list of APNs to restrict this chunk to. ``None`` means "all
        parcels in parcels_wkb." Used by :mod:`opentrash.patterns.runner` to
        process the service area in route-sized batches.
    config:
        Optional :class:`PatternConfig`. Defaults to :data:`DEFAULT_CONFIG`
        (production-tuned values from a year of real data).
    """
    import duckdb  # noqa: F401  (used via the con argument)

    cfg = config or DEFAULT_CONFIG
    start = _to_date(start_date)
    end = _to_date(end_date)
    if end <= start:
        raise ValueError(
            f"end_date ({end}) must be strictly after start_date ({start})."
        )

    parcels_wkb_path = Path(parcels_wkb_path)
    if not parcels_wkb_path.exists():
        raise FileNotFoundError(
            f"parcels_wkb parquet not found: {parcels_wkb_path}"
        )

    enriched_sql = _path_for_sql(enriched_glob)
    parcels_sql = _path_for_sql(parcels_wkb_path)

    # Effective min-weeks thresholds: take the MAX of the flat floor and the
    # ratio applied to the actual window's total_weeks. Flat floor protects
    # very short windows from a tiny ratio; ratio scales gracefully across
    # long windows where a flat number would be too lenient.
    total_weeks_preview = max(1, (end - start).days // 7 + 1)
    effective_weekly_min_weeks = max(
        cfg.weekly_min_weeks,
        int(round(cfg.weekly_min_weeks_ratio * total_weeks_preview)),
    )
    effective_biweekly_min_weeks = max(
        cfg.biweekly_min_weeks,
        int(round(cfg.biweekly_min_weeks_ratio * total_weeks_preview)),
    )
    import dataclasses
    cfg = dataclasses.replace(
        cfg,
        weekly_min_weeks=effective_weekly_min_weeks,
        biweekly_min_weeks=effective_biweekly_min_weeks,
    )

    # Build the refined view: enriched pings with apn -> APN rename, filtered
    # to the analysis window, optionally to a chunk's APNs. parcels_wkb is
    # joined for the route_ref/org/rec sidecar columns. Route columns flow
    # through but never enter the detection logic.
    apn_filter = ""
    if chunk_apns is not None:
        if len(chunk_apns) == 0:
            # Empty chunk -> empty result, no need to run the pipeline.
            return _empty_patterns_frame()
        apns_quoted = ", ".join(f"'{a}'" for a in chunk_apns)
        apn_filter = f"AND e.apn IN ({apns_quoted})"

    con.execute("DROP VIEW IF EXISTS refined_src")
    con.execute("DROP VIEW IF EXISTS parcels_src")
    con.execute(f"""
        CREATE VIEW refined_src AS
        SELECT
            e.apn               AS APN,
            e.vehicle_id        AS VehicleName,
            CAST(e.dt_local AS TIMESTAMP) AS dt_local,
            p.route_ref,
            p.route_org,
            p.route_rec
        FROM read_parquet('{enriched_sql}') e
        LEFT JOIN read_parquet('{parcels_sql}') p ON e.apn = p.APN
        WHERE e.apn IS NOT NULL
          AND CAST(e.dt_local AS DATE) BETWEEN DATE '{start.isoformat()}'
                                           AND DATE '{end.isoformat()}'
          {apn_filter}
    """)
    con.execute(f"""
        CREATE VIEW parcels_src AS
        SELECT APN, route_ref, route_org, route_rec
        FROM read_parquet('{parcels_sql}')
        {"WHERE APN IN (" + apns_quoted + ")" if chunk_apns else ""}
    """)

    # Use the explicit window for total_weeks (not data min/max).
    total_weeks = max(1, (end - start).days // 7 + 1)

    # Early exit if refined_src is empty — return the chunk's parcels with
    # null pattern columns rather than raising.
    n_refined = con.execute("SELECT COUNT(*) FROM refined_src").fetchone()[0]
    if n_refined == 0:
        return _build_empty_chunk_result(con, start, end, total_weeks)

    _build_stops(con, "refined_src", cfg)
    _build_daily_visits(con)
    _build_dow_stats(con, total_weeks)
    _build_best_dow(con)
    _build_best_days(con)

    week0 = con.execute("SELECT MIN(week_start) FROM best_days").fetchone()[0]
    if week0 is None:
        # No best_days rows — every parcel in the chunk failed thresholds.
        return _build_empty_chunk_result(con, start, end, total_weeks)

    _build_biweekly_gaps(con, cfg)
    _build_biweekly_phase(con, week0)
    _build_biweekly_scored(con, cfg)
    _build_biweekly_best(con, cfg)

    _build_weekly_pool(con, cfg)
    _build_weekly_ranked(con)
    _build_weekly_slot(con, "weekly1", rank=1)
    _build_weekly_slot(con, "weekly2", rank=2)

    return _build_final_patterns(con, "parcels_src", start, end, total_weeks)


def _to_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _path_for_sql(path: str | Path) -> str:
    """Return a forward-slashed path string safe to embed in DuckDB SQL."""
    return str(path).replace("\\", "/")


def _empty_patterns_frame() -> pd.DataFrame:
    """An empty DataFrame in PATTERNS_COLUMNS shape, for empty chunks."""
    import pandas as pd
    return pd.DataFrame({c: [] for c in PATTERNS_COLUMNS})


def _build_empty_chunk_result(
    con: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    total_weeks: int,
) -> pd.DataFrame:
    """Build the chunk's parcels with null pattern columns (no matches case).

    Used when refined_src is empty or every best_days row failed
    thresholds. We still want to return one row per parcel in the chunk,
    just with null pattern fields — so the manifest of what was analyzed
    stays complete.
    """
    null_select = ", ".join(
        f"NULL AS {c}"
        for c in PATTERNS_COLUMNS
        if c not in ("APN", "route_ref", "route_org", "route_rec",
                     "analysis_min_day", "analysis_max_day",
                     "analysis_total_weeks")
    )
    return con.execute(f"""
        SELECT
            APN, route_ref, route_org, route_rec,
            {null_select},
            DATE '{start.isoformat()}' AS analysis_min_day,
            DATE '{end.isoformat()}'   AS analysis_max_day,
            {total_weeks}              AS analysis_total_weeks
        FROM parcels_src
    """).df()


# ---------------------------------------------------------------------------
# CTAS step builders.
#
# Each step is a small, named function. They take the open DuckDB connection
# and create one named table; later steps read from earlier ones. Splitting
# them out keeps the SQL inspectable, testable, and replaceable one piece at
# a time. These are unchanged from v1 — they implement the detection
# algorithm, which is route-agnostic and stays stable across architectures.
# ---------------------------------------------------------------------------
def _build_stops(con: duckdb.DuckDBPyConnection, refined: str, cfg: PatternConfig) -> None:
    """One row per parcel/vehicle/15-min-bin: when did the truck *stop* here?

    A "stop bin" is a fixed-width window in epoch seconds. Two pings 30
    seconds apart land in the same bin; two pings 16 minutes apart land in
    different bins. This is how we collapse a stream of pings near one parcel
    into discrete service events.
    """
    con.execute("DROP TABLE IF EXISTS stops")
    con.execute(f"""
        CREATE TABLE stops AS
        WITH src AS (
            SELECT
                APN,
                VehicleName,
                CAST(dt_local AS DATE) AS day,
                date_trunc('week', CAST(dt_local AS DATE))::DATE AS week_start,
                dt_local,
                CAST(epoch(dt_local) / ({cfg.stop_bin_minutes} * 60) AS BIGINT) AS bin_id
            FROM {refined}
            WHERE dt_local IS NOT NULL
        )
        SELECT
            APN,
            VehicleName,
            day,
            week_start,
            bin_id,
            strftime(MIN(dt_local), '%a') AS dow,
            CAST(strftime(MIN(dt_local), '%H') AS INTEGER) AS hour_24,
            COUNT(*) AS hits
        FROM src
        GROUP BY APN, VehicleName, day, week_start, bin_id
    """)


def _build_daily_visits(con: duckdb.DuckDBPyConnection) -> None:
    """Collapse stop bins into one row per parcel/vehicle/day with summary stats."""
    con.execute("DROP TABLE IF EXISTS daily_visits")
    con.execute("""
        CREATE TABLE daily_visits AS
        SELECT
            APN,
            VehicleName,
            day,
            week_start,
            dow,
            CAST(ROUND(AVG(hour_24)) AS INTEGER) AS typical_day_hour,
            COUNT(*) AS stop_bins,
            SUM(hits) AS raw_hits
        FROM stops
        GROUP BY APN, VehicleName, day, week_start, dow
    """)


def _build_dow_stats(con: duckdb.DuckDBPyConnection, total_weeks: int) -> None:
    """For each parcel/vehicle/day-of-week, how regular is the service?

    ``regularity`` is the share of all analysis-window weeks where this
    parcel/vehicle pair appeared on this DOW. Higher = more reliable pattern.
    """
    con.execute("DROP TABLE IF EXISTS dow_stats")
    con.execute(f"""
        CREATE TABLE dow_stats AS
        SELECT
            APN,
            VehicleName,
            dow,
            COUNT(DISTINCT week_start) AS weeks_present,
            ROUND(COUNT(DISTINCT week_start)::DOUBLE / {int(total_weeks)}, 4) AS regularity,
            COUNT(DISTINCT day) AS visit_days,
            CAST(ROUND(AVG(typical_day_hour)) AS INTEGER) AS typical_hour
        FROM daily_visits
        GROUP BY APN, VehicleName, dow
    """)


def _build_best_dow(con: duckdb.DuckDBPyConnection) -> None:
    """Top DOW per parcel/vehicle (highest regularity, then most visit days)."""
    con.execute("DROP TABLE IF EXISTS best_dow")
    con.execute("""
        CREATE TABLE best_dow AS
        SELECT *
        FROM (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY APN, VehicleName
                    ORDER BY regularity DESC, visit_days DESC, dow
                ) AS rn
            FROM dow_stats
        )
        WHERE rn = 1
    """)


def _build_best_days(con: duckdb.DuckDBPyConnection) -> None:
    """The actual daily-visit rows that fall on each parcel/vehicle's best DOW."""
    con.execute("DROP TABLE IF EXISTS best_days")
    con.execute("""
        CREATE TABLE best_days AS
        SELECT
            d.APN,
            d.VehicleName,
            d.day,
            d.week_start,
            d.dow,
            d.typical_day_hour
        FROM daily_visits d
        JOIN best_dow b
          ON d.APN = b.APN
         AND d.VehicleName = b.VehicleName
         AND d.dow = b.dow
    """)


def _build_biweekly_gaps(con: duckdb.DuckDBPyConnection, cfg: PatternConfig) -> None:
    """Gap-day analysis: median gap, share within target tolerance, visit count."""
    con.execute("DROP TABLE IF EXISTS biweekly_gaps")
    con.execute(f"""
        CREATE TABLE biweekly_gaps AS
        WITH g AS (
            SELECT
                APN,
                VehicleName,
                day,
                date_diff('day',
                    LAG(day) OVER (PARTITION BY APN, VehicleName ORDER BY day),
                    day) AS gap_days
            FROM best_days
        )
        SELECT
            APN,
            VehicleName,
            MEDIAN(gap_days) FILTER (WHERE gap_days IS NOT NULL) AS med_gap,
            AVG(
                CASE
                    WHEN gap_days IS NOT NULL
                     AND ABS(gap_days - {cfg.biweekly_gap_target}) <= {cfg.biweekly_gap_tolerance}
                    THEN 1.0 ELSE 0.0
                END
            ) FILTER (WHERE gap_days IS NOT NULL) AS pct14,
            COUNT(*) AS visits
        FROM g
        GROUP BY APN, VehicleName
    """)


def _build_biweekly_phase(con: duckdb.DuckDBPyConnection, week0) -> None:
    """Phase analysis: how cleanly do visits split into even-vs-odd weeks?

    ``phase_share`` = max(even_hits, odd_hits) / total_weeks. A clean biweekly
    pattern lands all its visits in one phase (share = 1.0); a noisy one
    splits between phases (share ~ 0.5).
    """
    con.execute("DROP TABLE IF EXISTS biweekly_phase")
    con.execute(f"""
        CREATE TABLE biweekly_phase AS
        SELECT
            APN,
            VehicleName,
            SUM(CASE WHEN (date_diff('week', DATE '{week0}', week_start) % 2) = 0
                     THEN 1 ELSE 0 END) AS even_hits,
            SUM(CASE WHEN (date_diff('week', DATE '{week0}', week_start) % 2) = 1
                     THEN 1 ELSE 0 END) AS odd_hits,
            COUNT(DISTINCT week_start) AS phase_weeks,
            ROUND(
                GREATEST(
                    SUM(CASE WHEN (date_diff('week', DATE '{week0}', week_start) % 2) = 0
                             THEN 1 ELSE 0 END),
                    SUM(CASE WHEN (date_diff('week', DATE '{week0}', week_start) % 2) = 1
                             THEN 1 ELSE 0 END)
                )::DOUBLE / NULLIF(COUNT(DISTINCT week_start), 0),
                4
            ) AS phase_share
        FROM best_days
        GROUP BY APN, VehicleName
    """)


def _build_biweekly_scored(con: duckdb.DuckDBPyConnection, cfg: PatternConfig) -> None:
    """Combine gap + phase + regularity into a composite score, apply thresholds.

    Score = ``w_phase * phase_share + w_pct14 * pct14 + w_reg * reg_centeredness``,
    where ``reg_centeredness = 1 - |reg - 0.5| / 0.5`` (peaks at reg ≈ 0.5,
    which is what a clean biweekly looks like in weekly-regularity terms).
    """
    con.execute("DROP TABLE IF EXISTS biweekly_scored")
    con.execute(f"""
        CREATE TABLE biweekly_scored AS
        SELECT
            b.APN,
            b.VehicleName,
            b.dow,
            b.typical_hour,
            b.weeks_present,
            b.regularity,
            b.visit_days,
            g.med_gap,
            COALESCE(g.pct14, 0.0) AS pct14,
            COALESCE(g.visits, 0) AS visits,
            p.phase_share,
            p.even_hits,
            p.odd_hits,
            ROUND(
                {cfg.biweekly_score_weight_phase} * COALESCE(p.phase_share, 0.0)
              + {cfg.biweekly_score_weight_pct14} * COALESCE(g.pct14, 0.0)
              + {cfg.biweekly_score_weight_regularity_centeredness}
                  * GREATEST(0.0, 1.0 - ABS(b.regularity - 0.5) / 0.5),
                4
            ) AS biweekly_score
        FROM best_dow b
        LEFT JOIN biweekly_gaps g
          ON b.APN = g.APN AND b.VehicleName = g.VehicleName
        LEFT JOIN biweekly_phase p
          ON b.APN = p.APN AND b.VehicleName = p.VehicleName
        WHERE b.weeks_present >= {cfg.biweekly_min_weeks}
          AND b.visit_days   >= {cfg.biweekly_min_visits}
          AND COALESCE(p.phase_share, 0.0) >= {cfg.biweekly_min_phase_share}
          AND b.regularity   <= {cfg.biweekly_max_weekly_regularity}
    """)


def _build_biweekly_best(con: duckdb.DuckDBPyConnection, cfg: PatternConfig) -> None:
    """Top biweekly candidate per APN (best composite score wins)."""
    con.execute("DROP TABLE IF EXISTS biweekly_best")
    con.execute(f"""
        CREATE TABLE biweekly_best AS
        SELECT *
        FROM (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY APN
                    ORDER BY biweekly_score DESC, phase_share DESC,
                             pct14 DESC, visit_days DESC, VehicleName
                ) AS rn
            FROM biweekly_scored
            WHERE biweekly_score >= {cfg.biweekly_min_score}
        )
        WHERE rn = 1
    """)


def _build_weekly_pool(con: duckdb.DuckDBPyConnection, cfg: PatternConfig) -> None:
    """Weekly candidates = best_dow rows whose APN has no biweekly winner."""
    con.execute("DROP TABLE IF EXISTS weekly_pool")
    con.execute(f"""
        CREATE TABLE weekly_pool AS
        SELECT b.*
        FROM best_dow b
        LEFT JOIN biweekly_best bw
          ON b.APN = bw.APN
         AND b.VehicleName = bw.VehicleName
        WHERE bw.APN IS NULL
          AND b.regularity    >= {cfg.weekly_min_regularity}
          AND b.weeks_present >= {cfg.weekly_min_weeks}
    """)


def _build_weekly_ranked(con: duckdb.DuckDBPyConnection) -> None:
    """Rank weekly candidates per APN by regularity (then visit count)."""
    con.execute("DROP TABLE IF EXISTS weekly_ranked")
    con.execute("""
        CREATE TABLE weekly_ranked AS
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY APN
                ORDER BY regularity DESC, visit_days DESC, VehicleName
            ) AS rank
        FROM weekly_pool
    """)


def _build_weekly_slot(con: duckdb.DuckDBPyConnection, table: str, *, rank: int) -> None:
    """Pull rank=N from weekly_ranked into a named slot table (weekly1, weekly2)."""
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(f"CREATE TABLE {table} AS SELECT * FROM weekly_ranked WHERE rank = {rank}")


def _build_final_patterns(
    con: duckdb.DuckDBPyConnection,
    parcels_table: str,
    min_day,
    max_day,
    total_weeks: int,
) -> pd.DataFrame:
    """Assemble the wide final table and return it as a pandas DataFrame.

    ``parcels`` is the outer side of the LEFT JOIN so every parcel appears in
    the output — parcels with no detected pattern come through with NULLs.
    """
    con.execute("DROP TABLE IF EXISTS patterns")
    con.execute(f"""
        CREATE TABLE patterns AS
        SELECT
            p.APN,
            p.route_ref,
            p.route_org,
            p.route_rec,

            w1.VehicleName    AS weekly1_vehicle,
            w1.dow            AS weekly1_dow,
            w1.typical_hour   AS weekly1_hour,
            w1.regularity     AS weekly1_regularity,
            w1.visit_days     AS weekly1_visits,
            w1.weeks_present  AS weekly1_weeks_present,

            w2.VehicleName    AS weekly2_vehicle,
            w2.dow            AS weekly2_dow,
            w2.typical_hour   AS weekly2_hour,
            w2.regularity     AS weekly2_regularity,
            w2.visit_days     AS weekly2_visits,
            w2.weeks_present  AS weekly2_weeks_present,

            bw.VehicleName    AS biweekly_vehicle,
            bw.dow            AS biweekly_dow,
            bw.typical_hour   AS biweekly_hour,
            bw.weeks_present  AS biweekly_weeks_present,
            bw.regularity     AS biweekly_regularity,
            bw.visit_days     AS biweekly_visit_days,
            bw.med_gap        AS biweekly_med_gap,
            bw.pct14          AS biweekly_pct14,
            bw.phase_share    AS biweekly_phase_share,
            bw.biweekly_score AS biweekly_score,
            bw.even_hits      AS biweekly_even_hits,
            bw.odd_hits       AS biweekly_odd_hits,

            DATE '{min_day}'      AS analysis_min_day,
            DATE '{max_day}'      AS analysis_max_day,
            {int(total_weeks)}    AS analysis_total_weeks
        FROM {parcels_table} p
        LEFT JOIN weekly1 w1       ON p.APN = w1.APN
        LEFT JOIN weekly2 w2       ON p.APN = w2.APN
        LEFT JOIN biweekly_best bw ON p.APN = bw.APN
    """)
    df = con.execute("SELECT * FROM patterns").fetchdf()
    return df[list(PATTERNS_COLUMNS)]
