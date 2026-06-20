"""Unit tests for consensus aggregation and outcome validation."""
from datetime import date, timedelta

import pytest

from app.analytics import compute_consensus, evaluate_outcome
from app.models import AnalystRecommendation


def rec(symbol="AAPL", action="buy", count=1, target=None, source="finnhub",
        firm=None, entry_date="2026-06-14"):
    return AnalystRecommendation(
        symbol=symbol, source=source, action=action, count=count,
        target_price=target, firm=firm, entry_date=entry_date,
    )


# ── compute_consensus ─────────────────────────────────────────────────────────

class TestConsensus:
    def test_empty(self):
        assert compute_consensus([]) is None

    def test_single_buy(self):
        c = compute_consensus([rec(action="buy")])
        assert c.buy_count == 1 and c.sell_count == 0
        assert c.consensus_score == 1
        assert c.total_count == 1

    def test_counts_are_summed_not_rows(self):
        # An aggregate Finnhub row of 25 buys + a TipRanks sell (both counting).
        recs = [
            rec(action="buy", count=25, source="finnhub"),
            rec(action="sell", count=1, source="tipranks"),
        ]
        c = compute_consensus(recs)
        assert c.buy_count == 25
        assert c.sell_count == 1
        assert c.consensus_score == 24
        assert c.total_count == 26
        assert c.sources == ["finnhub", "tipranks"]

    def test_multiple_recommenders_add_up(self):
        recs = [rec(action="buy"), rec(action="buy", firm="X"), rec(action="hold")]
        c = compute_consensus(recs)
        assert c.buy_count == 2
        assert c.hold_count == 1
        assert c.consensus_score == 2

    def test_avg_target_ignores_missing(self):
        recs = [rec(action="buy", target=100), rec(action="buy", target=200, firm="X"),
                rec(action="hold")]
        c = compute_consensus(recs)
        assert c.avg_target == 150.0

    def test_avg_target_none_when_no_targets(self):
        assert compute_consensus([rec(action="hold")]).avg_target is None

    def test_latest_entry_date(self):
        recs = [rec(entry_date="2026-06-10"), rec(entry_date="2026-06-14", firm="X")]
        assert compute_consensus(recs).latest_entry_date == "2026-06-14"


# ── evaluate_outcome ──────────────────────────────────────────────────────────

class TestOutcome:
    today = date(2026, 6, 14)

    def test_buy_hit(self):
        r = rec(action="buy", target=100, entry_date="2026-06-01")
        o = evaluate_outcome(r, current_price=120, today=self.today)
        assert o.status == "hit"
        assert o.pct_to_target == 20.0

    def test_buy_pending(self):
        r = rec(action="buy", target=100, entry_date="2026-06-01")
        o = evaluate_outcome(r, current_price=90, today=self.today)
        assert o.status == "pending"
        assert o.pct_to_target == -10.0

    def test_buy_missed_after_horizon(self):
        r = rec(action="buy", target=100, entry_date="2025-01-01")
        o = evaluate_outcome(r, current_price=90, horizon_days=365, today=self.today)
        assert o.status == "missed"

    def test_sell_hit(self):
        r = rec(action="sell", target=80, entry_date="2026-06-01")
        o = evaluate_outcome(r, current_price=70, today=self.today)
        assert o.status == "hit"

    def test_sell_pending(self):
        r = rec(action="sell", target=80, entry_date="2026-06-01")
        o = evaluate_outcome(r, current_price=90, today=self.today)
        assert o.status == "pending"

    def test_hold_not_directional_pending(self):
        r = rec(action="hold", target=100, entry_date="2026-06-01")
        o = evaluate_outcome(r, current_price=120, today=self.today)
        assert o.status == "pending"

    def test_no_target_pending_then_expired(self):
        r1 = rec(action="buy", target=None, entry_date="2026-06-01")
        assert evaluate_outcome(r1, 100, today=self.today).status == "pending"
        r2 = rec(action="buy", target=None, entry_date="2024-01-01")
        assert evaluate_outcome(r2, 100, horizon_days=365, today=self.today).status == "expired"

    def test_days_held(self):
        r = rec(action="buy", target=100, entry_date="2026-06-04")
        o = evaluate_outcome(r, current_price=90, today=self.today)
        assert o.days_held == 10

    def test_unparseable_date_zero_days(self):
        r = rec(action="buy", target=100, entry_date="not-a-date")
        o = evaluate_outcome(r, current_price=90, today=self.today)
        assert o.days_held == 0
