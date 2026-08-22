"""Pattern detection — validation and diagnostics.

After :func:`opentrash.patterns.detector.detect_patterns` writes a patterns
parquet, this module answers the obvious questions: how many parcels did we
find a pattern for? How clean are the biweekly detections? Are weekly1 and
weekly2 picking the same vehicle (a bug — they should be ranked competitors)?

Returns a single :class:`ValidationReport` dataclass with everything in one
place. Designed for human inspection (just print it) or for piping into a
small markdown report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


@dataclass
class ValidationReport:
    """A small bag of summary stats from a patterns parquet."""

    total: int
    weekly1_count: int
    weekly2_count: int
    biweekly_count: int
    all_three_count: int
    weekly1_eq_weekly2_count: int

    # Optional biweekly diagnostics (None if no biweekly rows).
    biweekly_score_describe: dict | None = field(default=None)
    biweekly_phase_share_describe: dict | None = field(default=None)

    def coverage(self, kind: str) -> float:
        """Return coverage for ``weekly1``, ``weekly2``, ``biweekly``, or ``all_three`` as a fraction."""
        if self.total == 0:
            return 0.0
        counts = {
            "weekly1": self.weekly1_count,
            "weekly2": self.weekly2_count,
            "biweekly": self.biweekly_count,
            "all_three": self.all_three_count,
        }
        if kind not in counts:
            raise ValueError(f"Unknown coverage kind: {kind!r}. Expected one of {list(counts)}.")
        return counts[kind] / self.total

    def summary(self) -> str:
        """Human-readable multi-line summary."""
        lines = [
            f"Total parcels: {self.total:,}",
            f"  Weekly1:    {self.weekly1_count:,} ({self.coverage('weekly1')*100:.1f}%)",
            f"  Weekly2:    {self.weekly2_count:,} ({self.coverage('weekly2')*100:.1f}%)",
            f"  Biweekly:   {self.biweekly_count:,} ({self.coverage('biweekly')*100:.1f}%)",
            f"  All three:  {self.all_three_count:,} ({self.coverage('all_three')*100:.1f}%)",
            f"Weekly1 == Weekly2 vehicle (sanity check): {self.weekly1_eq_weekly2_count:,}",
        ]
        return "\n".join(lines)


def validate_patterns(patterns: pd.DataFrame | str | Path) -> ValidationReport:
    """Compute a :class:`ValidationReport` from a patterns DataFrame or parquet path.

    The diagnostics mirror cell 4 of ``parcels_JB.ipynb``: coverage of each
    pattern type, all-three coverage, weekly1==weekly2 sanity, and
    ``describe()`` stats for the biweekly score and phase share when present.
    """
    import pandas as pd

    if isinstance(patterns, (str, Path)):
        df = pd.read_parquet(patterns)
    else:
        df = patterns

    total = len(df)
    has_w1 = df["weekly1_vehicle"].notna()
    has_w2 = df["weekly2_vehicle"].notna()
    has_bw = df["biweekly_vehicle"].notna()

    report = ValidationReport(
        total=total,
        weekly1_count=int(has_w1.sum()),
        weekly2_count=int(has_w2.sum()),
        biweekly_count=int(has_bw.sum()),
        all_three_count=int((has_w1 & has_w2 & has_bw).sum()),
        weekly1_eq_weekly2_count=int(
            (has_w1 & has_w2 & (df["weekly1_vehicle"] == df["weekly2_vehicle"])).sum()
        ),
    )

    if has_bw.any():
        # Convert describe()'s Series-of-floats to a plain dict so the
        # report is JSON-serializable for logging.
        for col, attr in (
            ("biweekly_score", "biweekly_score_describe"),
            ("biweekly_phase_share", "biweekly_phase_share_describe"),
        ):
            if col in df.columns:
                d = df.loc[has_bw, col].describe().to_dict()
                setattr(report, attr, {k: float(v) for k, v in d.items()})

    return report
