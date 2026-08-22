"""routeview — interactive single-route, single-day, single-vehicle maps.

The micro counterpart to ``patterns`` (the macro view). For one chosen
day, one vehicle, one route: render a self-contained MapLibre HTML
showing the truck's trail (as colored GPS dots, not a polyline), the
parcels served vs missed, the depot, landfills, and a side panel with
load-by-load breakdown including tonnage (when available).

Five modules:

- ``rank``         — which vehicles touched a route on a given day, ranked.
- ``trail``        — GPS pings as colored dots; phase color from L9 segments.
- ``parcel_eval``  — served/missed per parcel + optional L10 expected vehicle.
- ``render``       — wrap the bundled MapLibre template with the built JSON blobs.
- ``runner``       — file-in / HTML-out driver, three modes.

The HTML template lives at ``opentrash/routeview/templates/maplibre_template.html`` and
is preserved byte-for-byte from the original RouteView notebook, except
for one transformation: Python f-string expressions were swapped for plain
``str.format()`` placeholders so the template can be rendered without
eval.
"""
