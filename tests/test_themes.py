"""Tests for thematic grouping and theme-filtered feed."""
import pytest

from app import themes
from app.models import AnalystRecommendation
from app.service import build_feed, build_themes
from app.store import RecommendationStore


# ── themes module ─────────────────────────────────────────────────────────────

def test_all_tickers_is_sorted_union_no_dupes():
    all_t = themes.all_tickers()
    assert all_t == sorted(set(all_t))           # unique + sorted
    # NVDA is in several themes but appears once.
    assert all_t.count("NVDA") == 1


def test_tickers_for_case_insensitive():
    assert "NVDA" in themes.tickers_for("ai")
    assert "NVDA" in themes.tickers_for("AI")
    assert themes.tickers_for("nonexistent") == []


def test_themes_for_multi_membership():
    t = themes.themes_for("NVDA")
    assert "AI" in t and "Semiconductors" in t and "Data Center" in t


def test_build_themes_shape():
    res = build_themes()
    names = {t.name for t in res.themes}
    assert {"AI", "Semiconductors", "Finance", "Green Energy"}.issubset(names)
    for t in res.themes:
        assert t.ticker_count == len(t.tickers)


# ── theme-filtered feed ───────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    from datetime import date
    s = RecommendationStore(str(tmp_path / "t.db"))
    today = date.today().isoformat()
    # JPM (Finance), NVDA (AI/Semi), ENPH (Green Energy)
    for sym in ("JPM", "NVDA", "ENPH"):
        s.add_recommendation(AnalystRecommendation(
            symbol=sym, source="yahoo", action="buy", count=10, entry_date=today))
    return s


def test_feed_unfiltered_has_all(store):
    syms = {c.symbol for c in build_feed(store, days=1).stocks}
    assert syms == {"JPM", "NVDA", "ENPH"}


def test_feed_theme_filter(store):
    fin = build_feed(store, days=1, theme="Finance").stocks
    assert {c.symbol for c in fin} == {"JPM"}

    ai = build_feed(store, days=1, theme="AI").stocks
    assert {c.symbol for c in ai} == {"NVDA"}


def test_feed_tags_themes_on_stocks(store):
    by = {c.symbol: c for c in build_feed(store, days=1).stocks}
    assert "Finance" in by["JPM"].themes
    assert "AI" in by["NVDA"].themes and "Semiconductors" in by["NVDA"].themes


def test_feed_unknown_theme_empty(store):
    assert build_feed(store, days=1, theme="Crypto").stocks == []
