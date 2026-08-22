# Learn more

This page collects external resources that go deeper into how `opentrash`
works and how to extend it. Useful if you're building something on top of
the package, contributing back, or want to understand the implementation
in more detail than the architecture page provides.

## Course material

A free, 12-lesson course walks through building the package from scratch:
GPS ingest, the routing engine, pattern detection, RouteView, and
release engineering. The course is hosted by
[AI Research Corps (AIRC)](https://airesearchcorps.org/) and available at:

[airesearchcorps.org/opentrash](https://airesearchcorps.org/opentrash/)

It's an option for users who want to understand the package at the
implementation level — equally suitable as a reference if you'd like to
build a similar package for an adjacent domain.

## Source code

The package source is on GitHub at
[github.com/malsheikhyass/opentrash](https://github.com/malsheikhyass/opentrash).
Issues, discussions, and pull requests are welcome.

## Related projects and tools

`opentrash` builds on a strong open-source geospatial stack. If you're new
to any of these, their documentation is the right place to go deeper:

- [DuckDB](https://duckdb.org/) — in-process analytical database that
  drives every set-based aggregation in the package.
- [DuckDB Spatial extension](https://duckdb.org/docs/extensions/spatial) —
  the spatial-join primitives the engine depends on.
- [GeoPandas](https://geopandas.org/) — Python's standard geo-dataframe library.
- [Shapely](https://shapely.readthedocs.io/) — Python geometry library
  underlying GeoPandas.
- [PyProj](https://pyproj4.github.io/pyproj/) — coordinate-reference-system
  transformations.
- [MapLibre GL JS](https://maplibre.org/) — the open-source map renderer
  that powers RouteView output.
- [Apache Arrow / Parquet](https://arrow.apache.org/) — the columnar
  storage format used throughout for cached and prepared data.

## Background reading

Some of the design decisions in `opentrash` are easier to understand
against the broader context of fleet telematics and geospatial analytics:

- *Geographic Information Systems for Solid Waste Management* — the
  three-tier GIS taxonomy (operational, support, strategic) maps onto
  the package's static-layer / cache / product separation.
- The DuckDB whitepapers and conference talks on columnar query
  execution explain why DuckDB is well-suited for the chunked CTAS
  pipelines used in pattern detection.

If you have additional resources to recommend, please open a pull request
adding them here.
