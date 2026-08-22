"""adapters.gps — GPS vendor adapters.

This subpackage isolates *where GPS data comes from* behind a single Protocol
(:class:`opentrash.adapters.gps.base.GPSAdapter`). Downstream code consumes the
canonical schema; vendor-specific quirks stay in each adapter.

Implemented:

- ``base``      — the ``GPSAdapter`` Protocol + canonical output schema.
- ``geotab``    — Geotab LogRecord API adapter (credentials via args/env).
- ``postgres``  — Streaming Postgres adapter (production data source).
"""
