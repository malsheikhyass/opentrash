"""Tests for opentrash.adapters.gps.base — the GPSAdapter Protocol.

The Protocol itself has no runtime behavior; what we verify is:

1. The canonical schema constants are present and consistent.
2. A class with a matching ``fetch`` is recognized as a ``GPSAdapter``
   (structural typing in action).
3. A class without one is not.
"""

from datetime import date

import pandas as pd

from opentrash.adapters.gps.base import GPS_COLUMNS, GPS_SCHEMA, GPSAdapter


# ----- schema constants -----
def test_schema_has_expected_columns():
    assert GPS_COLUMNS == (
        "vehicle_id", "dt_utc", "dt_local", "lat", "lon", "speed_mph",
    )
    # GPS_COLUMNS is derived from GPS_SCHEMA — keep them in sync.
    assert GPS_COLUMNS == tuple(name for name, _ in GPS_SCHEMA)


def test_schema_dtypes_make_sense():
    dtypes = dict(GPS_SCHEMA)
    assert "UTC" in dtypes["dt_utc"]
    assert "Pacific" in dtypes["dt_local"]
    assert dtypes["lat"] == "float64"
    assert dtypes["lon"] == "float64"


# ----- structural typing: implements / doesn't implement the Protocol -----
class _FakeAdapter:
    """A trivial adapter that satisfies the Protocol structurally."""

    def fetch(self, vehicle, start_date, end_date):
        return pd.DataFrame(columns=list(GPS_COLUMNS))


class _NotAnAdapter:
    """No ``fetch`` method — should NOT pass isinstance(.., GPSAdapter)."""

    def something_else(self):
        pass


def test_fake_adapter_satisfies_protocol():
    fake = _FakeAdapter()
    # runtime_checkable Protocol enables isinstance().
    assert isinstance(fake, GPSAdapter)


def test_non_adapter_does_not_satisfy_protocol():
    assert not isinstance(_NotAnAdapter(), GPSAdapter)


def test_fake_adapter_returns_correct_columns():
    fake = _FakeAdapter()
    df = fake.fetch("100001", date(2026, 5, 1), date(2026, 5, 1))
    assert tuple(df.columns) == GPS_COLUMNS
