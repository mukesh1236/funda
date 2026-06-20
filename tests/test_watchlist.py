"""Tests for watchlist store ops and daily-variation computation."""
import pytest

from app.service import compute_daily_points
from app.store import RecommendationStore


@pytest.fixture
def store(tmp_path):
    return RecommendationStore(str(tmp_path / "wl.db"))


@pytest.fixture
def uid(store):
    return store.create_user("a@example.com", "x", "User A")


# ── store ─────────────────────────────────────────────────────────────────────

def test_add_and_list(store, uid):
    assert store.add_watchlist(uid, "AAPL", "My Watchlist", "2026-06-15", 200.0, "Apple Inc.")
    items = store.list_watchlist(uid, "My Watchlist")
    assert len(items) == 1
    assert items[0]["symbol"] == "AAPL" and items[0]["pin_price"] == 200.0


def test_add_is_idempotent_preserves_pin(store, uid):
    store.add_watchlist(uid, "AAPL", "My Watchlist", "2026-06-15", 200.0, "Apple Inc.")
    # Re-adding does NOT overwrite the original pin date/price.
    assert store.add_watchlist(uid, "AAPL", "My Watchlist", "2026-06-20", 250.0, "Apple Inc.") is False
    item = store.list_watchlist(uid, "My Watchlist")[0]
    assert item["pin_price"] == 200.0 and item["pin_date"] == "2026-06-15"


def test_same_symbol_different_groups(store, uid):
    store.add_watchlist(uid, "AAPL", "Tech", "2026-06-15", 200.0, None)
    store.add_watchlist(uid, "AAPL", "Long term", "2026-06-15", 200.0, None)
    assert len(store.list_watchlist(uid)) == 2
    assert set(store.watchlist_groups(uid)) == {"Tech", "Long term"}


def test_remove(store, uid):
    store.add_watchlist(uid, "AAPL", "My Watchlist", "2026-06-15", 200.0, None)
    assert store.remove_watchlist(uid, "AAPL", "My Watchlist") is True
    assert store.list_watchlist(uid, "My Watchlist") == []
    assert store.remove_watchlist(uid, "AAPL", "My Watchlist") is False  # already gone


def test_watchlist_is_user_scoped(store, uid):
    other = store.create_user("b@example.com", "y", "User B")
    store.add_watchlist(uid, "AAPL", "My Watchlist", "2026-06-15", 200.0, None)
    # User B sees nothing of User A's pins.
    assert store.list_watchlist(other) == []
    # ...and can pin the same symbol independently.
    assert store.add_watchlist(other, "AAPL", "My Watchlist", "2026-06-16", 210.0, None)
    assert len(store.list_watchlist(uid)) == 1
    assert len(store.list_watchlist(other)) == 1


def test_create_user_duplicate_email_raises(store):
    store.create_user("dup@example.com", "h", "First")
    with pytest.raises(ValueError):
        store.create_user("DUP@example.com", "h2", "Second")  # case-insensitive


# ── daily variation ───────────────────────────────────────────────────────────

def test_compute_daily_points_day_over_day():
    pts = [
        {"date": "2026-06-15", "close": 100.0},
        {"date": "2026-06-16", "close": 110.0},   # +10%
        {"date": "2026-06-17", "close": 99.0},     # -10%
    ]
    out = compute_daily_points(pts)
    assert out[0].change_pct is None              # first day has no prior
    assert out[1].change_pct == 10.0
    assert out[2].change_pct == -10.0
    assert [p.close for p in out] == [100.0, 110.0, 99.0]


def test_compute_daily_points_single_and_empty():
    assert compute_daily_points([]) == []
    single = compute_daily_points([{"date": "2026-06-15", "close": 50.0}])
    assert len(single) == 1 and single[0].change_pct is None


def test_build_watchlist_since_pin(store, uid, monkeypatch):
    import app.service as svc
    store.add_watchlist(uid, "AAPL", "My Watchlist", "2026-06-15", 100.0, "Apple Inc.")
    # Mock price history: includes a pre-pin day that must be excluded.
    monkeypatch.setattr(svc, "get_price_history", lambda sym, since=None: [
        {"date": "2026-06-14", "close": 90.0},    # before pin → excluded
        {"date": "2026-06-15", "close": 100.0},
        {"date": "2026-06-16", "close": 120.0},
    ])
    res = svc.build_watchlist(store, uid, group="My Watchlist")
    it = res.items[0]
    assert it.pin_price == 100.0
    assert it.current_price == 120.0
    assert it.change_since_pin_pct == 20.0        # 100 → 120
    assert [p.date for p in it.daily] == ["2026-06-15", "2026-06-16"]  # pre-pin dropped
    assert it.day_change_pct == 20.0
