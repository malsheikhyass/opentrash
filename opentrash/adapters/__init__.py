"""adapters — vendor-specific data sources behind small Protocols.

Implemented:

- ``gps.base``      — the ``GPSAdapter`` Protocol + canonical output schema.
- ``gps.geotab``    — Geotab LogRecord API adapter (credentials via args/env).
- ``gps.postgres``  — Streaming Postgres adapter (production data source).
"""
