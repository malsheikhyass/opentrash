"""tonnage — Excel-to-parquet ingestion + lookup.

Tonnage data comes from a legacy system as messy Excel files, daily-ish. The
ingestion modules turn that into partitioned parquet with hash-based dedup,
vehicle-ID parsing, and an idempotent upsert path. Same Excel processed twice
produces zero new rows.

Implemented:

- ``registry``  — track input file paths + manage which files we have on disk.
- ``cleaners``  — column normalization, type coercion, business rule application.
- ``keys``      — deterministic record-hash keys for dedup.
- ``upsert``    — idempotent merge into the year-partitioned parquet store.
- ``pipeline``  — the top-level ingestion entry point.

Planned:

- ``lookup``    — service for "what tipped on day D from vehicle V" queries.
"""
