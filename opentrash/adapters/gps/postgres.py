"""Postgres GPS adapter.

Implements :class:`opentrash.adapters.gps.base.GPSAdapter` against a Postgres
database where GPS pings stream in continuously (typically every 10 seconds
from a Geotab/MyGeotab streaming integration). This is the **production** GPS
path for opentrash: the database is always current, so reading from it is
cheap and incremental — unlike the Geotab API which is rate-limited and slow.

Schema this adapter expects (PostgreSQL/PostGIS):

- ``log_records`` with columns ``DeviceId``, ``DateTime`` (naive UTC),
  ``Latitude``, ``Longitude``, ``Speed`` (km/h).
- ``devices`` with columns ``id`` and ``Name`` (vehicle name).

If your schema differs, override the queries via the constructor or subclass.

As with Geotab, credentials are arguments (or environment variables), **never
hardcoded**. The ``psycopg2`` / ``sqlalchemy`` dependencies are imported lazily
so the package doesn't hard-require them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from .base import GPS_COLUMNS

if TYPE_CHECKING:
    import pandas as pd

_LOCAL_TZ = "US/Pacific"
_DEFAULT_CHUNKSIZE = 250_000

# Default queries; override on the adapter instance if your schema differs.
_DEFAULT_DEVICES_SQL = """
    SELECT "id" AS "DeviceId", "Name" AS "VehicleName"
    FROM public."devices"
    WHERE "Name" IS NOT NULL AND LENGTH(TRIM("Name")) > 0
"""

_DEFAULT_PINGS_SQL = """
    SELECT
      lr."DeviceId",
      lr."DateTime"  AS "DateTimeUTC_naive",
      lr."Latitude",
      lr."Longitude",
      lr."Speed"
    FROM public."log_records" lr
    WHERE lr."DateTime" >= :from_utc
      AND lr."DateTime" <  :to_utc
"""


def _as_date(d: date | str | datetime) -> date:
    """Coerce a date / datetime / 'YYYY-MM-DD' string to a ``date``."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        return datetime.strptime(d.strip(), "%Y-%m-%d").date()
    raise TypeError(f"Unsupported date type: {type(d)!r}")


