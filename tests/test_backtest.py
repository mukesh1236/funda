"""The what-if calculator and the scoreboard: hypothetical returns from price history.

The rules worth pinning are the ones that make the numbers honest:
  - the scoreboard never uses a recommendation dated after the day it picks on,
  - it buys at the NEXT day's close, not the signal day's,
  - results sit next to simply holding the index over the same dates,
  - and a window that has not finished yet is never scored.
"""
import threading
import time
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.backtest as bt
import app.main as main_mod
from app.models import AnalystRecommendation

D0 = date(2026, 6, 1)


def daily(start, n, price=lambda i: 100.0):
    return [(start + timedelta(days=i), price(i)) for i in range(n)]


def rec(symbol, day, count=10, action="buy", source="yahoo"):
    return AnalystRecommendation(symbol=symbol, source=source, action=action, count=count,
                                 entry_date=day.isoformat())


# ── what if ──────────────────────────────────────────────────────────────────
def test_what_if_math_against_the_benchmark():
    stock = daily(D0, 31, lambda i: 100 + i * (20 / 30))        # 100 -> 120
    bench = daily(D0, 31, lambda i: 50 + i * (5 / 30))          # 50 -> 55
    r = bt.what_if("NVDA", 1000, D0, stock, bench, "SPY", "S&P 500 (SPY)")
    assert r.entry_date == D0.isoformat() and r.entry_price == 100.0
    assert r.shares == 10.0 and r.value_now == 1200.0 and r.gain == 200.0 and r.gain_pct == 20.0
    assert r.benchmark.value_now == 1100.0 and r.benchmark.gain_pct == 10.0
    assert r.excess_pct == 10.0 and r.days == 30
    assert r.series[0].value == 1000.0 and r.series[-1].value == 1200.0
    assert r.series[-1].benchmark == 1100.0


def test_a_non_trading_start_buys_on_the_next_close():
    closes = [(D0 + timedelta(days=i), 100.0 + i) for i in (0, 1, 4, 5, 6)]   # gap: no 2nd/3rd
    r = bt.what_if("X", 100, D0 + timedelta(days=2), closes)
    assert r.entry_date == (D0 + timedelta(days=4)).isoformat() and r.entry_price == 104.0


def test_what_if_refuses_what_it_cannot_answer():
    closes = daily(D0, 10)
    with pytest.raises(bt.WhatIfError, match="too recent"):
        bt.what_if("X", 100, D0 + timedelta(days=9), closes)       # no later trading day
    with pytest.raises(bt.WhatIfError, match="too recent"):
        bt.what_if("X", 100, D0 + timedelta(days=60), closes)
    with pytest.raises(bt.WhatIfError, match="no price history"):
        bt.what_if("X", 100, D0, [])
    with pytest.raises(bt.WhatIfError, match="above zero"):
        bt.what_if("X", 0, D0, closes)


def test_missing_benchmark_leaves_it_out_rather_than_failing():
    r = bt.what_if("X", 100, D0, daily(D0, 5, lambda i: 100 + i), [], "SPY", "S&P")
    assert r.benchmark is None and r.excess_pct is None
    assert all(p.benchmark is None for p in r.series)


def test_the_chart_series_is_bounded_and_ends_on_the_last_day():
    closes = daily(D0, 400, lambda i: 100 + i)
    r = bt.what_if("X", 100, D0, closes)
    assert len(r.series) <= bt.MAX_SERIES_POINTS
    assert r.series[-1].date == r.last_date


def test_a_loss_is_reported_as_a_loss():
    r = bt.what_if("X", 1000, D0, daily(D0, 31, lambda i: 100 - i / 3))
    assert r.gain < 0 and r.gain_pct < 0 and r.value_now < 1000


