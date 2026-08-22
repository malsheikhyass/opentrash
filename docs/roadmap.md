# Roadmap

This page describes the direction of `opentrash` development. It is a
statement of intent, not a commitment: priorities will shift as real-world
use surfaces what matters most. The [Changelog](https://github.com/malsheikhyass/opentrash/blob/main/CHANGELOG.md)
records what has actually shipped.

## Current release: 0.1.0

The first public release is feature-complete across the core pipeline:
the routing engine, segments timeline, per-parcel pattern detection,
interactive single-route HTML rendering, GPS adapters for Geotab and
PostgreSQL/PostGIS, and an idempotent tonnage ingest pipeline. The API
is considered alpha — usable for real work, but subject to refinement
before `1.0.0`.

## Upcoming releases

The following are planned for upcoming `0.x` releases. They are listed in
rough priority order; individual items may move between releases as the
work crystallizes.

### 0.2.0 — Samples and tutorials {#samples-and-tutorials}

A synthetic sample dataset that ships with the package or is downloadable
on first use, so new users can run the full pipeline end-to-end without
needing their own GPS, parcels, or routes. Together with an `examples/`
directory of runnable scripts and notebooks covering the most common
workflows: a one-day RouteView, a year-long patterns run, a tonnage
ingest from scratch.

### 0.3.0 — Road-network mileage

Replace the current haversine-approximation mileage with road-network
mileage, using either OpenStreetMap routing services or a pluggable
routing backend. This unlocks more accurate per-segment distances for
operational analytics and tonnage-per-mile metrics.

### 0.4.0 — Per-commodity routing

The engine currently collapses to a single `route_id` per ping. Real
waste-collection operations run multiple commodity streams (refuse,
organics, recycling) on overlapping route geographies. This release
will carry the commodity dimension through the engine end-to-end, with
the patterns module producing per-commodity signatures and RouteView
rendering commodity-aware overlays.

### 0.5.0 — Operational dashboards

A small set of self-contained HTML dashboards built on top of the existing
products: fleet-wide service-completion summaries, multi-day pattern
trends, route-balance views. The principle stays the same — calculations
on enriched pings, no new spatial joins, self-contained HTML output.

## Beyond 0.5

The following are on the longer horizon. Each is significant enough to
warrant its own release planning when the time comes.

### Additional GPS adapters

Adapters for more telematics platforms beyond Geotab and PostgreSQL.
Candidates include Verizon Connect, Samsara, and direct CSV/parquet
ingestion for agencies that already export their telematics data to
cold storage.

### Pluggable parcel and route formats

Today's prep pipeline expects parcels and routes as WKB-backed parquet.
A more pluggable design would accept GeoJSON, GeoPackage, or PostGIS
directly, with the conversion handled inside `opentrash.prep`.

### Tile-server alternatives in RouteView

RouteView renders against MapLibre with OpenStreetMap-style tile sources.
Future work will add first-class support for self-hosted tile servers
(e.g., MBTiles, PMTiles served from a static host) for agencies that
need air-gapped or fully-controlled deployments.

### conda-forge distribution

In addition to the existing PyPI distribution, a conda-forge recipe to
reach the scientific-Python conda user base. This is a small amount of
recipe work once the package is stable enough to commit to compatible
versioning.

### 1.0.0 — API stabilization

Once the items above have settled and the package has been used against
multiple agencies' real data, `1.0.0` will lock the public API.
Breaking changes after `1.0.0` will require a major version bump under
strict semver.

## How to contribute or request features

Open an issue on GitHub at
[github.com/malsheikhyass/opentrash/issues](https://github.com/malsheikhyass/opentrash/issues)
describing the use case or feature. Concrete use cases — "I have this kind
of data and I'm trying to do this kind of analysis" — are more useful than
abstract feature requests.

For code contributions, the project follows standard GitHub flow: fork,
branch, pull request. Test coverage is expected for new functionality; the
project ships with a comprehensive test suite that should pass on every PR.
