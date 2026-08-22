# Changelog

All notable changes to `opentrash` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from 1.0.0 onwards. Pre-1.0 releases may include breaking changes between
minor versions; these are called out explicitly when they occur.

## [Unreleased]

## [0.2.1] — 2026-08-21

### Changed (breaking)

- Renamed route-layer API: `htc_path`/`htc_route_col`/`htc_buffer_ft` are now
  `manual_path`/`manual_route_col`/`manual_buffer_ft`; the `"HTC"` kind label
  is now `"MANUAL"`.
- Renamed tonnage source identifiers to the generic example names `SCALE` and
  `FIELD`: `clean_scale_file`/`clean_field_file`, `add_keys_scale`/
  `add_keys_field`, `matl_norm_scale`/`matl_norm_field`, and the
  `scale_note`/`field_note` render arguments.
- Renamed `SD_BBOX`/`in_sd_bbox` to `REGION_BBOX`/`in_region_bbox`.
- Default route-ID column is now `ROUTE_ID`.
- All example identifiers (fleet prefixes, vehicle IDs, route IDs, zone
  ranges, depot name) are now clearly-labeled placeholder values; remap the
  module-level constants and config dataclasses to your agency's schema.

### Removed

- Agency-specific defaults and references removed from docstrings, examples,
  and configuration.

## [0.1.1] — 2026-06-30

### Added

- Added a lightweight `opentrash` command-line interface.
- Added `opentrash --help`, `opentrash --version`, `opentrash modules`, and
  `opentrash doctor`.
- Added `python -m opentrash` support.

### Changed

- Updated public documentation to describe the current alpha CLI and
  module-level API accurately.
- Removed outdated quickstart examples that referenced workflow wrappers not
  yet exposed by the public API.
- Removed public GitHub metadata links while the source repository remains
  private during alpha hardening.
- Added source repository notes explaining that the public repository will open
  after the API, documentation, sample data, and contributor workflow stabilize.
- Added `pytz` to the development extra because the Geotab adapter tests require
  timezone fixtures.

## [0.1.0] — 2026-06-07

First public release. The package is feature-complete across the
12-lesson course series and stable enough for real-world use, though
the API may evolve before 1.0.0.

### Added

- **`opentrash.core`** — CRS conversions (EPSG:2230 working / EPSG:4326 web),
  shared DuckDB session helper, vehicle-ID parsing.
- **`opentrash.adapters.gps`** — pluggable GPS adapters: Geotab (`mygeotab`)
  and PostgreSQL/PostGIS, both with read-only API and credentials via
  environment variables.
- **`opentrash.cache`** — date-partitioned GPS ping cache, secondary
  indexes, master index with refresh-on-demand semantics.
- **`opentrash.prep`** — static layer ingest: sites (with parcel
  spatial-join), parcels, parcels-with-WKB, routes, facilities.
- **`opentrash.tonnage`** — tonnage ingest pipeline with hash-based dedup,
  cleaners for FIELD and SCALE source formats, idempotent year-partitioned
  upsert.
- **`opentrash.engine`** — the routing engine: `enrich_pings()` performs
  one DuckDB spatial-join pass over routes, parcels, and facilities,
  producing the canonical enriched-ping stream; `segments` builds the
  load-organized workday timeline with cumulative haversine mileage and
  choreography-violation flags.
- **`opentrash.patterns`** — route-agnostic per-parcel service-signature
  detection (weekly1, weekly2, biweekly) via a chunked DuckDB CTAS
  pipeline. Idempotent on-disk writes; skip-if-fresh runner.
- **`opentrash.routeview`** — interactive single-route, single-day,
  single-vehicle MapLibre HTML rendering. Trail as colored GPS dots
  (granularity preserved), parcels colored served / missed / unknown,
  patterns expectation overlaid on the parcel popups, per-load tonnage
  via vehicle + time-window matching.

### Course material

- 12-lesson course from "notebook to publishable package" available at
  https://airesearchcorps.org/opentrash/.