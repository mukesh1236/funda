"""The stock detail is served in two halves so the UI never waits on the network
for the part that lives in our own database.

core   = consensus + analyst calls + rule summary: SQLite only, no network, no LLM.
extras = fundamentals, ownership, news, insider trades (+ a refreshed summary when
         news themes or an LLM narrative change it): network, cached.
"""
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
import app.service as service
import app.summarize as summarize
from app.models import AnalystRecommendation
from app.store import RecommendationStore

HEADLINES = [{"title": "AI chip demand lifts guidance", "publisher": "Wire", "url": "https://x.test/1"}]


@pytest.fixture
def store(tmp_path):
    s = RecommendationStore(str(tmp_path / "t.db"))
    s.add_recommendation(AnalystRecommendation(
        symbol="NVDA", source="yahoo", action="buy", count=10, entry_date=date.today().isoformat()))
    s.add_recommendation(AnalystRecommendation(
        symbol="NVDA", source="yahoo_upgrades", action="buy", firm="Citi",
        note="Citi raised PT to $200", target_price=200.0, entry_date=date.today().isoformat()))
    service._DETAIL_CACHE.clear()
    service._EXTRAS_CACHE.clear()
    summarize._SUMMARY_CACHE.clear()   # build_summary memoises per symbol per day
    return s


def _net(news=None, fund=None):
    return [
        patch("app.service.get_news", return_value=HEADLINES if news is None else news),
        patch("app.service.fetch_ownership", return_value={"inst_pct": 70.0}),
        patch("app.service.fetch_fundamentals", return_value={"pe_ratio": 30.0} if fund is None else fund),
        patch("app.sources.sec_insider.fetch_insider_trades", return_value=[]),
    ]


def test_core_makes_no_network_or_llm_call(store):
    boom = AssertionError("core must not touch the network")
    with patch("app.service.get_news", side_effect=boom), \
         patch("app.service.fetch_ownership", side_effect=boom), \
         patch("app.service.fetch_fundamentals", side_effect=boom), \
         patch("app.sources.sec_insider.fetch_insider_trades", side_effect=boom), \
         patch("app.summarize.generate_narrative", side_effect=boom):
        core = service.build_detail_core(store, "NVDA")
    assert core.summary and core.summary.headline
    assert core.consensus.buy_count > 0
    assert any(r.firm == "Citi" for r in core.recommendations)
    # the slow half is empty, not missing
    assert core.news == [] and core.fundamentals is None and core.ownership is None


def test_core_summary_has_no_news_reason_extras_adds_it(store):
    core = service.build_detail_core(store, "NVDA")
    assert not any("headlines" in r.lower() for r in core.summary.reasons)
    ps = _net()
    [p.start() for p in ps]
    try:
        extras = service.build_detail_extras(store, "NVDA")
    finally:
        [p.stop() for p in ps]
    assert extras.fundamentals.pe_ratio == 30.0
    assert extras.ownership.inst_pct == 70.0
    assert extras.news[0].title == HEADLINES[0]["title"]
    assert extras.summary is not None
    assert any("headlines" in r.lower() for r in extras.summary.reasons)


def test_extras_summary_is_none_when_nothing_new(store):
    ps = _net(news=[])
    [p.start() for p in ps]
    try:
        extras = service.build_detail_extras(store, "NVDA")
    finally:
        [p.stop() for p in ps]
    assert extras.summary is None   # the instant summary stands


def test_merged_detail_equals_core_plus_extras(store):
    """build_detail (used by chat and any whole-detail caller) must match the halves."""
    ps = _net()
    [p.start() for p in ps]
    try:
        whole = service.build_detail(store, "NVDA")
        core = service.build_detail_core(store, "NVDA")
        extras = service.build_detail_extras(store, "NVDA")
    finally:
        [p.stop() for p in ps]
    assert whole.news == extras.news
    assert whole.fundamentals == extras.fundamentals
    assert whole.ownership == extras.ownership
    assert whole.summary == extras.summary
    assert whole.consensus == core.consensus
    assert whole.recommendations == core.recommendations


def test_extras_are_cached_and_bounded(store):
    ps = _net()
    mocks = [p.start() for p in ps]
    try:
        service.build_detail_extras(store, "NVDA")
        service.build_detail_extras(store, "NVDA")
        assert mocks[0].call_count == 1   # second call served from cache
    finally:
        [p.stop() for p in ps]
    assert service._EXTRAS_CACHE.maxsize > 0


def test_untracked_symbol_is_none_and_never_hits_the_network(store):
    boom = AssertionError("must not fetch for an untracked symbol")
    with patch("app.service.get_news", side_effect=boom), patch("app.service.fetch_fundamentals", side_effect=boom):
        assert service.build_detail_core(store, "ZZZZ") is None
        assert service.build_detail_extras(store, "ZZZZ") is None


def test_endpoints(store):
    ps = _net()
    [p.start() for p in ps]
    try:
        with patch.object(main_mod, "store", store):
            c = TestClient(main_mod.app)
            core = c.get("/api/recommendations/NVDA/core")
            extras = c.get("/api/recommendations/NVDA/extras")
            assert core.status_code == 200 and core.json()["summary"]["headline"]
            assert extras.status_code == 200 and extras.json()["fundamentals"]["pe_ratio"] == 30.0
            assert c.get("/api/recommendations/ZZZZ/core").status_code == 404
            assert c.get("/api/recommendations/ZZZZ/extras").status_code == 404
            assert c.get("/api/recommendations/NVDA").status_code == 200   # whole detail unchanged
    finally:
        [p.stop() for p in ps]
