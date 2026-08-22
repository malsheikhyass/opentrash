"""Tests for the tonnage pipeline.

Covers: filename parsing, the canonical-header column resolver, hash-key
stability and idempotency, year-partitioned upsert behavior, and a tiny
end-to-end run against synthetic .xlsx files written into a temp directory.
"""

import pandas as pd
import pytest

from opentrash.tonnage.cleaners import (
    clean_fleet_id,
    matl_norm_field,
    matl_norm_scale,
    resolve_column,
)
from opentrash.tonnage.keys import add_keys_field, add_keys_scale
from opentrash.tonnage.pipeline import run_ingest
from opentrash.tonnage.registry import (
    detect_source_from_name,
    parse_file_date_from_name,
    pick_latest_file,
    scan_tonnage_folder,
)
from opentrash.tonnage.upsert import upsert_annual_parquet


# ----- registry / filename parsing -----
def test_detect_source_from_name():
    assert detect_source_from_name("SCALE_021926.xlsx") == "SCALE"
    assert detect_source_from_name("FIELD_021926.xlsx") == "FIELD"
    assert detect_source_from_name("WAT_021926.xlsx") == "UNKNOWN"


def test_parse_file_date_mmddyy():
    ts = parse_file_date_from_name("SCALE_021926.xlsx")
    assert ts == pd.Timestamp("2026-02-19")
    assert pd.isna(parse_file_date_from_name("SCALE_no_date.xlsx"))


def test_scan_tonnage_folder(tmp_path):
    (tmp_path / "SCALE_021826.xlsx").write_bytes(b"")  # body irrelevant for scan
    (tmp_path / "SCALE_021926.xlsx").write_bytes(b"")
    (tmp_path / "FIELD_021926.xlsx").write_bytes(b"")
    (tmp_path / "ignored.txt").write_text("x")

    reg = scan_tonnage_folder(tmp_path)
    assert len(reg) == 3
    assert set(reg["source"]) == {"SCALE", "FIELD"}
    assert (reg["status"] == "NEW").all()


def test_pick_latest_file(tmp_path):
    (tmp_path / "SCALE_021826.xlsx").write_bytes(b"")
    (tmp_path / "SCALE_021926.xlsx").write_bytes(b"")
    reg = scan_tonnage_folder(tmp_path)
    latest = pick_latest_file(reg, "SCALE")
    assert latest.endswith("SCALE_021926.xlsx")


# ----- canonical-header column resolver -----
def test_resolve_column_tolerates_case_and_whitespace():
    df = pd.DataFrame({" Co_Fleet_ID ": [1], "TONS": [2]})
    assert resolve_column(df, ["CO_FLEET_ID"]) == " Co_Fleet_ID "
    assert resolve_column(df, ["TRUCK", "CO_FLEET_ID"]) == " Co_Fleet_ID "
    assert resolve_column(df, ["NONEXISTENT"]) is None


# ----- field cleaners -----
def test_clean_fleet_id_strips_noise():
    s = pd.Series(["811,145", "811145.0", "", "NaN", None])
    out = clean_fleet_id(s)
    assert out.iloc[0] == "811145"
    assert out.iloc[1] == "811145"
    assert pd.isna(out.iloc[2])
    assert pd.isna(out.iloc[3])
    assert pd.isna(out.iloc[4])


def test_matl_norm_scale():
    s = pd.Series(["REFUSE", "RECYCLING", "COLLECTIONS ORGANICS", "GREEN WASTE"])
    out = matl_norm_scale(s)
    assert list(out) == ["REFUSE", "RECYCLING", "ORGANIC", "OTHER"]


def test_matl_norm_field():
    s = pd.Series(["REFUSE", "GREEN WASTE", "CHRISTMAS TREES"])
    out = matl_norm_field(s)
    assert list(out) == ["REFUSE", "ORGANIC", "OTHER"]


# ----- record-key stability -----
def _arts_row(**over):
    base = dict(
        rpt_dt=pd.Timestamp("2026-02-19 09:30:00"),
        co_fleet_id="811145",
        tons=1.234567,
        matl_norm="REFUSE",
    )
    base.update(over)
    base["ROUTE#"] = base.get("ROUTE#", "62081")
    base["NBR_LOADS"] = base.get("NBR_LOADS", "1")
    base["DUMP LOCATION"] = base.get("DUMP LOCATION", "CENTRAL LANDFILL")
    return base


def test_record_key_arts_is_stable_and_deduplicates():
    df = pd.DataFrame([_arts_row(), _arts_row()])      # identical rows
    keyed = add_keys_scale(df)
    assert keyed["record_key"].iloc[0] == keyed["record_key"].iloc[1]
    # Tons jitter beyond the 3rd decimal does NOT change the key
    # (1.23451 and 1.23459 both round to 1.235).
    a = add_keys_scale(pd.DataFrame([_arts_row(tons=1.23451)]))
    b = add_keys_scale(pd.DataFrame([_arts_row(tons=1.23459)]))
    assert a["record_key"].iloc[0] == b["record_key"].iloc[0]


