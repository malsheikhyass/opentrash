# opentrash

> A geospatial Python package for residential waste collection analytics.
> Vehicle GPS telematics + parcel polygons + route polygons → per-parcel
> service signatures and self-contained interactive route maps.

[![PyPI](https://img.shields.io/pypi/v/opentrash.svg)](https://pypi.org/project/opentrash/)
[![Python](https://img.shields.io/pypi/pyversions/opentrash.svg)](https://pypi.org/project/opentrash/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)
[![Docs](https://img.shields.io/badge/docs-opentrash.app-2563eb)](https://opentrash.app/)

`opentrash` is for waste-collection agencies, civic technologists, and
operations researchers working with vehicle-fleet data. It ingests GPS
telematics, parcel polygons, route polygons, and facility locations,
performs a single disciplined spatial-join pass — the **routing engine** —
and produces two analytical products on top:

- **Patterns** — per-parcel service signatures (weekly, weekly-double,
  biweekly) derived from months or years of pings. Answers "for this
  parcel, who serves it regularly, on what day, at what hour?"
- **RouteView** — a self-contained interactive HTML map for one route +
  day + vehicle. The truck's trail renders as colored GPS dots, parcels
  color by served / missed / unknown, and patterns expectations overlay
  on today's reality. Drop it on a USB stick, email it, host it static.

The architectural principle is *spatial joins are infrastructure; products
are calculations*. Everything downstream of the engine is pure aggregation
on enriched pings.

## Install

```bash
pip install opentrash
```

Optional extras for specific data sources:

```bash
pip install "opentrash[geotab]"     # Geotab GPS adapter
pip install "opentrash[postgres]"   # PostgreSQL/PostGIS GPS adapter
pip install "opentrash[dev]"        # pytest, ruff, and test dependencies
```

Requires Python 3.11 or newer.

## Quickstart

After installation, verify that the package and command-line entry point are
available:

```bash
opentrash --help
opentrash --version
opentrash doctor
```

You can also run the package as a module:

```bash
python -m opentrash
```

The current CLI is intentionally lightweight. It provides package discovery,
version reporting, and environment checks while higher-level workflow commands
are being developed.

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

The package is in alpha. APIs are usable, but some workflow-level wrappers,
sample-data tutorials, and fuller CLI pipeline commands are still being built.

## Architecture

```text
GPS ingest  →  cache  →  engine (the only spatial-join layer)
                            │
                            ↓ enriched pings
                            │
                ┌───────────┴───────────┐
                ↓                       ↓
        patterns (macro)          segments (timeline)
        per-parcel signatures           │
        across the window              ↓
                                  RouteView (micro)
                                  one (route, day, vehicle)
                                  as interactive HTML
```

Patterns is the **macro** view; RouteView is the **micro** view. Together
they form a validation loop: long-period patterns predict who should show
up; the single-day RouteView shows who actually did.

## Documentation

Full documentation, including a getting-started guide, architecture overview,
and roadmap, lives at [opentrash.app](https://opentrash.app/).

## Source repository

`opentrash` is currently in alpha. The published package artifacts are
available through PyPI, while the GitHub repository remains private during
early API, documentation, and sample-data hardening. The public repository
will be opened after the package interface and contributor workflow stabilize.

## How it's built

`opentrash` uses [DuckDB](https://duckdb.org/) for set-based aggregations
(chunked and idempotent for large datasets),
[GeoPandas](https://geopandas.org/) + [Shapely](https://shapely.readthedocs.io/)
for in-memory geometry, [PyProj](https://pyproj4.github.io/pyproj/) for
projections, and [MapLibre GL JS](https://maplibre.org/) for rendered HTML
maps. No commercial dependencies, no tile-provider lock-in.

## License

Apache License 2.0.

You may use, modify, and redistribute `opentrash` for any purpose,
including commercial use, provided you retain the license and copyright
notices.

## Citation

Citation metadata will be published with the public source repository. Until
then, please cite the PyPI package name, version, and documentation site when
referencing `opentrash` in academic or public-facing work.

## Acknowledgments

Developed under [AI Research Corps (AIRC)](https://airesearchcorps.org/),
a 501(c)(3) nonprofit. A free 12-lesson course covering the package
implementation is available at
[airesearchcorps.org/opentrash](https://airesearchcorps.org/opentrash/).