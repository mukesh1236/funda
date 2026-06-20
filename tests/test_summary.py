"""Tests for the 'why analysts recommend' summary builder."""
import pytest

from app.models import (
    ConsensusOut,
    NewsItem,
    OutcomeOut,
    RecommendationOut,
    StockDetailResult,
)
from app.summarize import build_rule_summary, extract_news_themes


def detail(buy=50, hold=5, sell=1, avg_target=300.0, cur=260.0,
           named=None, news=None, themes=None, status="pending"):
    total = buy + hold + sell
    c = ConsensusOut(
        symbol="NVDA", buy_count=buy, hold_count=hold, sell_count=sell,
        total_count=total, consensus_score=buy - sell, avg_target=avg_target,
        themes=themes or ["AI", "Semiconductors"],
    )
    outcome = OutcomeOut(symbol="NVDA", current_price=cur, target_price=avg_target,
                         status=status) if cur else None
    return StockDetailResult(
        symbol="NVDA", consensus=c, recommendations=named or [],
        outcome=outcome, news=[NewsItem(title=t) for t in (news or [])],
    )


def named_rec(firm, action="buy", note=None):
    return RecommendationOut(rec_id=1, symbol="NVDA", source="yahoo_upgrades",
                             action=action, firm=firm, note=note, entry_date="2026-06-14")


# ── news theme extraction ─────────────────────────────────────────────────────

def test_extract_news_themes_ranks_by_frequency():
    titles = [
        "Nvidia AI chip demand surges",
        "New AI data center orders boost revenue",
        "Analysts cite AI growth",
    ]
    themes = extract_news_themes(titles)
    assert themes[0] == "AI"          # most frequent across the headlines
    assert len(themes) >= 2


def test_extract_news_themes_empty():
    assert extract_news_themes([]) == []
    assert extract_news_themes(["totally unrelated headline"]) == []


# ── rule summary ──────────────────────────────────────────────────────────────

def test_summary_bullish_headline_and_upside():
    s = build_rule_summary(detail(buy=59, hold=2, sell=1, avg_target=300, cur=250))
    assert "bullish" in s.headline.lower()
    assert any("Buy" in r and "net" in r for r in s.reasons)
    # 300 vs 250 ≈ +20% upside
    assert any("+20% upside" in r for r in s.reasons)
    assert s.source == "rule"


def test_summary_bearish():
    s = build_rule_summary(detail(buy=2, hold=4, sell=15, avg_target=80, cur=100))
    assert "bearish" in s.headline.lower()
    assert any("downside" in r for r in s.reasons)


def test_summary_counts_revisions_and_example():
    named = [
        named_rec("UBS", note="UBS: reiterated Buy, PT raises $275 -> $280"),
        named_rec("Citi", action="sell", note="Citi: Buy -> Sell, PT lowers $300 -> $200"),
    ]
    s = build_rule_summary(detail(named=named))
    assert any("raise(s)" in r and "cut(s)" in r for r in s.reasons)
    assert any(r.startswith("e.g.,") and "UBS" in r for r in s.reasons)


def test_summary_includes_segments_and_news_and_hit():
    s = build_rule_summary(detail(
        themes=["AI", "Data Center"],
        news=["AI demand drives data center growth"],
        status="hit",
    ))
    assert any("segments" in r.lower() for r in s.reasons)
    assert any("headlines center on" in r.lower() for r in s.reasons)
    assert any("already been reached" in r.lower() for r in s.reasons)


def test_summary_no_target_no_price():
    s = build_rule_summary(detail(avg_target=None, cur=None))
    assert s.headline                      # still produced
    assert not any("upside" in r for r in s.reasons)
