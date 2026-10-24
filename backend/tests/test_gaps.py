"""Unit tests of the anomaly rule. Run from backend/: python -m pytest tests"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions", "ingestion"))

import comtrade
import gaps

EXPORTERS = {"FRA"}


def row(ym, reporter, flow, partner, value, qty, hs4="1001"):
    return {"ym": ym, "reporter": reporter, "flow": flow, "partner": partner, "hs4": hs4,
            "value_usd": value, "qty_t": qty}


def cell(cells, freq, period, partner="EGY"):
    return next(c for c in cells if c["freq"] == freq and c["period"] == period and c["partner"] == partner)


@pytest.mark.parametrize("cif, status", [
    (440, "ok"),                           # +10 %
    (600, "anomaly_gap_above_threshold"),  # +50 %
    (380, "anomaly_fob_above_cif"),        # -5 %
])
def test_examples_from_the_brief(cif, status):
    # Slide 7: FOB = 400 USD/t.
    cells, _ = gaps.compute([
        row("2023-01", "FRA", "Export", "EGY", 400_000, 1000),
        row("2023-01", "EGY", "Import", "FRA", cif * 1000, 1000),
    ], EXPORTERS)
    assert cell(cells, "M", "2023-01")["status"] == status


def test_threshold_is_exclusive():
    cells, _ = gaps.compute([
        row("2023-01", "FRA", "Export", "EGY", 400_000, 1000),
        row("2023-01", "EGY", "Import", "FRA", 540_000, 1000),  # exactly +35 %
    ], EXPORTERS)
    assert cell(cells, "M", "2023-01")["status"] == "ok"


def test_quarter_sums_values_and_quantities_before_dividing():
    cells, _ = gaps.compute([
        row("2023-01", "FRA", "Export", "EGY", 100_000, 100), row("2023-01", "EGY", "Import", "FRA", 110_000, 100),
        row("2023-02", "FRA", "Export", "EGY", 900_000, 3000), row("2023-02", "EGY", "Import", "FRA", 990_000, 3000),
    ], EXPORTERS)
    q = cell(cells, "Q", "2023-Q1")
    assert q["fob_usd_t"] == pytest.approx(1_000_000 / 3100)
    assert q["cif_usd_t"] == pytest.approx(1_100_000 / 3100)
    assert q["months"] == 2


def test_month_declared_on_one_side_is_kept_out_of_the_quarter():
    cells, _ = gaps.compute([
        row("2023-01", "FRA", "Export", "EGY", 400_000, 1000), row("2023-01", "EGY", "Import", "FRA", 440_000, 1000),
        row("2023-02", "FRA", "Export", "EGY", 400_000, 1000),  # no mirror import
    ], EXPORTERS)
    assert cell(cells, "M", "2023-02")["status"] == "one_sided"
    assert cell(cells, "Q", "2023-Q1")["months"] == 1
    assert cell(cells, "Q", "2023-Q1")["gap"] == pytest.approx(0.10)


def test_imports_from_a_country_outside_the_scope_are_ignored():
    cells, _ = gaps.compute([row("2023-01", "EGY", "Import", "TUR", 100, 1)], EXPORTERS)
    assert cells == []


def test_weights_count_one_sided_flows():
    _, weights = gaps.compute([
        row("2023-01", "EGY", "Import", "FRA", 500, 1),
        row("2023-01", "FRA", "Export", "MAR", 200, 1),
    ], EXPORTERS)
    assert weights[("FRA", "1001", "2023")] == {"EGY": 500, "MAR": 200}


def test_normalize_drops_world_and_converts_units():
    rows = comtrade.normalize([
        {"period": "202501", "reporterISO": "ARG", "flowCode": "X", "partnerCode": 24, "partnerISO": "AGO",
         "cmdCode": "1001", "primaryValue": 22998881.93, "netWgt": 101939000.0},
        {"period": "202501", "reporterISO": "ARG", "flowCode": "X", "partnerCode": 0, "partnerISO": "W00",
         "cmdCode": "1001", "primaryValue": 1, "netWgt": 1},
    ])
    # Same figures as DNEXT's file: ARG -> AGO, wheat, 2025-01, 22 998 882 USD, 101 939 t.
    assert rows == [{"ym": "2025-01", "reporter": "ARG", "flow": "Export", "partner": "AGO", "hs4": "1001",
                     "value_usd": 22998881.93, "qty_t": 101939.0}]


@pytest.mark.parametrize("fob_value, cif_value, status", [
    (0, 440_000, "anomaly_gap_above_threshold"),  # (CIF - 0) / 0 tends to infinity
    (0, 0, "ok"),                                 # nothing to compare
    (400_000, 0, "anomaly_fob_above_cif"),        # gap of -100 %
])
def test_zero_values(fob_value, cif_value, status):
    cells, _ = gaps.compute([
        row("2023-01", "FRA", "Export", "EGY", fob_value, 1000),
        row("2023-01", "EGY", "Import", "FRA", cif_value, 1000),
    ], EXPORTERS)
    m = cell(cells, "M", "2023-01")
    assert m["status"] == status
    assert m["gap"] is None if fob_value == 0 else m["gap"] == pytest.approx(-1)


def test_missing_quantity_gives_no_unit_price():
    cells, _ = gaps.compute([
        row("2023-01", "FRA", "Export", "EGY", 400_000, float("nan")),
        row("2023-01", "EGY", "Import", "FRA", 440_000, 1000),
    ], EXPORTERS)
    assert cell(cells, "M", "2023-01")["status"] == "one_sided"


def test_flow_declared_on_one_side_only_is_never_an_anomaly():
    # DNEXT's note: Russia stopped reporting exports after 2021-12; partners' imports remain legitimate.
    cells, weights = gaps.compute([
        row("2022-03", "EGY", "Import", "RUS", 900_000, 1000),
        row("2022-05", "EGY", "Import", "RUS", 900_000, 1000),
    ], {"RUS"})
    statuses = {(c["freq"], c["period"]): c["status"] for c in cells if c["partner"] == "EGY"}
    assert statuses == {("M", "2022-03"): "one_sided", ("M", "2022-05"): "one_sided",
                        ("Q", "2022-Q1"): "one_sided", ("Q", "2022-Q2"): "one_sided", ("Y", "2022"): "one_sided"}
    assert weights[("RUS", "1001", "2022")] == {"EGY": 1_800_000}


def test_export_and_import_rows_are_never_added_together():
    cells, weights = gaps.compute([
        row("2023-01", "FRA", "Export", "EGY", 400_000, 1000),
        row("2023-01", "EGY", "Import", "FRA", 440_000, 1100),
    ], EXPORTERS)
    m = cell(cells, "M", "2023-01")
    assert (m["fob_value_usd"], m["cif_value_usd"]) == (400_000, 440_000)
    # The partner's weight is the larger declaration, not the sum of both.
    assert weights[("FRA", "1001", "2023")] == {"EGY": 440_000}


def test_row_with_zero_quantity_is_ignored_and_the_other_side_kept():
    cells, weights = gaps.compute([
        row("2023-01", "FRA", "Export", "EGY", 400_000, 0),       # ignored
        row("2023-01", "EGY", "Import", "FRA", 440_000, 1000),   # kept, legitimate one-sided flow
    ], EXPORTERS)
    assert cell(cells, "M", "2023-01")["status"] == "one_sided"
    assert cell(cells, "Y", "2023")["status"] == "one_sided"
    assert weights[("FRA", "1001", "2023")] == {"EGY": 440_000}
