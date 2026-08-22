"""Pattern detection — tunable parameters.

Every threshold and weight the pattern detector uses lives here as a single
frozen dataclass. Defaults match values that produced clean patterns on a
year of real residential-collection data.

Why a dataclass and not a dict? Typed fields, autocomplete, ``repr`` for
logging, immutability (so the config can't drift mid-run), and one
obvious place to look when you ask "what knobs are there?".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PatternConfig:
    """All knobs for :func:`opentrash.patterns.detector.detect_patterns_chunk`.

    Three logical groups: stop binning, weekly pattern acceptance, biweekly
    pattern acceptance (with composite scoring).
    """

    # ------------------------------------------------------------------
    # Stop binning — how we group nearby pings into a single "visit".
    # ------------------------------------------------------------------
    # Width of the time bin (minutes) for grouping pings into a single stop.
    # 15 min works for residential routes where a truck spends seconds per
    # parcel but minutes per block.
    stop_bin_minutes: int = 15

    # ------------------------------------------------------------------
    # Weekly pattern acceptance.
    # ------------------------------------------------------------------
    # Minimum regularity = (weeks_present / total_weeks) on the best DOW.
    # 0.25 = the truck must hit this parcel on at least 1 in 4 weeks.
    weekly_min_regularity: float = 0.25

    # Minimum number of weeks the parcel was visited on the best DOW.
    # Effective floor is ``max(weekly_min_weeks, weekly_min_weeks_ratio * total_weeks)``.
    # Floor handles very short windows where ratio would be tiny; ratio
    # handles long windows where a flat number would be too lenient.
    weekly_min_weeks: int = 6
    weekly_min_weeks_ratio: float = 0.25

    # ------------------------------------------------------------------
    # Biweekly pattern acceptance.
    # ------------------------------------------------------------------
    # Target gap in days between visits for a "biweekly" pattern.
    biweekly_gap_target: int = 14
    # Tolerance around the target (days). 14 ± 4 = 10..18 day gaps count.
    biweekly_gap_tolerance: int = 4
    # Minimum number of visit days observed (across the window).
    biweekly_min_visits: int = 6
    # Minimum number of distinct weeks the parcel was visited on the best DOW.
    # Same floor + ratio pattern as weekly_min_weeks.
    biweekly_min_weeks: int = 6
    biweekly_min_weeks_ratio: float = 0.25
    # Phase share: weeks land in even-or-odd phase relative to an anchor week.
    # 0.72 = at least 72% of weeks share the same phase (clean biweekly).
    biweekly_min_phase_share: float = 0.72
    # Final composite score must clear this to count as biweekly.
    biweekly_min_score: float = 0.58
    # Cap on weekly regularity — if a parcel hits ≥80% of weeks, it's weekly,
    # not biweekly, regardless of phase.
    biweekly_max_weekly_regularity: float = 0.80

    # ------------------------------------------------------------------
    # Composite biweekly score weights. They sum to 1.0 by convention.
    #   score = w_phase * phase_share
    #         + w_pct14 * pct_within_target
    #         + w_reg   * (1 - |reg - 0.5| / 0.5)    # rewards reg near 0.5
    # ------------------------------------------------------------------
    biweekly_score_weight_phase: float = 0.45
    biweekly_score_weight_pct14: float = 0.35
    biweekly_score_weight_regularity_centeredness: float = 0.20


# A reasonable default callers can reach for without constructing one.
DEFAULT_CONFIG = PatternConfig()