@dataclass
class PostgresAdapter:
    """A :class:`GPSAdapter` backed by a streaming Postgres GPS database.

    Parameters
    ----------
    url:
        SQLAlchemy database URL (``postgresql+psycopg2://user:pw@host:port/db``).
        If omitted, read from ``OPENTRASH_PG_URL`` in the environment.
    chunksize:
        Server-side fetch size for streaming results out of Postgres. The
        default (250k rows) is comfortable for a year of pings; tune for your
        memory.
    devices_sql, pings_sql:
        Override the default queries if your schema differs.
    """

    url: str | None = None
    chunksize: int = _DEFAULT_CHUNKSIZE
    devices_sql: str = _DEFAULT_DEVICES_SQL
    pings_sql: str = _DEFAULT_PINGS_SQL

    def __post_init__(self) -> None:
        self.url = self.url or os.environ.get("OPENTRASH_PG_URL")
        self._engine = None
        self._id_to_name: dict[int, str] | None = None

    def _require_url(self) -> None:
        if not self.url:
            raise ValueError(
                "Missing Postgres URL. Pass url='postgresql+psycopg2://...' to "
                "PostgresAdapter(...) or set OPENTRASH_PG_URL in the environment."
            )

    def _get_engine(self):
        """Lazily build the SQLAlchemy engine."""
        if self._engine is None:
            self._require_url()
            from sqlalchemy import create_engine

            self._engine = create_engine(self.url, pool_pre_ping=True, future=True)
        return self._engine

    def _device_lookup(self) -> dict[int, str]:
        """Cached ``DeviceId -> VehicleName`` map. Loaded once per adapter."""
        if self._id_to_name is None:
            import pandas as pd

            devices = pd.read_sql_query(self.devices_sql, self._get_engine())
            devices["VehicleName"] = devices["VehicleName"].astype(str).str.strip()
            devices = devices.drop_duplicates(subset=["DeviceId"]).reset_index(drop=True)
            self._id_to_name = dict(
                zip(devices["DeviceId"].astype(int), devices["VehicleName"].astype(str), strict=True)
            )
        return self._id_to_name

    def fetch(
        self,
        vehicle: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """Fetch GPS pings for one vehicle over an inclusive local-day range.

        Returns a DataFrame in the canonical schema (see
        :data:`opentrash.adapters.gps.base.GPS_SCHEMA`), sorted by ``dt_utc``.
        Returns an empty DataFrame (with correct columns) if no pings exist.
        """
        self._require_url()

        import pandas as pd
        import pytz
        from sqlalchemy import text

        # Resolve the vehicle name -> DeviceId via the device lookup.
        name_to_id = {name: did for did, name in self._device_lookup().items()}
        vehicle_name = str(vehicle).strip()
        device_id = name_to_id.get(vehicle_name)
        if device_id is None:
            raise ValueError(f"Vehicle name {vehicle_name!r} not found in devices")

        # Local-day window -> UTC window (naive, as stored in log_records).
        # The window is half-open [from_local, start_of_next_day) so an inclusive
        # end_date covers its full local day.
        from datetime import timedelta

        pst = pytz.timezone(_LOCAL_TZ)
        start_d = _as_date(start_date)
        end_d = _as_date(end_date)
        from_local = pst.localize(datetime.combine(start_d, time(0, 0, 0)))
        to_local = pst.localize(datetime.combine(end_d + timedelta(days=1), time(0, 0, 0)))

        from_utc_naive = from_local.astimezone(pytz.utc).replace(tzinfo=None)
        to_utc_naive = to_local.astimezone(pytz.utc).replace(tzinfo=None)

        # Stream the result via chunked fetch — keeps memory flat on big windows.
        engine = self._get_engine()
        stmt = text(self.pings_sql + ' AND lr."DeviceId" = :device_id ORDER BY lr."DateTime"')
        chunks = []
        with engine.connect() as con:
            result = con.execution_options(stream_results=True).execute(
                stmt,
                {"from_utc": from_utc_naive, "to_utc": to_utc_naive, "device_id": device_id},
            )
            while True:
                rows = result.fetchmany(self.chunksize)
                if not rows:
                    break
                chunks.append(pd.DataFrame(rows, columns=result.keys()))

        if not chunks:
            return _empty_gps_frame()

        df = pd.concat(chunks, ignore_index=True)
        return self._normalize(df, vehicle_name)

    @staticmethod
    def _normalize(df: pd.DataFrame, vehicle_name: str) -> pd.DataFrame:
        """Turn a raw Postgres result into the canonical GPS schema.

        Split out from :meth:`fetch` so unit tests can exercise it without a
        live database connection.
        """
        import pandas as pd

        if df.empty:
            return _empty_gps_frame()

        out = pd.DataFrame()
        out["vehicle_id"] = pd.Series([vehicle_name] * len(df), dtype="string")
        out["dt_utc"] = pd.to_datetime(df["DateTimeUTC_naive"], utc=True, errors="coerce")
        out["dt_local"] = out["dt_utc"].dt.tz_convert(_LOCAL_TZ)
        out["lat"] = pd.to_numeric(df["Latitude"], errors="coerce")
        out["lon"] = pd.to_numeric(df["Longitude"], errors="coerce")
        # Speed in DB is km/h; convert to mph for the canonical schema.
        out["speed_mph"] = (pd.to_numeric(df["Speed"], errors="coerce") * 0.621371).round()

        out = out.dropna(subset=["dt_utc"]).sort_values("dt_utc").reset_index(drop=True)
        return out[list(GPS_COLUMNS)]


def _empty_gps_frame() -> pd.DataFrame:
    """An empty DataFrame with the canonical GPS columns."""
    import pandas as pd

    return pd.DataFrame(columns=list(GPS_COLUMNS))
