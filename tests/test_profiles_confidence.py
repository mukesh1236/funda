"""Tests for trailing-return computation and target-hit confidence."""
from datetime import date, timedelta

import pytest

from app.analytics import estimate_confidence
from app.models import ConsensusOut
from app.sources.profiles import returns_from_closes


# ── returns_from_closes ───────────────────────────────────────────────────────

def _series(n_days, start=100.0, step=0.1):
    today = date(2026, 6, 15)
    days = [today - timedelta(days=(n_days - 1 - i)) for i in range(n_days)]
    closes = [start + i * step for i in range(n_days)]  # steadily rising
    return days, closes, today


def test_returns_rising_series_all_positive_and_ordered():
    days, closes, today = _series(400)
    r = returns_from_closes(days, closes, today=today)
    assert all(v is not None for v in r.values())
    # Longer windows look further back on a rising series → larger gains.
    assert r["twelve_month"] > r["six_month"] > r["three_month"] > r["one_month"] > 0


def test_returns_insufficient_history_is_none():
    days, closes, today = _series(5)   # only 5 days — no window reaches back
    r = returns_from_closes(days, closes, today=today)
    assert all(v is None for v in r.values())


def test_returns_empty_and_mismatched():
    assert returns_from_closes([], []) == {
        "one_month": None, "three_month": None, "six_month": None, "twelve_month": None}
    assert all(v is None for v in returns_from_closes([date(2026, 1, 1)], []).values())


def test_returns_negative_when_falling():
    today = date(2026, 6, 15)
    days = [today - timedelta(days=(99 - i)) for i in range(100)]
    closes = [200.0 - i for i in range(100)]   # falling
    r = returns_from_closes(days, closes, today=today)
    assert r["one_month"] < 0 and r["three_month"] < 0


# ── estimate_confidence ───────────────────────────────────────────────────────

def con(buy, hold, sell, target):
    return ConsensusOut(symbol="X", buy_count=buy, hold_count=hold, sell_count=sell,
                        total_count=buy + hold + sell, consensus_score=buy - sell,
                        avg_target=target)


def test_confidence_high_when_close_and_strong():
    c = con(58, 1, 1, target=110)
    conf = estimate_confidence(c, current_price=105, ret_3m=12, hit_rate=None, resolved=0)
    assert conf is not None
    assert conf.label == "High"
    assert conf.components["proximity"] > 80


def test_confidence_low_when_far_and_weak():
    c = con(10, 5, 8, target=200)
    conf = estimate_confidence(c, current_price=100, ret_3m=-20, hit_rate=None, resolved=0)
    assert conf.label == "Low"
    assert conf.components["proximity"] == 0  # 100% move needed → clamped to 0


def test_confidence_already_above_target_is_max_proximity():
    c = con(40, 2, 1, target=90)
    conf = estimate_confidence(c, current_price=100, ret_3m=5, hit_rate=None, resolved=0)
    assert conf.components["proximity"] == 100


def test_confidence_uses_track_record():
    c = con(30, 2, 2, target=120)
    good = estimate_confidence(c, 110, 5, hit_rate=1.0, resolved=10)
    poor = estimate_confidence(c, 110, 5, hit_rate=0.0, resolved=10)
    assert good.score > poor.score
    assert good.components["track"] == 100 and poor.components["track"] == 0


def test_confidence_none_for_hold_or_missing_target():
    assert estimate_confidence(con(5, 50, 5, target=100), 100, 0, None, 0) is None  # net 0
    assert estimate_confidence(con(50, 1, 1, target=None), 100, 0, None, 0) is None
    assert estimate_confidence(con(50, 1, 1, target=100), None, 0, None, 0) is None


def test_confidence_sell_direction():
    # Bearish consensus, price above a lower target → some move needed down.
    c = con(2, 3, 20, target=80)
    conf = estimate_confidence(c, current_price=100, ret_3m=-10, hit_rate=None, resolved=0)
    assert conf is not None
    # 20% drop needed → proximity 100 - 40 = 60
    assert conf.components["proximity"] == 60
