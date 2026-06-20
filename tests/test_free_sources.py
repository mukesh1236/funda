"""Tests for the free analyst sources: grade mapping, Yahoo, TipRanks, FMP,
and the latest-per-source consensus reduction."""
import pytest

from app.analytics import compute_consensus
from app.models import AnalystRecommendation
from app.sources._mapping import grade_to_action
from app.sources.fmp import FMPClient, FMPError
from app.sources.tipranks import TipRanksClient
from app.sources import yahoo as yahoo_mod
from app.sources.yahoo import YahooClient


# ── grade_to_action ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Strong Buy", "buy"), ("Outperform", "buy"), ("Overweight", "buy"),
    ("Hold", "hold"), ("Neutral", "hold"), ("Market Perform", "hold"),
    ("Equal-Weight", "hold"), ("Sell", "sell"), ("Underperform", "sell"),
    ("Underweight", "sell"), ("Market Underperform", "sell"),
    ("", None), (None, None), ("gibberish", None),
])
def test_grade_to_action(text, expected):
    assert grade_to_action(text) == expected


# ── Yahoo (yfinance) ──────────────────────────────────────────────────────────

def test_yahoo_mapping(monkeypatch):
    monkeypatch.setattr(yahoo_mod, "_latest_trend", lambda t: {
        "period": "0m", "strongBuy": 8, "buy": 12, "hold": 4,
        "sell": 1, "strongSell": 1,
    })
    monkeypatch.setattr(yahoo_mod, "_price_target", lambda t: 195.0)
    recs = YahooClient().get_recommendations("AAPL", entry_date="2026-06-14")
    by = {r.action: r for r in recs}
    assert by["buy"].count == 20    # strongBuy + buy
    assert by["sell"].count == 2    # strongSell + sell
    assert by["hold"].count == 4
    assert by["buy"].target_price == 195.0
    assert all(r.source == "yahoo" for r in recs)


def test_yahoo_no_data(monkeypatch):
    monkeypatch.setattr(yahoo_mod, "_latest_trend", lambda t: None)
    assert YahooClient().get_recommendations("ZZZZ") == []


# ── TipRanks ──────────────────────────────────────────────────────────────────

def test_tipranks_parse():
    data = {
        "consensuses": [{"nB": 18, "nH": 5, "nS": 2}],
        "ptConsensus": [{"priceTarget": 240.0}],
    }
    buy, hold, sell, target = TipRanksClient._parse(data)
    assert (buy, hold, sell, target) == (18, 5, 2, 240.0)


def test_tipranks_build(monkeypatch):
    client = TipRanksClient(enabled=True)
    monkeypatch.setattr(client, "_fetch", lambda s: {
        "consensuses": [{"nB": 10, "nH": 0, "nS": 3}],
        "priceTarget": 100.0,
    })
    recs = client.get_recommendations("NVDA", entry_date="2026-06-14")
    by = {r.action: r for r in recs}
    assert by["buy"].count == 10 and by["sell"].count == 3
    assert "hold" not in by               # zero buckets skipped
    assert by["buy"].target_price == 100.0


def test_tipranks_disabled():
    assert TipRanksClient(enabled=False).get_recommendations("AAPL") == []


def test_tipranks_fetch_failure(monkeypatch):
    client = TipRanksClient(enabled=True)
    monkeypatch.setattr(client, "_fetch", lambda s: None)
    assert client.get_recommendations("ZZ") == []  # never raises


# ── FMP ───────────────────────────────────────────────────────────────────────

def test_fmp_missing_key():
    with pytest.raises(FMPError):
        FMPClient("")


def test_fmp_grade_mapping(monkeypatch):
    client = FMPClient("fake-key")
    monkeypatch.setattr(client, "_fetch_grades", lambda s: [
        {"gradingCompany": "Morgan Stanley", "newGrade": "Overweight", "date": "2026-06-10"},
        {"gradingCompany": "Goldman Sachs", "newGrade": "Sell", "date": "2026-06-09"},
        {"gradingCompany": "Citi", "newGrade": "Whatever", "date": "2026-06-08"},  # unmapped
    ])
    recs = client.get_recommendations("AAPL", entry_date="2026-06-14")
    assert len(recs) == 2  # unmapped grade skipped
    by_firm = {r.firm: r for r in recs}
    assert by_firm["Morgan Stanley"].action == "buy"
    assert by_firm["Goldman Sachs"].action == "sell"
    assert all(r.source == "fmp" and r.count == 1 for r in recs)


def test_fmp_fetch_failure(monkeypatch):
    client = FMPClient("fake-key")
    monkeypatch.setattr(client, "_fetch_grades", lambda s: None)
    assert client.get_recommendations("ZZ") == []


# ── consensus: latest snapshot per source ─────────────────────────────────────

def rec(source, action, count, entry_date):
    return AnalystRecommendation(symbol="AAPL", source=source, action=action,
                                 count=count, entry_date=entry_date)


def test_consensus_uses_latest_snapshot_per_source():
    recs = [
        rec("yahoo", "buy", 10, "2026-06-13"),   # stale
        rec("yahoo", "buy", 12, "2026-06-14"),   # latest yahoo
        rec("finnhub", "buy", 5, "2026-06-14"),  # latest finnhub
    ]
    c = compute_consensus(recs)
    assert c.buy_count == 17   # 12 (latest yahoo) + 5 (finnhub), NOT 27
    assert set(c.sources) == {"yahoo", "finnhub"}


def test_consensus_named_sources_shown_not_counted():
    # FMP rows are named detail — surfaced in firms/sources but NOT counted
    # (their analysts are already in the yahoo aggregate).
    recs = [
        AnalystRecommendation(symbol="AAPL", source="fmp", action="buy",
                              count=1, firm="Morgan Stanley", entry_date="2026-06-14"),
        AnalystRecommendation(symbol="AAPL", source="fmp", action="sell",
                              count=1, firm="Goldman", entry_date="2026-06-14"),
        rec("yahoo", "buy", 20, "2026-06-14"),
    ]
    c = compute_consensus(recs)
    assert c.buy_count == 20    # only yahoo aggregate counted
    assert c.sell_count == 0
    assert {"Morgan Stanley", "Goldman"}.issubset(set(c.firms))
    assert "fmp" in c.sources
