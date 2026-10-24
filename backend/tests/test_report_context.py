"""Unit tests of the report context: the trade data around a flagged cell and the Wikipedia retrieval.
Run from the repository root: python -m pytest backend/tests"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions", "explain"))

import trade_context  # noqa: E402
import wikipedia  # noqa: E402


def cell(partner, period, status="ok", **extra):
    return {"pk": "USA#1001#M", "sk": f"{period}#{partner}", "partner": partner, "period": period,
            "status": status, "gap": Decimal("0.1"), **extra}


def test_select_splits_anomaly_history_and_peers():
    cells = [
        cell("EGY", "2024-01"), cell("EGY", "2024-02", "anomaly_fob_above_cif"), cell("EGY", "2024-03"),
        cell("MAR", "2024-02"), cell("DZA", "2024-02", "one_sided"),
    ]
    anomaly, history, peers = trade_context.select(cells, "EGY", "2024-02")
    assert anomaly["status"] == "anomaly_fob_above_cif"
    assert [c["period"] for c in history] == ["2024-01", "2024-02"]  # nothing after the flagged period
    assert [c["partner"] for c in peers] == ["MAR"]                  # one-sided flows have no price to compare
    assert isinstance(anomaly["gap"], float) and "pk" not in anomaly  # plain JSON for the model


def test_select_returns_no_anomaly_for_an_unknown_cell():
    anomaly, _, _ = trade_context.select([cell("EGY", "2024-01")], "EGY", "2024-02")
    assert anomaly is None


def test_queries_use_english_names_product_and_year():
    assert wikipedia.queries("RUS", "EGY", "1001", "2022-03") == [
        "wheat exports Russia Egypt 2022",
        "wheat exports Russia 2022",
    ]


def fake_api(pages):
    """Stands in for the Wikipedia API: search returns the titles, a page request returns its text."""
    def get(params):
        if params["action"] == "query" and "list" in params:
            return {"query": {"search": [{"title": t} for t in pages]}}
        title = params["titles"]
        return {"query": {"pages": {"1": {"title": title, "fullurl": f"https://en.wikipedia.org/wiki/{title}",
                                          "extract": pages[title]}}}}
    return get


def test_fetch_returns_three_whole_pages(monkeypatch):
    monkeypatch.setattr(wikipedia, "_get", fake_api({"A": "a" * 10, "B": "b" * 10, "C": "c" * 10, "D": "d"}))
    pages = wikipedia.fetch("RUS", "EGY", "1001", "2022-03")
    assert [(p["title"], p["text"]) for p in pages] == [("A", "a" * 10), ("B", "b" * 10), ("C", "c" * 10)]
    assert pages[0]["url"] == "https://en.wikipedia.org/wiki/A"


def test_fetch_stops_at_the_character_budget(monkeypatch):
    monkeypatch.setattr(wikipedia, "MAX_CHARS", 15)
    monkeypatch.setattr(wikipedia, "_get", fake_api({"A": "a" * 10, "B": "b" * 10, "C": "c" * 10}))
    pages = wikipedia.fetch("RUS", "EGY", "1001", "2022-03")
    assert [p["text"] for p in pages] == ["a" * 10, "b" * 5]


def test_wikipedia_failure_never_fails_the_report(monkeypatch):
    def down(params):
        raise TimeoutError("no answer")
    monkeypatch.setattr(wikipedia, "_get", down)
    assert wikipedia.fetch("RUS", "EGY", "1001", "2022-03") == []
