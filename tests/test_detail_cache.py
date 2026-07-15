"""build_detail() aggregate caching: a repeat detail lookup for the same
symbol must skip the network fetches entirely (cache hit), an unresolved
symbol must never be memorized as missing, and 'Refresh now' must bust the
cache so newly collected data shows up immediately."""
from datetime import date
from unittest.mock import patch

import app.service as service_mod
from app.models import AnalystRecommendation
from app.store import RecommendationStore


def _seeded_store(tmp_path) -> RecommendationStore:
    store = RecommendationStore(str(tmp_path / "test.db"))
    store.add_recommendation(AnalystRecommendation(
        symbol="NVDA", source="yahoo", action="buy", count=10,
        entry_date=date.today().isoformat(),
    ))
    return store


def _patched_sources():
    return [
        patch("app.service.get_news", return_value=[]),
        patch("app.service.fetch_ownership", return_value={}),
        patch("app.service.fetch_fundamentals", return_value={}),
        patch("app.sources.sec_insider.fetch_insider_trades", return_value=[]),
    ]


def test_second_call_is_a_cache_hit_no_network(tmp_path):
    service_mod._DETAIL_CACHE.clear()
    store = _seeded_store(tmp_path)
    patches = _patched_sources()
    mocks = [p.start() for p in patches]
    try:
        d1 = service_mod.build_detail(store, "NVDA")
        d2 = service_mod.build_detail(store, "NVDA")
    finally:
        for p in patches:
            p.stop()

    assert d1 is not None and d2 is not None
    assert d1 is d2   # same cached object, not just equal
    for m in mocks:
        assert m.call_count == 1   # NOT called again on the second lookup


def test_unresolved_symbol_is_never_cached(tmp_path):
    service_mod._DETAIL_CACHE.clear()
    store = _seeded_store(tmp_path)   # NVDA seeded, XXNOPE is not

    assert service_mod.build_detail(store, "XXNOPE") is None
    assert service_mod.build_detail(store, "XXNOPE") is None   # still None, not a stuck cache
    assert "XXNOPE" not in service_mod._DETAIL_CACHE


def test_cache_is_per_symbol(tmp_path):
    service_mod._DETAIL_CACHE.clear()
    store = _seeded_store(tmp_path)
    store.add_recommendation(AnalystRecommendation(
        symbol="AAPL", source="yahoo", action="hold", count=5,
        entry_date=date.today().isoformat(),
    ))
    patches = _patched_sources()
    for p in patches:
        p.start()
    try:
        nvda = service_mod.build_detail(store, "NVDA")
        aapl = service_mod.build_detail(store, "AAPL")
    finally:
        for p in patches:
            p.stop()
    assert nvda.symbol == "NVDA" and aapl.symbol == "AAPL"
    assert len(service_mod._DETAIL_CACHE) == 2


def test_refresh_invalidates_detail_and_overview_caches(tmp_path, monkeypatch):
    import app.main as main_mod

    service_mod._DETAIL_CACHE["NVDA"] = (0.0, object())
    service_mod._OVERVIEW_CACHE["NVDA"] = (0.0, object())
    monkeypatch.setattr(main_mod, "run_daily", lambda *a, **k: None)

    main_mod._run_daily_and_invalidate()

    assert service_mod._DETAIL_CACHE == {}
    assert service_mod._OVERVIEW_CACHE == {}
