# Architecture

`opentrash` is organized around one architectural principle: **the routing
engine is foundational; products are thin consumers**.

This page describes the package's structural design and the rationale
behind it. For the user-facing pipeline, see [Getting started](getting-started.md).

## Layers

```text
                        Raw municipal inputs
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
                 parcels     sites      tonnage
                                │           │
                                ▼           ▼
                              prep/      tonnage/
                                │           │
                                ▼           │
              static layers (routes, facilities)
                                │           │
                                ▼           ▼
                           ┌─────────────────────┐
                           │   GPS ping stream   │
                           │  (cache + indexes)  │
                           └──────────┬──────────┘
                                      │
                          ┌───────────▼───────────┐
                          │   The routing engine  │
                          │                       │
                          │   enrichment          │
                          │   segments            │
                          │                       │
                          │   → enriched pings    │
                          └───────────┬───────────┘
                                      │
                  ┌───────────────────┼───────────────────┐
                  ▼                   ▼                   ▼
              patterns/           routeview/          future products
              (calculation)       (calculation +      (calculation)
                                   rendering)
```

## The guiding principle

**Spatial joins are infrastructure. Products are calculations.**

The cache holds GPS pings. The engine joins them to the operational GIS
layers (routes, parcels, facilities) once, producing the canonical enriched
ping stream. Every downstream product reads enriched pings and applies its
own arithmetic — patterns aggregates over long windows, RouteView renders
a single day, future products will do other things — but no product
performs its own spatial join. This keeps the spatial work in one place
where it can be optimized, tested, and reasoned about, and it keeps each
product small enough to understand on its own.

## Modules

### `opentrash.core`

Foundational primitives that every other module depends on: CRS conversion
(working CRS ↔ web CRS), a shared DuckDB session helper with the spatial
extension wired in, and vehicle-ID parsing utilities. No business logic
lives here.

### `opentrash.adapters.gps`

Pluggable GPS adapters that implement a small read-only protocol. Ships
with `geotab` using the `mygeotab` library and `postgres` for PostgreSQL or
PostGIS-backed GPS data. Adding a new source means implementing one class.
Credentials flow through environment variables — never hard-coded, never logged.

### `opentrash.cache`

A date-partitioned GPS cache (`cache/YYYY-MM-DD/<vehicle>.parquet`) plus
secondary indexes for fast lookup. The cache is the substrate the engine
consumes; populating it is a fast, idempotent operation that runs once
per day per fleet.

### `opentrash.prep`

One-time preparation of static GIS layers: parcels, sites, routes, and
facilities. Output is a set of WKB-backed parquet files optimized for the
engine's join workload.

### `opentrash.tonnage`

A hash-based, idempotent ingest pipeline for landfill weight records.
Includes cleaners for the FIELD and SCALE source formats. Year-partitioned
on disk; designed for incremental updates without ever double-counting.

### `opentrash.engine`

The routing engine. `enrichment.py` performs the unified spatial-join pass
over routes, parcels, and facilities, producing the canonical enriched-ping
stream. `segments.py` builds the load-organized workday timeline:
depot departure → windshield → collection → dump → depot arrival, with
cumulative haversine mileage and choreography-violation flags.

### `opentrash.patterns`

Route-agnostic per-parcel service-signature detection. Detects weekly,
weekly-double, and biweekly signatures from enriched pings using a chunked
DuckDB CTAS pipeline. Detection groups on `(APN, vehicle_id)` only —
route columns are sidecar metadata, never detection keys. Outputs are
partitioned parquet files with idempotent, skip-if-fresh writes.

### `opentrash.routeview`

Interactive single-route, single-day, single-vehicle HTML map rendering.
The truck's trail renders as colored GPS dots (granularity preserved),
parcels color by served / missed / unknown, patterns expectations
appear in parcel popups, and per-load tonnage is matched by vehicle +
time window against the tonnage records. Output is a single self-contained
HTML file per route + day + vehicle.

### Command-line interface

The package exposes a lightweight `opentrash` command-line entry point.

Current commands include:

```bash
opentrash --help
opentrash --version
opentrash modules
opentrash doctor
python -m opentrash
```

The CLI currently focuses on package discovery, version reporting, and
environment checks. Workflow-level commands for preparation, enrichment,
pattern detection, and RouteView rendering are planned, but the primary alpha
API remains the Python modules.

## Coordinate reference systems

The working CRS is **EPSG:2230** (California State Plane Zone 6, US feet)
by default. This is configurable through `opentrash.core.crs`; users in
other state plane zones should set their working CRS to whatever provides
metric-friendly, distance-true geometry for their area of operations.

The web CRS for rendered HTML is **EPSG:4326** (WGS 84, longitude /
latitude). The render layer in `routeview` handles the reprojection.

Buffered geometries use the working CRS so that buffer distances are
meaningful.

## Data flow

For a typical day, the intended flow is:

1. **GPS ingest** (`opentrash.adapters.gps` → `opentrash.cache`) — pulls
   the day's GPS pings into the cache.
2. **Engine** (`opentrash.engine.enrichment`) — joins pings against routes,
   parcels, and facilities. Produces enriched pings.
3. **Segments** (`opentrash.engine.segments`) — derives the load-organized
   timeline from enriched pings.
4. **Patterns** (`opentrash.patterns`) — runs against a long window of
   enriched pings, not just today's data.
5. **RouteView** (`opentrash.routeview`) — combines today's enriched pings,
   today's segments, and the latest patterns into a single HTML per
   route + vehicle.

Patterns is the **macro** view: it answers "what does this parcel typically
look like over a long horizon?" RouteView is the **micro** view: it answers
"what happened today on this route?" Together they form a validation loop
between long-period prediction and single-day reality.

## Versioning and stability

`opentrash` follows Semantic Versioning. While the package is in `0.x`,
breaking changes between minor versions (`0.1 → 0.2`) are possible and will
be documented in release notes. At `1.0.0` the API contract tightens: breaking
changes will require a major version bump.

The package is released under the Apache License 2.0.