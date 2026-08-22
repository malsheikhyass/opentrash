"""Vehicle identifiers — parsing, normalization, and fleet classification.

A vehicle ID is a short numeric code that tags every weighbridge ticket and GPS
record. In this operation the **first three digits are a fleet prefix** that
identifies what kind of truck it is, and the rest is the per-truck number:

    100001  →  prefix=815, number=001  →  AUTO   (automated collection truck)
    300145  →  prefix=300, number=145  →  MANUAL (manual collection)

The classification (prefix -> kind) is a knob that lives **in this module as a
plain dict** — not scattered through ``if`` statements. When operations changes
or a new fleet range appears, you edit one table here and every part of the
package (tonnage filtering, route analysis, reporting) picks it up.

If you're adapting this package for a different agency, this is the file you'll
edit first.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Rules as data.
# ---------------------------------------------------------------------------
# Map fleet prefix -> commodity-collection kind.
#
# Example mapping — edit this table to match your agency's fleet ranges. The package treats anything not
# listed here as ``"OTHER"`` — see ``classify_fleet`` below.
FLEET_CLASSIFICATION: dict[str, str] = {
    "100": "AUTO",     # automated side-loaders (example prefix)
    "200": "AUTO",     # automated organics (example prefix)
    "300": "MANUAL",   # manual collection (example prefix)
    "400": "MANUAL",   # manual collection (example prefix)
}

# Prefixes that count as "ours" for filtering external records (e.g. FIELD
# includes other haulers; we want only our trucks). Defaults to the AUTO
# fleet prefixes that show up in tonnage exports. Override per-call when needed.
OUR_FLEET_PREFIXES: tuple[str, ...] = ("100", "200")

# How long the prefix is, in characters. Three in the example schema. Lift this
# into a constant so the rule is visible, not buried in slice indices.
PREFIX_LEN: int = 3


# ---------------------------------------------------------------------------
# The value type.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class VehicleId:
    """A parsed vehicle ID, split into its prefix and number.

    ``frozen=True`` makes the instance immutable and hashable (handy for set
    membership and dict keys); ``slots=True`` keeps it small in memory.

    Construct via :func:`parse_vehicle_id`, which handles the noise (commas,
    trailing ``.0``, whitespace) that comes out of Excel.
    """

    raw: str           # the cleaned string form, e.g. "100001"
    prefix: str        # first PREFIX_LEN chars, e.g. "100"
    number: str        # everything after the prefix, e.g. "001"

    @property
    def kind(self) -> str:
        """Commodity-collection kind from the prefix: AUTO / MANUAL / OTHER."""
        return classify_fleet(self.prefix)

    def __str__(self) -> str:
        return self.raw


# ---------------------------------------------------------------------------
# Parsing + classification.
# ---------------------------------------------------------------------------
def _normalize(value: object) -> str | None:
    """Clean Excel-style noise from a raw vehicle value. Returns ``None`` if empty.

    Strips commas, trailing ``.0`` (from floats), and anything after a dot;
    treats ``""`` / ``"nan"`` / ``"none"`` / ``"null"`` / ``"<na>"`` as missing.
    """
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    # drop a trailing ".0" or any decimal tail
    if "." in s:
        s = s.split(".", 1)[0]
    if not s or s.upper() in {"NA", "NAN", "NONE", "NULL", "<NA>"}:
        return None
    return s


def parse_vehicle_id(value: object) -> VehicleId | None:
    """Parse a raw vehicle value into a :class:`VehicleId`, or ``None`` if invalid.

    "Invalid" means: missing/empty, shorter than the prefix length, or not
    all digits. Returning ``None`` (rather than raising) makes this safe to
    use across a column of mixed-quality data; the caller can drop or report
    the misses.
    """
    s = _normalize(value)
    if s is None or len(s) <= PREFIX_LEN or not s.isdigit():
        return None
    return VehicleId(raw=s, prefix=s[:PREFIX_LEN], number=s[PREFIX_LEN:])


def classify_fleet(prefix: str) -> str:
    """Return the commodity-kind for a prefix, or ``"OTHER"`` if unknown."""
    return FLEET_CLASSIFICATION.get(prefix, "OTHER")


def is_our_fleet(
    value: object,
    prefixes: tuple[str, ...] = OUR_FLEET_PREFIXES,
) -> bool:
    """Whether a raw vehicle value belongs to our fleet (by prefix).

    Used to filter external datasets (e.g. landfill tonnage, which includes
    other haulers) down to just our trucks. The default ``prefixes`` covers
    the AUTO fleet we receive tonnage exports for; pass your own tuple to
    widen or narrow the filter for a different report.
    """
    parsed = parse_vehicle_id(value)
    return parsed is not None and parsed.prefix in prefixes
