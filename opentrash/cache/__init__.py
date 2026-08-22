"""cache — the substrate: GPS cache + spatial-temporal indexes.

The streaming GPS pipeline lands here. Every other downstream module
(engine, patterns, routeview) reads from this cache; none of them
re-fetch from vendors.

Implemented:

- ``gps_cache``    — one parquet per vehicle per local day (``YYYY-MM-DD/``).
- ``gps_indexes``  — per-day vehicle-day summaries (small lookup tables).
- ``master_index`` — STAC-like cross-join: vehicle-day bbox × route bbox.
"""
