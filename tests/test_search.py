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


# ── mutual funds in search ────────────────────────────────────────────────────
# Mutual funds were filtered out entirely, so VFIAX/FXAIX never appeared. The
# fact sheet lives on the fund card, so "search a fund, read its fact sheet"
# was impossible until this was unblocked.

_VFIAX = {"symbol": "VFIAX", "shortname": "Vanguard 500 Index Fund Admiral",
          "exchange": "NAS", "quoteType": "MUTUALFUND"}
_VOO = {"symbol": "VOO", "shortname": "Vanguard S&P 500 ETF",
        "exchange": "PCX", "quoteType": "ETF"}
_AAPL = {"symbol": "AAPL", "shortname": "Apple Inc.",
         "exchange": "NMS", "quoteType": "EQUITY"}
_FUT = {"symbol": "ESZ4", "shortname": "E-mini S&P", "quoteType": "FUTURE"}


def _fake_quotes(quotes):
    def fake_get(url, params=None, **kw):
        import httpx
        return httpx.Response(200, json={"quotes": quotes},
                              request=httpx.Request("GET", url))
    return fake_get


def test_mutual_funds_are_returned():
    _clear_cache()
    with patch("httpx.Client.get", side_effect=_fake_quotes([_VFIAX])):
        results = search_mod.search_tickers("vanguard 500", market="us")
    assert [r["symbol"] for r in results] == ["VFIAX"]


def test_every_result_carries_a_type():
    """The frontend routes on this; without it a fund opens the stock view."""
    _clear_cache()
    with patch("httpx.Client.get", side_effect=_fake_quotes([_AAPL, _VOO, _VFIAX])):
        results = search_mod.search_tickers("x", market="us")
    types = {r["symbol"]: r["type"] for r in results}
    assert types == {"AAPL": "stock", "VOO": "etf", "VFIAX": "fund"}


def test_stocks_and_etfs_rank_above_mutual_funds():
    """A query like 'fidelity' otherwise returns a dozen share classes of the
    same fund and buries the things most people meant."""
    _clear_cache()
    quotes = [_VFIAX, dict(_VFIAX, symbol="VFINX"), _AAPL, _VOO]
    with patch("httpx.Client.get", side_effect=_fake_quotes(quotes)):
        results = search_mod.search_tickers("vanguard", market="us", limit=4)
    assert [r["symbol"] for r in results[:2]] == ["AAPL", "VOO"]


def test_include_funds_false_still_filters_them():
    """The watchlist pins an entry price, which a once-daily NAV can't support."""
    _clear_cache()
    with patch("httpx.Client.get", side_effect=_fake_quotes([_VFIAX, _AAPL])):
        results = search_mod.search_tickers("x", market="us", include_funds=False)
    assert [r["symbol"] for r in results] == ["AAPL"]


def test_include_funds_variants_cached_separately():
    """Otherwise the watchlist would serve the global search's fund results."""
    _clear_cache()
    with patch("httpx.Client.get", side_effect=_fake_quotes([_VFIAX, _AAPL])):
        with_funds = search_mod.search_tickers("x", market="us", include_funds=True)
        without = search_mod.search_tickers("x", market="us", include_funds=False)
    assert "VFIAX" in [r["symbol"] for r in with_funds]
    assert "VFIAX" not in [r["symbol"] for r in without]


def test_futures_and_options_still_excluded():
    _clear_cache()
    with patch("httpx.Client.get", side_effect=_fake_quotes([_FUT, _AAPL])):
        results = search_mod.search_tickers("es", market="us")
    assert [r["symbol"] for r in results] == ["AAPL"]
