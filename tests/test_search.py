"""Ticker search tests: fuzzy fallback when strict phrase-match finds
nothing (the reported "Coca-Cola doesn't come up" bug), and not caching
empty/failed results for the full hour."""
from unittest.mock import patch

import app.sources.search as search_mod

_KO_QUOTE = {"symbol": "KO", "shortname": "The Coca-Cola Company",
             "exchange": "NYQ", "fullExchangeName": "NYSE", "quoteType": "EQUITY"}


def _clear_cache():
    search_mod._CACHE.clear()


def test_fuzzy_fallback_finds_company_strict_match_misses():
    """Strict phrase-match returns nothing (e.g. 'coca cola' vs Yahoo's
    canonical 'The Coca-Cola Company'); fuzzy search must still find KO."""
    _clear_cache()
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        import httpx
        quotes = [] if params.get("enableFuzzyQuery") == "false" else [_KO_QUOTE]
        return httpx.Response(200, json={"quotes": quotes}, request=httpx.Request("GET", url))

    with patch("httpx.Client.get", side_effect=fake_get):
        results = search_mod.search_tickers("coca cola", market="us")

    assert [r["symbol"] for r in results] == ["KO"]
    assert len(calls) == 2   # strict attempt, then fuzzy fallback
    assert calls[0]["enableFuzzyQuery"] == "false"
    assert calls[1]["enableFuzzyQuery"] == "true"


def test_strict_match_hit_skips_fuzzy_call():
    _clear_cache()
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        import httpx
        return httpx.Response(200, json={"quotes": [_KO_QUOTE]}, request=httpx.Request("GET", url))

    with patch("httpx.Client.get", side_effect=fake_get):
        results = search_mod.search_tickers("KO", market="us")

    assert [r["symbol"] for r in results] == ["KO"]
    assert len(calls) == 1   # strict match hit — no fuzzy retry needed


def test_empty_result_is_not_cached_for_an_hour():
    """A transient failure or genuine zero-match must not be memorized for
    everyone for 60 minutes — the next search should retry, not short-circuit."""
    _clear_cache()
    call_count = {"n": 0}

    def fake_get(url, params=None, **kw):
        call_count["n"] += 1
        import httpx
        return httpx.Response(200, json={"quotes": []}, request=httpx.Request("GET", url))

    with patch("httpx.Client.get", side_effect=fake_get):
        r1 = search_mod.search_tickers("zzznosuchcompanyzzz", market="us")
        r2 = search_mod.search_tickers("zzznosuchcompanyzzz", market="us")

    assert r1 == [] and r2 == []
    assert "zzznosuchcompanyzzz:us" not in search_mod._CACHE
    # Two lookups (strict+fuzzy) per call, two calls made -> 4 network hits,
    # proving the second search_tickers() call was NOT served from cache.
    assert call_count["n"] == 4


def test_non_empty_result_is_cached():
    _clear_cache()
    call_count = {"n": 0}

    def fake_get(url, params=None, **kw):
        call_count["n"] += 1
        import httpx
        return httpx.Response(200, json={"quotes": [_KO_QUOTE]}, request=httpx.Request("GET", url))

    with patch("httpx.Client.get", side_effect=fake_get):
        search_mod.search_tickers("KO", market="us")
        search_mod.search_tickers("KO", market="us")

    assert call_count["n"] == 1   # second call served entirely from cache
