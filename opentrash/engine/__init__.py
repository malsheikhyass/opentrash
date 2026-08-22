"""engine — THE ROUTING ENGINE.

The architectural heart of the package: the one place spatial joins happen.
Every GPS ping is joined, in a single pass, to every applicable GIS layer
(routes, parcels, facilities). Downstream modules consume the enriched ping
stream as pure aggregations — they never do their own spatial joins.

The rule: **spatial joins are infrastructure; products are calculations.**

Implemented:

- ``config``     — ``EngineConfig`` dataclass: all knobs in one place.
- ``enrichment`` — the single spatial-join entry point: ``enrich_pings()``.
- ``segments``   — load-organized workday timeline + choreography violations.
"""
