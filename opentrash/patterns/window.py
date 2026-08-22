"""Analysis-window resolution — turn 'past_year' into actual dates.

Pattern detection runs over a specific time window. The detector itself takes
explicit ``start_date`` / ``end_date`` parameters; this module is the small
helper that turns common operational shorthand ('past_year', 'past_quarter')
into the matching date pair.

Keeping the resolution separate from the detector keeps the detector pure
(it just consumes dates) and makes the time-window decision visible at the
call site.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

Period = Literal[
    "past_year", "past_quarter", "past_month",
    "past_week", "past_day", "custom",
]

# How many days each named period covers, anchored at the reference date.
_PERIOD_DAYS: dict[str, int] = {
    "past_year": 365,
    "past_quarter": 90,
    "past_month": 30,
    "past_week": 7,
    "past_day": 1,
}


def compute_window(
    period: Period = "past_year",
    *,
    anchor: date | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> tuple[date, date]:
    """Resolve a named analysis period to ``(start_date, end_date)``.

    Parameters
    ----------
    period:
        One of ``past_year``, ``past_quarter``, ``past_month``, ``past_week``,
        ``past_day``, or ``custom``. For ``custom``, supply explicit
        ``start_date`` and ``end_date``.
    anchor:
        The "now" reference date the past-* periods are anchored to. Defaults
        to today. Useful for reproducible test runs and for analyzing a
        window ending at a specific historical date.
    start_date, end_date:
        Required when ``period="custom"``. Accept either ``date`` objects or
        ISO 8601 date strings (``"YYYY-MM-DD"``).

    Returns
    -------
    A ``(start, end)`` tuple of ``datetime.date`` objects, inclusive on both
    ends. The detector and runner use these as half-open or closed bounds
    depending on context.

    Examples
    --------
    >>> compute_window("past_year", anchor=date(2026, 3, 1))
    (datetime.date(2025, 3, 1), datetime.date(2026, 3, 1))
    >>> compute_window("custom", start_date="2025-01-01", end_date="2025-12-31")
    (datetime.date(2025, 1, 1), datetime.date(2025, 12, 31))
    """
    anchor = anchor or date.today()

    if period == "custom":
        if start_date is None or end_date is None:
            raise ValueError(
                "period='custom' requires both start_date and end_date."
            )
        return _to_date(start_date), _to_date(end_date)

    if period not in _PERIOD_DAYS:
        raise ValueError(
            f"Unknown period {period!r}. Use one of: "
            f"{', '.join(sorted(_PERIOD_DAYS))} or 'custom'."
        )

    days = _PERIOD_DAYS[period]
    return (anchor - timedelta(days=days), anchor)


def window_label(start: date, end: date) -> str:
    """Filesystem-friendly label for a window: ``YYYY-MM-DD_to_YYYY-MM-DD``.

    Used by the runner to name the output directory for a given run.
    """
    return f"{start.isoformat()}_to_{end.isoformat()}"


def _to_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)