def test_record_key_rad_uses_tran_when_present():
    df = pd.DataFrame([{
        "ld_dt_tm": pd.Timestamp("2026-02-19 09:30:00"),
        "co_fleet_id": "100001",
        "LANDFILL_DEST_DESC": "CENTRAL LANDFILL",
        "matl_norm": "REFUSE",
        "tons": 5.0,
        "TRAN_NUM": "T-1",
    }])
    keyed = add_keys_field(df)
    assert keyed["record_key"].iloc[0]   # non-empty hash
    assert keyed["year"].iloc[0] == 2026


# ----- upsert idempotency -----
def test_upsert_is_idempotent(tmp_path):
    df = pd.DataFrame({
        "year": [2026, 2026, 2025],
        "record_key": ["a", "b", "c"],
        "tons": [1.0, 2.0, 3.0],
    })
    out_dir = tmp_path / "out"
    s1 = upsert_annual_parquet(df, "SCALE", out_dir)
    assert s1[2026]["added"] == 2 and s1[2025]["added"] == 1
    # Re-run with the same df: zero rows added.
    s2 = upsert_annual_parquet(df, "SCALE", out_dir)
    assert s2[2026]["added"] == 0 and s2[2025]["added"] == 0
    # New row in 2026 -> only the new key gets added.
    df2 = pd.DataFrame({"year": [2026], "record_key": ["d"], "tons": [4.0]})
    s3 = upsert_annual_parquet(df2, "SCALE", out_dir)
    assert s3[2026]["added"] == 1 and s3[2026]["total"] == 3


def test_upsert_requires_record_key(tmp_path):
    df = pd.DataFrame({"year": [2026], "tons": [1.0]})   # no record_key
    with pytest.raises(ValueError, match="record_key"):
        upsert_annual_parquet(df, "SCALE", tmp_path)


# ----- end-to-end pipeline with synthetic xlsx -----
def _write_synthetic_scale(path, rows):
    """Write a tiny SCALE-shaped xlsx for the pipeline to ingest."""
    pd.DataFrame(rows).to_excel(path, sheet_name="SCALE Extract", index=False)


def _write_synthetic_field(path, rows):
    pd.DataFrame(rows).to_excel(path, sheet_name="FIELD Extract", index=False)


def test_pipeline_end_to_end_and_rerun_idempotent(tmp_path):
    data_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    data_dir.mkdir()

    scale_rows = [
        {
            "ROUTE#": "62081",
            "TRANSACTION #": "T1",
            "RPT_DT": "2026-02-19 09:30",
            "LD_DT_TM": "2026-02-19 09:30",
            "TRUCK": "811145",
            "TONS": 1.5,
            "MATL_TYP_DESC": "REFUSE",
            "NBR_LOADS": 1,
            "DUMP LOCATION": "CENTRAL LANDFILL",
        },
        {
            "ROUTE#": "62081",
            "TRANSACTION #": "T2",
            "RPT_DT": "2026-02-19 11:00",
            "LD_DT_TM": "2026-02-19 11:00",
            "TRUCK": "811145",
            "TONS": 2.0,
            "MATL_TYP_DESC": "COLLECTIONS ORGANICS",
            "NBR_LOADS": 2,
            "DUMP LOCATION": "CENTRAL LANDFILL",
        },
    ]
    field_rows = [
        {
            "TRAN_NUM": "R1",
            "LD_DT_TM": "2026-02-19 08:00",
            "RPT_DT": "2026-02-19",
            "TONS": 4.2,
            "TRUCK": "100001",
            "MATL_TYP_DESC": "REFUSE",
        },
        # Should be filtered out (other hauler, not our fleet prefix).
        {
            "TRAN_NUM": "R2",
            "LD_DT_TM": "2026-02-19 08:30",
            "RPT_DT": "2026-02-19",
            "TONS": 9.9,
            "TRUCK": "999999",
            "MATL_TYP_DESC": "REFUSE",
        },
    ]
    _write_synthetic_scale(data_dir / "SCALE_021926.xlsx", scale_rows)
    _write_synthetic_field(data_dir / "FIELD_021926.xlsx", field_rows)

    # First run.
    reg = run_ingest(data_dir, out_dir)
    assert (reg["status"] == "OK").all()
    arts_p = pd.read_parquet(out_dir / "SCALE_2026.parquet")
    rad_p = pd.read_parquet(out_dir / "FIELD_2026.parquet")
    assert len(arts_p) == 2
    assert len(rad_p) == 1     # the 999999 truck was filtered out
    assert (out_dir / "registry.parquet").exists()

    # Re-run on identical inputs: zero new rows.
    run_ingest(data_dir, out_dir)
    arts_p2 = pd.read_parquet(out_dir / "SCALE_2026.parquet")
    rad_p2 = pd.read_parquet(out_dir / "FIELD_2026.parquet")
    assert len(arts_p2) == 2   # idempotent
    assert len(rad_p2) == 1
