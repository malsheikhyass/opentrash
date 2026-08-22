# opentrash

**A geospatial Python package for residential waste collection analytics.**

`opentrash` turns vehicle GPS telematics, parcel polygons, route polygons, and
facility locations into operational intelligence: per-parcel service signatures
that span months or years, and self-contained interactive maps of a single
route on a single day. It runs on a laptop, scales to fleet-wide datasets via
DuckDB, and produces outputs that anyone with a web browser can open.

The package is designed for waste-collection agencies, civic technologists,
operations researchers, and anyone building tools on top of vehicle-fleet
data. It is open source under the Apache License 2.0, has no commercial
dependencies, and is not locked to any vendor's data format.

[Install](#install) · [Quickstart](#quickstart) · [Features](#features) · [Roadmap](roadmap.md)

---

## Install

```bash
pip install opentrash
```

Optional extras for specific data sources:

```bash
pip install "opentrash[geotab]"     # Geotab GPS adapter
pip install "opentrash[postgres]"   # PostgreSQL/PostGIS GPS adapter
pip install "opentrash[dev]"        # pytest and ruff for development
```

Requires Python 3.11 or newer. Tested on Linux and macOS; Windows support is
available but less exercised — please report issues.

---

## Quickstart

After installation, verify that the package and command-line entry point are available:

```bash
opentrash --help
opentrash --version
opentrash doctor
```

The current CLI is intentionally lightweight. It provides package discovery,
version reporting, and environment checks while the higher-level workflow
commands are being developed.

```bash
opentrash modules   # list core package modules
opentrash doctor    # check core imports and optional dependency groups
```

For Python users, `opentrash` currently exposes module-level building blocks
for GPS adapters, cache/index management, static-layer preparation, routing
engine enrichment, pattern detection, route rendering, and tonnage ingest.

Example imports:

```python
from opentrash.core.vehicle_ids import parse_vehicle_id
from opentrash.engine.enrichment import enrich_pings
from opentrash.patterns.runner import run_patterns
from opentrash.routeview.runner import render_routeview
from opentrash.tonnage.pipeline import run_ingest
```

The package is in alpha. APIs are usable, but some workflow-level wrappers and
sample-data tutorials are still being built. For a longer walkthrough of the
current package layout and expected data flow, see [Getting started](getting-started.md).

---

## Features

### The routing engine

A single, disciplined spatial-join layer (`opentrash.engine`) joins every GPS
ping against every operational GIS layer — routes, parcels, facilities —
exactly once. Every downstream product reads enriched pings and applies
its own arithmetic. The principle is *spatial joins are infrastructure;
products are calculations*.

### Patterns

Per-parcel service-signature detection (`opentrash.patterns`) processes
months or years of GPS history to surface the typical service for each
parcel: weekly, biweekly, day-of-week, hour-of-day, and which vehicle.
Outputs are partitioned parquet files, idempotent, and skip-if-fresh.

### RouteView

Single-route, single-day, single-vehicle interactive HTML maps
(`opentrash.routeview`). The truck's GPS trail renders as colored dots
(granularity preserved, not collapsed into polylines), parcels color by
served / missed / unknown, patterns expectations appear in parcel popups,
and per-load tonnage is matched by vehicle and time window.

### GPS adapters

Pluggable adapters (`opentrash.adapters.gps`) for ingesting GPS data from
multiple sources. Ships with adapters for the Geotab telematics API and
PostgreSQL/PostGIS. Each adapter implements a small read-only protocol,
so adding a new source is straightforward.

### Tonnage ingest

A hash-based, idempotent pipeline (`opentrash.tonnage`) for landfill weight
records, with cleaners for the FIELD and SCALE source formats. Year-partitioned
on disk, designed for incremental updates.

### Static layer preparation

One-time data preparation tools (`opentrash.prep`) for parcels, routes,
facilities, and service-site polygons. Geometries are stored as
WKB-backed parquet for fast load and spatial-join performance.

---

## How it's built

`opentrash` uses [DuckDB](https://duckdb.org/) for set-based aggregations
(chunked and idempotent for large datasets), [GeoPandas](https://geopandas.org/) +
[Shapely](https://shapely.readthedocs.io/) for in-memory geometry,
[PyProj](https://pyproj4.github.io/pyproj/) for projections, and
[MapLibre GL JS](https://maplibre.org/) for rendered HTML maps. No commercial
dependencies; no tile-provider lock-in. The working CRS is EPSG:2230 (CA
State Plane Zone 6, US feet) by default; the web CRS is EPSG:4326.

See [Architecture](architecture.md) for the full structural overview.

---

## License and citation

`opentrash` is licensed under the [Apache License 2.0](https://github.com/malsheikhyass/opentrash/blob/main/LICENSE).
You may use, modify, and redistribute it for any purpose, including commercial
use, provided you retain the license and copyright notices.

For academic work, please cite the package using the metadata in
[CITATION.cff](https://github.com/malsheikhyass/opentrash/blob/main/CITATION.cff).