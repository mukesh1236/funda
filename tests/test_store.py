"""Tests for the SQLite store: insert, dedupe, queries, outcomes."""
from datetime import date

import pytest

from app.analytics import evaluate_outcome
from app.models import AnalystRecommendation
from app.store import RecommendationStore


@pytest.fixture
def store(tmp_path):
    return RecommendationStore(str(tmp_path / "test.db"))


def rec(symbol="AAPL", action="buy", count=1, target=100.0, source="finnhub",
        firm=None, entry_date=None):
    return AnalystRecommendation(
        symbol=symbol, source=source, action=action, count=count,
        target_price=target, firm=firm,
        entry_date=entry_date or date.today().isoformat(),
    )


def test_add_returns_id(store):
    rid = store.add_recommendation(rec())
    assert isinstance(rid, int)


def test_same_day_rerun_dedupes(store):
    r = rec(action="buy")
    assert store.add_recommendation(r) is not None
    # Identical key on a second run → ignored.
    assert store.add_recommendation(rec(action="buy")) is None
    assert len(store.list_for_symbol("AAPL")) == 1


def test_different_action_not_deduped(store):
    store.add_recommendation(rec(action="buy"))
    store.add_recommendation(rec(action="sell"))
    assert len(store.list_for_symbol("AAPL")) == 2


def test_count_roundtrip(store):
    store.add_recommendation(rec(action="buy", count=25))
    got = store.list_for_symbol("AAPL")[0]
    assert got.count == 25


def test_list_recent(store):
    store.add_recommendation(rec(symbol="AAPL"))
    store.add_recommendation(rec(symbol="MSFT"))
    assert {r.symbol for r in store.list_recent(days=1)} == {"AAPL", "MSFT"}


def test_pending_and_outcome_flow(store):
    rid = store.add_recommendation(rec(action="buy", target=100))
    # Initially pending (no outcome row).
    assert any(r.rec_id == rid for r in store.pending_recommendations())

    r = store.list_for_symbol("AAPL")[0]
    o = evaluate_outcome(r, current_price=150)  # hit
    store.upsert_outcome(o)

    assert store.latest_outcome("AAPL")["status"] == "hit"
    # Resolved → no longer pending.
    assert not any(x.rec_id == rid for x in store.pending_recommendations())


def test_outcome_upsert_overwrites(store):
    rid = store.add_recommendation(rec(action="buy", target=100))
    r = store.list_for_symbol("AAPL")[0]
    store.upsert_outcome(evaluate_outcome(r, current_price=90))   # pending
    store.upsert_outcome(evaluate_outcome(r, current_price=150))  # hit
    assert store.latest_outcome("AAPL")["status"] == "hit"
    assert store.outcome_counts("AAPL") == {"hit": 1}
