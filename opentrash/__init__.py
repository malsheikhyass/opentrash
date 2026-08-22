"""opentrash — open-source cloud-native geospatial Python package for residential waste ops.

The folder shape laid out here matches the destination architecture:

- ``adapters/`` — vendor-specific data sources (GPS, future others).
- ``core/``     — foundational primitives (CRS, DuckDB session, vehicle IDs).
- ``prep/``     — one-time data preparation (parcels, sites, static layers).
- ``cache/``    — the substrate: GPS cache + indexes.
- ``tonnage/``  — Excel-to-parquet pipeline + lookup.
- ``engine/``   — THE ROUTING ENGINE: ping ↔ all GIS layers enrichment + segments.
- ``patterns/`` — pattern detection (consumes engine output).
- ``routeview/``— the single-route, single-day map (consumes engine output).

Each lesson lights up modules under one of these directories. Lesson 0 lands
the first one: ``prep.sites``.
"""

__version__ = "0.2.0"
