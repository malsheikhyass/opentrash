"""prep — one-time data preparation steps.

Turns raw municipal inputs into compact, queryable layers the rest of the
package consumes.

Implemented:

- ``sites``         — clean the sites export, validate route IDs, spatial APN attribution.
- ``parcels``       — load the parcel layer, ensure CRS, bbox prefilter.
- ``static_layers`` — routes (AUTO + MANUAL) and facilities (landfill + depot).
- ``parcels_wkb``   — parcels with WKB geometry + per-route assignment + bbox.
"""
