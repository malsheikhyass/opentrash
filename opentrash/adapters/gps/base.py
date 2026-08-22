"""GPS adapter Protocol — the interface every GPS provider implements.

A real package can't hardcode one GPS vendor. Today it's Geotab; tomorrow it
might be Samsara, Verizon Connect, or a CSV dump from a partner agency. This
module defines the **shape** every GPS adapter must satisfy — a
:class:`typing.Protocol` — so the rest of the package can consume GPS data
without knowing who produced it.

A concrete adapter (e.g. ``adapters.gps.geotab.GeotabAdapter``) implements
:meth:`GPSAdapter.fetch` and returns a DataFrame in the canonical schema
documented below. Authentication, vendor quirks, time-zone conversions, unit
conversions — all hidden behind the Protocol.

This is the file you copy from when adding support for a new GPS vendor.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import pandas as pd


# ---------------------------------------------------------------------------
# Canonical output schema.
# ---------------------------------------------------------------------------
# Every adapter returns these columns, in this order, with these dtypes.
# Downstream code (routeview, patterns) depends on this shape.
GPS_SCHEMA: tuple[tuple[str, str], ...] = (
    ("vehicle_id", "string"),                  # the raw vehicle identifier passed in
    ("dt_utc",     "datetime64[ns, UTC]"),     # event time in UTC (always timezone-aware)
    ("dt_local",   "datetime64[ns, US/Pacific]"),  # same instant, local time
    ("lat",        "float64"),                 # WGS84 latitude  (EPSG:4326)
    ("lon",        "float64"),                 # WGS84 longitude (EPSG:4326)
    ("speed_mph",  "float64"),                 # ground speed in miles per hour
)

GPS_COLUMNS: tuple[str, ...] = tuple(c for c, _ in GPS_SCHEMA)


@runtime_checkable
class GPSAdapter(Protocol):
    """The interface every GPS provider adapter must satisfy.

    Implementations don't subclass this — Python's structural typing means any
    class with a compatible :meth:`fetch` *is* a ``GPSAdapter``. The
    ``@runtime_checkable`` decorator just enables ``isinstance(x, GPSAdapter)``
    as a sanity check.

    Implementing adapters live in sibling modules:

    - ``adapters.gps.geotab.GeotabAdapter`` — pulls from Geotab's LogRecord API
      (added in the GPS lesson).
    - Future vendors: Samsara, Verizon Connect, etc. — one module each.
    """

    def fetch(
        self,
        vehicle: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """Fetch GPS pings for one vehicle over a local-day range.

        Parameters
        ----------
        vehicle:
            The vehicle identifier as the vendor knows it (Geotab device name,
            Samsara vehicle name, etc.). The adapter is responsible for
            resolving this into whatever internal handle the API needs.
        start_date, end_date:
            **Local calendar dates** (PT in this operation) — inclusive on
            both ends. The adapter handles the conversion to whatever the
            vendor's API actually wants (typically UTC instants).

        Returns
        -------
        A DataFrame with the columns defined by :data:`GPS_SCHEMA`, sorted by
        ``dt_utc`` ascending. Empty result is an empty DataFrame with the
        correct columns — **never** ``None``.
        """
        ...
