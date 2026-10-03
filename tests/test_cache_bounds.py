"""Every in-process cache must be bounded.

This bug class has now shipped twice. `app/fund_rag.py` carries a comment about
the first occurrence ("the previous unbounded dict grew for the process
lifetime — an OOM on a small container"), and three plain dicts in
`app/service.py` repeated it: they checked their TTL on read but never deleted
expired entries, so they only ever grew. On a host that bills resident memory
that is a slow, silent, recurring charge — production RSS climbed ~32 MB/day.

A plain `dict` used as a cache is the defect. These tests assert the container
type enforces a ceiling, because that is the property that was missing — not
whether any particular lookup hits.
"""
import pytest

from cachetools import Cache

import app.backtest as backtest
import app.fund_rag as rag
import app.service as service
import app.sources.history as history


CACHES = [
    ("service._DAY_CHANGE_CACHE", service._DAY_CHANGE_CACHE),
    ("service._DETAIL_CACHE", service._DETAIL_CACHE),
    ("service._EXTRAS_CACHE", service._EXTRAS_CACHE),
    ("history._CLOSES_CACHE", history._CLOSES_CACHE),
    ("backtest._SCORE_CACHE", backtest._SCORE_CACHE),
    ("backtest._SCORE_FAILED", backtest._SCORE_FAILED),
    ("service._OVERVIEW_CACHE", service._OVERVIEW_CACHE),
    ("fund_rag._cache", rag._cache),
]


@pytest.mark.parametrize("name,cache", CACHES, ids=[c[0] for c in CACHES])
def test_cache_is_bounded(name, cache):
    """A bare dict has no maxsize and never evicts — that is the bug."""
    assert isinstance(cache, Cache), f"{name} must be a cachetools Cache, not a plain dict"
    assert cache.maxsize > 0, f"{name} has no size ceiling"


@pytest.mark.parametrize("name,cache", CACHES, ids=[c[0] for c in CACHES])
def test_cache_evicts_past_its_ceiling(name, cache):
    """Writing well past maxsize must not grow the cache without limit."""
    cache.clear()
    try:
        for i in range(cache.maxsize + 50):
            cache[f"__bound_probe_{i}"] = i
        assert len(cache) <= cache.maxsize, (
            f"{name} grew to {len(cache)} with a ceiling of {cache.maxsize}")
    finally:
        cache.clear()


def test_day_change_cache_survives_many_distinct_symbol_sets():
    """_DAY_CHANGE_CACHE is keyed by a frozenset of symbols, so every distinct
    combination is a distinct key. As a plain dict that was unbounded growth
    driven by ordinary traffic — the scheduler alone produced ~96 new keys a day."""
    service._DAY_CHANGE_CACHE.clear()
    try:
        for i in range(500):
            service._DAY_CHANGE_CACHE[frozenset({f"SYM{i}", f"SYM{i + 1}"})] = {"X": 1.0}
        assert len(service._DAY_CHANGE_CACHE) <= service._DAY_CHANGE_CACHE.maxsize
    finally:
        service._DAY_CHANGE_CACHE.clear()


def test_no_plain_dict_caches_left_in_service():
    """Guards the specific regression: a module-level name ending in _CACHE that
    is a plain dict is exactly what this fix removed."""
    offenders = [
        name for name in dir(service)
        if name.endswith("_CACHE") and isinstance(getattr(service, name), dict)
        and not isinstance(getattr(service, name), Cache)
    ]
    assert not offenders, f"unbounded dict caches reintroduced: {offenders}"
