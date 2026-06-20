"""Tests for ownership parsing/summary and the top-5 buzzing highlight."""
import pandas as pd
import pytest

from app.models import ConsensusOut
from app.service import _highlights
from app.sources import profiles as prof


def _con(symbol, buy, total, sources=("yahoo",)):
    return ConsensusOut(symbol=symbol, buy_count=buy, hold_count=0, sell_count=0,
                        total_count=total, consensus_score=buy, sources=list(sources))


# ── top-5 buzzing ─────────────────────────────────────────────────────────────

def test_top_buzzed_returns_five_by_coverage():
    stocks = [_con(f"S{i}", buy=i, total=i) for i in range(1, 9)]  # totals 1..8
    h = _highlights(stocks)
    syms = [s.symbol for s in h.top_buzzed]
    assert len(h.top_buzzed) == 5
    assert syms == ["S8", "S7", "S6", "S5", "S4"]   # most analysts first
    assert h.most_buzzed.symbol == "S8"


def test_top_buzzed_handles_few_stocks():
    h = _highlights([_con("A", 3, 3), _con("B", 1, 1)])
    assert [s.symbol for s in h.top_buzzed] == ["A", "B"]


# ── ownership parsing ─────────────────────────────────────────────────────────

def _holders_df():
    return pd.DataFrame({
        "Date Reported": pd.to_datetime(["2026-03-31", "2026-03-31"]),
        "Holder": ["Blackrock Inc.", "Vanguard Group"],
        "pctHeld": [0.0779, 0.0649],
        "Shares": [1144695425, 953847648],
        "Value": [333255184669, 277693670419],
        "pctChange": [-0.0086, 1.0],     # Vanguard added (a "recent buyer")
    })


def test_fetch_ownership_maps_and_finds_buyers(monkeypatch):
    funds_df = pd.DataFrame({
        "Date Reported": pd.to_datetime(["2026-03-31"]),
        "Holder": ["Vanguard 500 Index Fund"],
        "pctHeld": [0.0254], "Shares": [373078146], "Value": [108614242466],
        "pctChange": [0.05],
    })
    major = pd.DataFrame({"Value": [0.0163, 0.6583]},
                         index=["insidersPercentHeld", "institutionsPercentHeld"])

    class FakeTicker:
        major_holders = major
        institutional_holders = _holders_df()
        mutualfund_holders = funds_df

    monkeypatch.setattr(prof.yf, "Ticker", lambda s: FakeTicker())
    prof._OWNERSHIP_CACHE.clear()
    o = prof.fetch_ownership("AAPL")

    assert o["inst_pct"] == 65.83        # fraction → percent
    assert o["insider_pct"] == 1.63
    assert o["fund_holders"] == 1
    assert o["institutions"][0]["holder"] == "Blackrock Inc."
    assert o["institutions"][0]["pct_held"] == 7.79
    # recent buyers = positive pctChange, biggest first (Vanguard +100%, fund +5%)
    assert o["recent_buyers"][0]["holder"] == "Vanguard Group"
    assert o["recent_buyers"][0]["change_pct"] == 100.0


def test_ownership_summary_picks_top_buyer():
    own = {"inst_pct": 65.8, "fund_holders": 3,
           "recent_buyers": [{"holder": "Vanguard Group", "change_pct": 100.0},
                             {"holder": "Fidelity", "change_pct": 5.0}]}
    s = prof.ownership_summary(own)
    assert s["top_buyer"] == "Vanguard Group"
    assert s["top_buyer_change"] == 100.0
    assert s["inst_pct"] == 65.8 and s["fund_holders"] == 3


def test_ownership_summary_no_buyers():
    s = prof.ownership_summary({"inst_pct": 50.0, "fund_holders": 2, "recent_buyers": []})
    assert s["top_buyer"] is None and s["top_buyer_change"] is None


def test_fetch_ownership_failure_is_safe(monkeypatch):
    def boom(s):
        raise RuntimeError("network down")
    monkeypatch.setattr(prof.yf, "Ticker", boom)
    prof._OWNERSHIP_CACHE.clear()
    o = prof.fetch_ownership("ZZZ")
    assert o["institutions"] == [] and o["recent_buyers"] == []
