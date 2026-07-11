"""Tests for feed highlights, counting-source exclusion, and Yahoo upgrades."""
import pandas as pd
import pytest

from app.analytics import compute_consensus
from app.models import AnalystRecommendation, ConsensusOut
from app.service import _highlights
from app.sources.yahoo import YahooUpgradesClient


def rec(source, action, count=1, firm=None, entry_date="2026-06-14", target=None):
    return AnalystRecommendation(symbol="AAPL", source=source, action=action,
                                 count=count, firm=firm, entry_date=entry_date,
                                 target_price=target)


# ── counting sources ──────────────────────────────────────────────────────────

def test_named_sources_excluded_from_counts_but_shown():
    recs = [
        rec("yahoo", "buy", 20),
        rec("yahoo", "sell", 2),
        rec("fmp", "buy", 1, firm="Morgan Stanley"),      # named: not counted
        rec("yahoo_upgrades", "sell", 1, firm="Citi"),     # named: not counted
        rec("morningstar", "buy", 1, firm="Morningstar"),  # named: not counted
    ]
    c = compute_consensus(recs)
    assert c.buy_count == 20      # only the yahoo aggregate counts
    assert c.sell_count == 2
    # but the named firms + their sources still surface
    assert "Morgan Stanley" in c.firms and "Citi" in c.firms
    assert {"fmp", "yahoo_upgrades", "morningstar"}.issubset(set(c.sources))


# ── highlights ────────────────────────────────────────────────────────────────

def con(symbol, buy, sell, total, sources=("yahoo",), target=None):
    return ConsensusOut(symbol=symbol, buy_count=buy, hold_count=0, sell_count=sell,
                        total_count=total, consensus_score=buy - sell,
                        avg_target=target, sources=list(sources))


class _EmptyStore:
    """Minimal store stub — _highlights only needs list_recent (for catalysts)."""
    def list_recent(self, days=1):
        return []


def test_highlights_picks_buzz_buy_sell():
    stocks = [
        con("AMZN", 60, 0, 60),
        con("TSLA", 5, 12, 25),    # net sell
        con("NVDA", 40, 1, 80, sources=("yahoo", "finnhub")),  # most analysts
    ]
    h = _highlights(stocks, _EmptyStore())
    assert h.most_buzzed.symbol == "NVDA"   # highest total_count
    assert h.top_buy.symbol == "AMZN"       # highest score (+60)
    assert h.top_sell.symbol == "TSLA"      # lowest score (-7)


def test_highlights_no_sell_when_all_positive():
    h = _highlights([con("AMZN", 10, 0, 10), con("MSFT", 5, 0, 5)], _EmptyStore())
    assert h.top_sell is None
    assert h.top_buy.symbol == "AMZN"


def test_highlights_empty():
    h = _highlights([], _EmptyStore())
    assert h.most_buzzed is None and h.top_buy is None and h.top_sell is None


# ── Yahoo upgrades/downgrades ─────────────────────────────────────────────────

def test_yahoo_upgrades_parsing(monkeypatch):
    df = pd.DataFrame(
        {
            "Firm": ["Morgan Stanley", "Citi", "Acme Capital"],
            "ToGrade": ["Overweight", "Sell", "Gibberish"],   # last is unmapped
            "FromGrade": ["Equal-Weight", "Neutral", "Buy"],
            "Action": ["up", "down", "main"],
            "priceTargetAction": ["Raises", "Lowers", "Maintains"],
            "currentPriceTarget": [360.0, 150.0, 0.0],
            "priorPriceTarget": [330.0, 180.0, 0.0],
        },
        index=pd.to_datetime(["2026-06-09", "2026-06-08", "2026-06-07"]),
    )

    class FakeTicker:
        upgrades_downgrades = df

    monkeypatch.setattr("app.sources.yahoo.yf.Ticker", lambda s: FakeTicker())
    recs = YahooUpgradesClient().get_recommendations("AAPL")

    assert len(recs) == 2            # unmapped grade dropped
    by_firm = {r.firm: r for r in recs}
    assert by_firm["Morgan Stanley"].action == "buy"
    assert by_firm["Morgan Stanley"].target_price == 360.0
    assert "330" in by_firm["Morgan Stanley"].note and "360" in by_firm["Morgan Stanley"].note
    assert by_firm["Citi"].action == "sell"
    assert all(r.source == "yahoo_upgrades" and r.count == 1 for r in recs)
    # entry_date taken from the grade date (index)
    assert by_firm["Morgan Stanley"].entry_date == "2026-06-09"
