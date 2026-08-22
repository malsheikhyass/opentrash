"""Render the RouteView HTML — wrap the bundled MapLibre template with the input data.

The template (``opentrash/routeview/templates/maplibre_template.html``) is the
production HTML from the original RouteView notebook, preserved
byte-for-byte except for one transformation: Python expression
placeholders (``{DAY.isoformat()}``, ``{json.dumps(USE_CODE)}``) have
been swapped for plain-name placeholders that ``str.format()`` can
resolve. Literal JS braces (the ``{x}/{y}/{z}`` tile URL syntax, the
JS regex escapes) are doubled in the template so they survive the
format pass.

This module's job is small: load the template, build the dict of
substitution values from the inputs, run ``.format(**ctx)``, return
the string.
"""

from __future__ import annotations

import datetime as dt
import json
from importlib import resources

# Shared color constants, re-exported from sibling modules so the
# template's MapLibre style can match the GeoJSON property colors
# without duplication.
from .parcel_eval import MISSED_COLOR, SERVED_COLOR, UNKNOWN_COLOR  # noqa: F401
from .trail import PHASE_COLORS  # noqa: F401

# The default USE_CODE dict from the v1 notebook. Maps tonnage source
# code prefixes to commodity labels for the right-side info panel.
DEFAULT_USE_CODE: dict[str, str] = {
    "R": "Refuse",
    "O": "Organics",
    "C": "Recycling",
}


def build_routeview_html(
    *,
    route_id: str,
    vehicle: str,
    day: dt.date | str,
    day_time_label: str,
    route_json: str,
    lf_json: str,
    pts_json: str,
    parcels_json: str,
    sites_json: str,
    summary_json: str,
    stats_json_enriched: str,
    center_lon: float,
    center_lat: float,
    use_code: dict[str, str] | None = None,
    field_note: str = "",
    scale_note: str = "",
) -> str:
    """Render the RouteView HTML for one (route, day, vehicle).

    Parameters
    ----------
    route_id, vehicle:
        The route polygon ID and the vehicle this view is for.
    day:
        The service date — accepts a ``datetime.date`` or an ISO string.
    day_time_label:
        Human-readable label for the shift hours, e.g. ``"06:00 → 17:00"``.
    route_json:
        GeoJSON FeatureCollection (string) of the route polygon outline.
    lf_json:
        GeoJSON of landfill polygons / markers.
    pts_json:
        GeoJSON FeatureCollection of trail dots (output of
        :func:`opentrash.routeview.trail.build_trail_geojson`).
    parcels_json:
        GeoJSON FeatureCollection of parcels with served/missed/expected
        props (output of :func:`opentrash.routeview.parcel_eval.build_parcels_geojson`).
    sites_json:
        GeoJSON of facility / depot markers.
    summary_json:
        JSON blob of summary stats for the right-side panel.
    stats_json_enriched:
        JSON blob of per-load tonnage stats from the tonnage join.
    center_lon, center_lat:
        Map center coordinates (web CRS, EPSG:4326).
    use_code:
        Override the default commodity-code mapping. None = use
        :data:`DEFAULT_USE_CODE`.
    field_note, scale_note:
        Plain-text notes about the two tonnage data sources, displayed
        in the tonnage panel.

    Returns
    -------
    The fully rendered HTML as a string.
    """
    if isinstance(day, str):
        day_iso = day
    else:
        day_iso = day.isoformat()

    use_code_resolved = use_code if use_code is not None else DEFAULT_USE_CODE

    template = _load_template()
    return template.format(
        ROUTE_ID=route_id,
        vehicle=vehicle,
        day_iso=day_iso,
        day_time_label=day_time_label,
        route_json=route_json,
        lf_json=lf_json,
        pts_json=pts_json,
        parcels_json=parcels_json,
        sites_json=sites_json,
        summary_json=summary_json,
        stats_json_enriched=stats_json_enriched,
        use_code_json=json.dumps(use_code_resolved),
        field_note_json=json.dumps(field_note),
        scale_note_json=json.dumps(scale_note),
        center_lon=center_lon,
        center_lat=center_lat,
    )


def _load_template() -> str:
    """Read the bundled MapLibre HTML template as a string."""
    return resources.files("opentrash.routeview.templates").joinpath(
        "maplibre_template.html"
    ).read_text(encoding="utf-8")