# ── scoreboard: honesty rules ────────────────────────────────────────────────
def test_picks_use_only_recommendations_known_that_day():
    recs = {"A": [rec("A", D0, count=10), rec("A", D0 + timedelta(days=10), count=30)]}
    early = bt._picks_as_of(recs, D0 + timedelta(days=5), 10, 5)
    assert early == ["A"]
    # a stock whose first recommendation is dated later cannot be picked earlier
    recs["B"] = [rec("B", D0 + timedelta(days=20), count=40)]
    assert "B" not in bt._picks_as_of(recs, D0 + timedelta(days=19), 10, 5)
    assert bt._picks_as_of(recs, D0 + timedelta(days=20), 10, 5)[0] == "B"


def test_thinly_covered_and_net_negative_stocks_are_not_picked():
    recs = {"THIN": [rec("THIN", D0, count=3)],
            "BEAR": [rec("BEAR", D0, count=20, action="sell")],
            "OK": [rec("OK", D0, count=8)]}
    assert bt._picks_as_of(recs, D0, 10, 5) == ["OK"]


def test_the_purchase_is_at_the_next_close_not_the_signal_day_close():
    # The signal is on D0. D0 closes at 100, the next day jumps to 110, and 30 days
    # after D0 it is 121. Buying at the next close gives +10%, not +21%.
    prices = {0: 100.0, 1: 110.0}
    series = bt.Series(daily(D0, 40, lambda i: prices.get(i, 110.0 if i < 30 else 121.0)))
    assert bt._window_return(series, D0, 30) == pytest.approx(10.0)


def test_a_window_that_has_not_finished_is_not_scored():
    series = bt.Series(daily(D0, 20))
    assert bt._window_return(series, D0, 30) is None


def test_scoreboard_aggregates_picks_against_the_benchmark():
    syms = {s: [rec(s, D0 + timedelta(days=d), count=10 + i) for d in range(0, 60, 1)]
            for i, s in enumerate(["A", "B", "C", "D"])}
    closes = {"A": daily(D0, 120, lambda i: 100 + i),            # rising
              "B": daily(D0, 120, lambda i: 100 + i),
              "C": daily(D0, 120, lambda i: 100 + i),
              "D": daily(D0, 120, lambda i: 100 - i / 4)}        # falling
    bench = daily(D0, 120, lambda i: 100.0)                       # flat
    r = bt.compute_scoreboard("us", syms, closes, bench, horizons=(30,), top_n=4, min_analysts=5)
    h = r.horizons[0]
    assert r.status == "ready" and r.since == D0.isoformat() and r.benchmark_symbol == "SPY"
    assert h.snapshots > 0 and h.picks >= h.snapshots * bt.MIN_PICKS
    assert h.benchmark_avg_return_pct == 0.0
    assert h.avg_return_pct > 0 and h.avg_excess_pct == h.avg_return_pct
    assert h.pct_snapshots_beating_benchmark == 100.0
    assert 50 < h.pct_picks_up < 100                 # the falling stock drags it below 100


def test_too_little_history_says_so_instead_of_inventing_numbers():
    syms = {"A": [rec("A", D0, count=20)]}
    r = bt.compute_scoreboard("us", syms, {"A": daily(D0, 10)}, daily(D0, 10), horizons=(30, 60, 90))
    assert r.snapshots == 0 and all(h.snapshots == 0 and h.avg_return_pct is None for h in r.horizons)
    assert r.since == D0.isoformat()


def test_no_recommendations_at_all_is_not_an_error():
    r = bt.compute_scoreboard("us", {}, {})
    assert r.horizons == [] and "not enough recorded history" in " ".join(r.notes)


def test_every_scoreboard_states_its_limits():
    r = bt.compute_scoreboard("in", {"A": [rec("A", D0)]}, {"A": daily(D0, 5)})
    text = " ".join(r.notes).lower()
    assert "overlap" in text and "survivorship" in text and "hypothetical" in text
    assert r.benchmark_symbol == "NIFTYBEES.NS"


# ── endpoints ────────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    return TestClient(main_mod.app)


