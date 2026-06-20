"""Tests for stock-fundamentals notes and the India (NSE) market split."""
from app.sources.fundamentals import build_fundamentals_notes
from app.themes import all_tickers, market_of, themes_for, tickers_for


# ── fundamentals notes (pure) ───────────────────────────────────────────────

def test_notes_empty_for_empty_input():
    assert build_fundamentals_notes({}) == []


def test_low_pe_flagged_cheap():
    notes = build_fundamentals_notes({"pe_ratio": 10})
    assert any("low" in n.lower() for n in notes)


def test_high_pe_flagged_expensive():
    notes = build_fundamentals_notes({"pe_ratio": 40})
    assert any("high" in n.lower() for n in notes)


def test_negative_pe_flagged_unprofitable():
    notes = build_fundamentals_notes({"pe_ratio": -5})
    assert any("unprofitable" in n.lower() for n in notes)


def test_high_roe_flagged_efficient():
    notes = build_fundamentals_notes({"roe": 25})
    assert any("efficient" in n.lower() for n in notes)


def test_high_debt_to_equity_flagged_leverage():
    notes = build_fundamentals_notes({"debt_to_equity": 200})
    assert any("leverage" in n.lower() for n in notes)


def test_dividend_yield_only_noted_when_positive():
    assert build_fundamentals_notes({"dividend_yield": 0}) == []
    notes = build_fundamentals_notes({"dividend_yield": 2.5})
    assert any("dividend" in n.lower() for n in notes)


def test_52week_position_near_highs():
    notes = build_fundamentals_notes({"current_price": 95, "week52_low": 50, "week52_high": 100})
    assert any("near the highs" in n for n in notes)


# ── India market split ──────────────────────────────────────────────────────

def test_market_of_detects_nse_suffix():
    assert market_of("RELIANCE.NS") == "in"
    assert market_of("AAPL") == "us"


def test_india_universe_is_disjoint_from_us():
    us = set(all_tickers(market="us"))
    india = set(all_tickers(market="in"))
    assert us.isdisjoint(india)
    assert len(india) > 0


def test_tickers_for_india_theme():
    it = tickers_for("IT", market="in")
    assert "TCS.NS" in it
    assert "AAPL" not in it


def test_themes_for_auto_detects_india_symbol():
    themes = themes_for("TCS.NS")
    assert "IT" in themes
