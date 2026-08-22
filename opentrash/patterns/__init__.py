"""patterns — per-parcel service-signature detection.

A product on top of the engine. Reads enriched pings (output of
:func:`opentrash.engine.enrichment.enrich_pings`) and produces a wide
patterns table: per parcel, who shows up regularly on what DOW at what
hour, with weekly1/weekly2/biweekly ranking.

**Route-agnostic by design.** The detection algorithm groups on
``(APN, vehicle_id)`` pairs only. Route columns (``route_ref``,
``route_org``, ``route_rec``) are carried through from parcels_wkb as
sidecar metadata for downstream readability; they never enter the
detection logic.

Implemented:

- ``config``    — :class:`PatternConfig` dataclass with all knobs.
- ``window``    — :func:`compute_window` helper for "past_year" etc.
- ``detector``  — :func:`detect_patterns_chunk`: the route-agnostic
                  detection pipeline (one chunk at a time).
- ``runner``    — :func:`run_patterns`: chunked orchestration over the
                  parcels universe with idempotent writes.
- ``validator`` — :func:`validate_patterns`: post-run diagnostics.
"""