def _days_ago(n):
    return date.today() - timedelta(days=n)


def test_whatif_endpoint(client):
    start = _days_ago(60)
    closes = daily(start, 61, lambda i: 100 + i)
    with patch("app.sources.history.get_closes", side_effect=lambda s, d: closes):
        ok = client.get("/api/whatif", params={"symbol": "nvda", "start": start.isoformat(), "amount": 500})
        assert ok.status_code == 200
        body = ok.json()
        assert body["symbol"] == "NVDA" and body["gain_pct"] == pytest.approx(60.0)
        assert body["benchmark"]["symbol"] == "SPY"
        assert client.get("/api/whatif", params={"symbol": "RELIANCE.NS", "start": start.isoformat()}
                          ).json()["benchmark"]["symbol"] == "NIFTYBEES.NS"


def test_whatif_endpoint_validation(client):
    start = _days_ago(60).isoformat()
    with patch("app.sources.history.get_closes", return_value=[]):
        assert client.get("/api/whatif", params={"symbol": "ZZZZ", "start": start}).status_code == 404
    assert client.get("/api/whatif", params={"symbol": "bad symbol!", "start": start}).status_code == 422
    assert client.get("/api/whatif", params={"symbol": "NVDA", "start": "not-a-date"}).status_code == 422
    assert client.get("/api/whatif", params={"symbol": "NVDA", "start": start, "amount": 0}).status_code == 422
    assert client.get("/api/whatif", params={"symbol": "NVDA", "start": start, "amount": 10_000_001}).status_code == 422
    future = (date.today() + timedelta(days=5)).isoformat()
    assert client.get("/api/whatif", params={"symbol": "NVDA", "start": future}).status_code == 422
    assert client.get("/api/whatif", params={"symbol": "NVDA", "start": "1999-01-01"}).status_code == 422


def _reset():
    bt._SCORE_CACHE.clear(); bt._SCORE_FAILED.clear(); bt._SCORE_PENDING.clear()


def _wait(cond, secs=5):
    t0 = time.time()
    while time.time() - t0 < secs:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_scoreboard_builds_in_the_background_then_serves_the_cache(client):
    _reset()
    result = bt.compute_scoreboard("us", {"A": [rec("A", D0)]}, {"A": daily(D0, 5)})
    with patch.object(bt, "_build_scoreboard", return_value=result) as build:
        first = client.get("/api/scoreboard", params={"market": "us"}).json()
        assert first["status"] == "computing"
        assert _wait(lambda: "us" in bt._SCORE_CACHE)
        second = client.get("/api/scoreboard", params={"market": "us"}).json()
        assert second["status"] == "ready" and build.call_count == 1
    _reset()


def test_only_one_build_runs_at_a_time():
    _reset()
    gate = threading.Event()
    calls = []

    def slow(store, market):
        calls.append(market)
        gate.wait(2)
        return bt.compute_scoreboard("us", {"A": [rec("A", D0)]}, {"A": daily(D0, 5)})

    with patch.object(bt, "_build_scoreboard", side_effect=slow):
        for _ in range(5):
            assert bt.start_scoreboard_build(None, "us").status == "computing"
        gate.set()
        assert _wait(lambda: "us" in bt._SCORE_CACHE)
    assert calls == ["us"]
    _reset()


def test_a_failed_build_backs_off_instead_of_retrying_every_poll():
    _reset()
    with patch.object(bt, "_build_scoreboard", side_effect=RuntimeError("yahoo down")) as build:
        bt.start_scoreboard_build(None, "us")
        assert _wait(lambda: "us" in bt._SCORE_FAILED)
        again = bt.start_scoreboard_build(None, "us")
        assert again.status == "unavailable" and build.call_count == 1
        assert _wait(lambda: not bt._SCORE_PENDING)
    _reset()


def test_scoreboard_endpoint_validates_the_market(client):
    assert client.get("/api/scoreboard", params={"market": "uk"}).status_code == 422
