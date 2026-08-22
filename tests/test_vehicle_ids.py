"""Tests for opentrash.core.vehicle_ids.

Covers: normalization of Excel noise (commas, ".0", NA tokens),
``VehicleId`` dataclass behavior (immutability, hashability, kind),
classification, and the ``is_our_fleet`` filter used downstream by tonnage.
"""

import pytest

from opentrash.core.vehicle_ids import (
    FLEET_CLASSIFICATION,
    OUR_FLEET_PREFIXES,
    VehicleId,
    classify_fleet,
    is_our_fleet,
    parse_vehicle_id,
)


# ----- parsing -----
def test_parse_clean_id():
    v = parse_vehicle_id("100001")
    assert v == VehicleId(raw="100001", prefix="100", number="001")
    assert v.kind == "AUTO"


def test_parse_strips_excel_noise():
    # comma thousands separator, trailing ".0" from float-typed cells, whitespace
    assert parse_vehicle_id("100,001").raw == "100001"
    assert parse_vehicle_id("100001.0").raw == "100001"
    assert parse_vehicle_id(" 100001 ").raw == "100001"
    assert parse_vehicle_id(100001).raw == "100001"   # numeric input


def test_parse_returns_none_for_invalid():
    for bad in ["", "  ", "NA", "nan", None, "abc123", "81"]:
        assert parse_vehicle_id(bad) is None, f"expected None for {bad!r}"


def test_parse_rejects_prefix_only():
    # Just the prefix with no number tail is not a complete vehicle ID.
    assert parse_vehicle_id("100") is None


# ----- dataclass behavior -----
def test_vehicle_id_is_immutable_and_hashable():
    v = parse_vehicle_id("100001")
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        v.prefix = "999"                 # type: ignore[misc]
    # Hashable -> usable in sets / dict keys
    assert {v, v} == {v}


def test_vehicle_id_str_is_raw():
    v = parse_vehicle_id("300145")
    assert str(v) == "300145"


# ----- classification -----
def test_classify_fleet_known_and_unknown():
    assert classify_fleet("100") == "AUTO"
    assert classify_fleet("300") == "MANUAL"
    assert classify_fleet("999") == "OTHER"      # unknown prefix


def test_kind_property_uses_classification_table():
    v = parse_vehicle_id("999123")
    assert v is not None and v.kind == "OTHER"


def test_fleet_classification_table_shape():
    # Sanity: the published classification table only contains the documented kinds.
    assert set(FLEET_CLASSIFICATION.values()) <= {"AUTO", "MANUAL", "OTHER"}


# ----- is_our_fleet filter -----
def test_is_our_fleet_default():
    assert is_our_fleet("100001") is True
    assert is_our_fleet("200042") is True
    assert is_our_fleet("300145") is False       # not in default OUR_FLEET_PREFIXES
    assert is_our_fleet("999999") is False
    assert is_our_fleet(None) is False
    assert is_our_fleet("garbage") is False


def test_is_our_fleet_custom_prefixes():
    # Widen the filter to include manual fleet too.
    custom = ("300", "400")
    assert is_our_fleet("300145", prefixes=custom) is True
    assert is_our_fleet("100001", prefixes=custom) is False


def test_default_our_fleet_constants_align():
    # The defaults should match the AUTO prefixes in the classification table.
    auto = {p for p, k in FLEET_CLASSIFICATION.items() if k == "AUTO"}
    assert set(OUR_FLEET_PREFIXES) == auto
