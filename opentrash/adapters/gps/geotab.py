"""Geotab GPS adapter.

Implements :class:`opentrash.adapters.gps.base.GPSAdapter` against Geotab's
``LogRecord`` API. Given a vehicle name and a local-day range, it returns GPS
pings in the canonical schema — it does **not** write files (caching is the
cache layer's job; see :mod:`opentrash.cache.gps_cache`).

Credentials are passed in (constructor args), with environment-variable
fallbacks. They are **never hardcoded** — a credential in source is a credential
leaked. The ``mygeotab`` dependency is imported lazily inside ``fetch`` so the
rest of the package doesn't require it to be installed.

The time handling mirrors the operational reality: the caller thinks in local
local-timezone calendar days, but Geotab wants a UTC instant window. We convert
local-day bounds to UTC for the query, then filter the results back to the
local day as a safety net (a UTC window spans two local dates at the edges).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from .base import GPS_COLUMNS

if TYPE_CHECKING:
    import pandas as pd

_DEFAULT_SERVER = "my.geotab.com"
_LOCAL_TZ = "US/Pacific"


def _as_date(d: date | str | datetime) -> date:
    """Coerce a date / datetime / 'YYYY-MM-DD' string to a ``date``."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        return datetime.strptime(d.strip(), "%Y-%m-%d").date()
    raise TypeError(f"Unsupported date type: {type(d)!r}")


def _kmh_to_mph(kmh: float) -> float:
    """Convert km/h to mph (Geotab reports speed in km/h)."""
    return round(float(kmh or 0) * 0.621371)


@dataclass
class GeotabAdapter:
    """A :class:`GPSAdapter` backed by the Geotab ``LogRecord`` API.

    Parameters
    ----------
    username, password, database:
        Geotab credentials. If omitted, read from the environment
        (``GEOTAB_USERNAME``, ``GEOTAB_PASSWORD``, ``GEOTAB_DATABASE``).
    server:
        Geotab server host. Defaults to ``my.geotab.com``.

    Example
    -------
    >>> adapter = GeotabAdapter(username="me", password="...", database="my_fleet_db")  # doctest: +SKIP
    >>> df = adapter.fetch("100421", "2025-08-18", "2025-08-18")                     # doctest: +SKIP
    """

    username: str | None = None
    password: str | None = None
    database: str | None = None
    server: str = _DEFAULT_SERVER

    def __post_init__(self) -> None:
        # Fall back to environment variables for any credential not passed in.
        self.username = self.username or os.environ.get("GEOTAB_USERNAME")
        self.password = self.password or os.environ.get("GEOTAB_PASSWORD")
        self.database = self.database or os.environ.get("GEOTAB_DATABASE")

    def _require_credentials(self) -> None:
        missing = [
            name
            for name, val in (
                ("username", self.username),
                ("password", self.password),
                ("database", self.database),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                "Missing Geotab credentials: "
                + ", ".join(missing)
                + ". Pass them to GeotabAdapter(...) or set GEOTAB_USERNAME / "
                "GEOTAB_PASSWORD / GEOTAB_DATABASE in the environment."
            )

    def fetch(
        self,
        vehicle: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """Fetch GPS pings for one vehicle over an inclusive local-day range.

        Returns a DataFrame in the canonical schema (see
        :data:`opentrash.adapters.gps.base.GPS_SCHEMA`), sorted by ``dt_utc``.
        An empty result is an empty DataFrame with the correct columns.
        """
        # Validate credentials first — a clear error before we even try to
        # import the optional vendor SDK.
        self._require_credentials()

        import mygeotab  # lazy: only needed when actually fetching
        import pytz

        pst = pytz.timezone(_LOCAL_TZ)
        utc = pytz.utc

        start_d = _as_date(start_date)
        end_d = _as_date(end_date)

        # Local-day bounds (PT), then convert to a UTC instant window.
        from_local = pst.localize(datetime.combine(start_d, time(0, 0, 0)))
        to_local = pst.localize(datetime.combine(end_d, time(23, 59, 59)))
        from_utc = from_local.astimezone(utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_utc = to_local.astimezone(utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        api = mygeotab.API(
            username=self.username,
            password=self.password,
            database=self.database,
            server=self.server,
        )
        api.authenticate()

        vehicle_name = str(vehicle).strip()
        devices = api.call("Get", typeName="Device", search={"name": vehicle_name})
        if not devices:
            raise ValueError(f"No Geotab device found for name={vehicle_name!r}")

        device_ids = [d["id"] for d in devices]
        calls = [
            ["Get", dict(
                typeName="LogRecord",
                search={
                    "fromDate": from_utc,
                    "toDate": to_utc,
                    "deviceSearch": {"id": dev_id},
                },
            )]
            for dev_id in device_ids
        ]
        results = api.multi_call(calls)

        rows = []
        for recs in results:
            if not recs:
                continue
            for r in recs:
                rows.append({
                    "vehicle_id": vehicle_name,
                    "dt_raw": r.get("dateTime"),
                    "lat": r.get("latitude"),
                    "lon": r.get("longitude"),
                    "speed_mph": _kmh_to_mph(r.get("speed", 0)),
                })

        return self._normalize_rows(rows, from_local, to_local)

    @staticmethod
    def _normalize_rows(rows: list[dict], from_local, to_local) -> pd.DataFrame:
        """Turn raw ping dicts into the canonical schema, filtered to the local day.

        Split out from :meth:`fetch` so it can be unit-tested without a live API.
        """
        import pandas as pd

        if not rows:
            return _empty_gps_frame()

        df = pd.DataFrame(rows)
        df["dt_utc"] = pd.to_datetime(df["dt_raw"], utc=True, errors="coerce")
        df = df.dropna(subset=["dt_utc"])
        df["dt_local"] = df["dt_utc"].dt.tz_convert(_LOCAL_TZ)

        # Safety filter: keep only pings within the requested local-day window
        # (the UTC query window spans two local dates at the edges).
        in_window = (df["dt_local"] >= from_local) & (df["dt_local"] <= to_local)
        df = df[in_window].copy()
        if df.empty:
            return _empty_gps_frame()

        df["vehicle_id"] = df["vehicle_id"].astype("string")
        df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
        df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
        df["speed_mph"] = pd.to_numeric(df["speed_mph"], errors="coerce")

        df = df[list(GPS_COLUMNS)].sort_values("dt_utc").reset_index(drop=True)
        return df


def _empty_gps_frame() -> pd.DataFrame:
    """An empty DataFrame with the canonical GPS columns."""
    import pandas as pd

    return pd.DataFrame(columns=list(GPS_COLUMNS))
