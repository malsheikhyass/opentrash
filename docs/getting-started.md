# Getting started

This page walks through installing `opentrash` and using the current alpha API
on your own data. For a high-level package overview, see [Home](index.md);
for the structural design, see [Architecture](architecture.md).

## Install

```bash
pip install opentrash
```

Requires Python 3.11 or newer. The base install brings in `pandas`,
`pyarrow`, `duckdb`, `geopandas`, `shapely`, `pyproj`, and `openpyxl`.

### Optional extras

```bash
pip install "opentrash[geotab]"     # Geotab GPS adapter
pip install "opentrash[postgres]"   # PostgreSQL/PostGIS GPS adapter
pip install "opentrash[dev]"        # pytest, ruff for development
```

Each extra is opt-in: install only the GPS adapters you need.

### From source

The GitHub repository is currently private while the package is in alpha
hardening. Public source access is planned after the API, documentation, sample
data, and contributor workflow stabilize.

For development from an authorized checkout:

```bash
cd opentrash
pip install -e ".[dev,geotab,postgres]"
pytest -q
```

## Verify the installation

Start by checking that the command-line entry point is available:

```bash
opentrash --help
opentrash --version
opentrash doctor
```

You can also run the package as a module:

```bash
python -m opentrash
```

The current CLI is intentionally lightweight. It supports version reporting,
module discovery, and environment checks. Higher-level workflow commands are
planned for future releases.

```bash
opentrash modules
opentrash doctor
opentrash doctor --strict
```

`opentrash doctor` checks core package imports and reports whether optional
dependency groups such as Geotab and PostgreSQL support are available.

## Prepare your inputs

`opentrash` works with four kinds of input data:

| Layer | Format | Purpose |
|---|---|---|
| Parcel polygons | parquet (WKB) | Service-point lookup; the unit of analysis for patterns |
| Route polygons | parquet (WKB) | Which route each GPS ping belongs to |
| Facility points or polygons | parquet (WKB) | Distinguishes landfill dwell from depot dwell |
| GPS pings | parquet | Vehicle telematics, date-partitioned in a cache |

The `opentrash.prep` module includes helpers for one-time preparation of
parcels, routes, and facilities from common source formats such as shapefile,
GeoJSON, and GeoPackage. The `opentrash.cache` module manages a
date-partitioned GPS cache populated by the GPS adapters.

The working coordinate reference system is EPSG:2230 (California State Plane
Zone 6, US feet) by default. The web CRS for rendered HTML is EPSG:4326. The
CRS is configurable through `opentrash.core.crs` if your area of operations
sits in a different state plane zone.

## Use the Python modules

The alpha API is currently organized around module-level building blocks rather
than one-command end-to-end workflows. The typical data flow is:

1. Prepare static GIS layers with `opentrash.prep`.
2. Populate or read GPS cache files with `opentrash.cache` and
   `opentrash.adapters.gps`.
3. Enrich GPS pings with `opentrash.engine.enrichment`.
4. Build workday timelines with `opentrash.engine.segments`.
5. Detect long-window service patterns with `opentrash.patterns`.
6. Render single-route HTML views with `opentrash.routeview`.
7. Ingest landfill tonnage records with `opentrash.tonnage`.

Useful entry points include:

```python
from opentrash.core.vehicle_ids import parse_vehicle_id
from opentrash.prep.sites import load_sites, clean_sites, sites_to_geo
from opentrash.prep.static_layers import load_route_polygons, load_facilities
from opentrash.engine.enrichment import enrich_pings, enrich_vehicle_day
from opentrash.engine.segments import build_timeline, build_all_segments
from opentrash.patterns.runner import run_patterns
from opentrash.routeview.runner import render_routeview
from opentrash.tonnage.pipeline import run_ingest
```

The API is still evolving while the project is in `0.x`. If you are building
against `opentrash` today, prefer pinning the exact version in your environment:

```bash
pip install "opentrash==0.1.1"
```

Sample data, stable workflow wrappers, and fuller command-line pipeline commands
are planned for upcoming releases.

## Next steps

- Read [Architecture](architecture.md) to understand why the package is
  organized the way it is.
- See the [Roadmap](roadmap.md) for what's coming in future releases.
- Sample data and step-by-step tutorials are planned for an upcoming release.