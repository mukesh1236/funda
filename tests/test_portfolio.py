"""Portfolio X-Ray maths.

Overlap is the number this feature exists to state, and it is stated
confidently, so it has to be right for cases whose answer is known by
inspection: identical funds are 100% overlapped, disjoint funds are 0%, and
a fund sharing exactly half its weight is 50%.

The two honesty rules are tested as hard as the arithmetic: no currency figure
may appear unless the user actually entered amounts, and partial (top-10 only)
holdings must be declared rather than reported as a precise overlap.
"""
import pytest

from app.portfolio import FundInput, build_xray

AAPL = {"ticker": "AAPL", "name": "Apple Inc", "weight": 50}
MSFT = {"ticker": "MSFT", "name": "Microsoft Corp", "weight": 50}
XOM = {"ticker": "XOM", "name": "Exxon Mobil", "weight": 50}
CVX = {"ticker": "CVX", "name": "Chevron Corp", "weight": 50}


def f(symbol, holdings, **kw):
    return FundInput(symbol=symbol, holdings=holdings, **kw)


class TestOverlap:

    def test_identical_funds_are_fully_overlapped(self):
        r = build_xray([f("A", [AAPL, MSFT]), f("B", [AAPL, MSFT])])
        assert r.overlap_pct == pytest.approx(100.0)

    def test_disjoint_funds_have_no_overlap(self):
        r = build_xray([f("A", [AAPL, MSFT]), f("B", [XOM, CVX])])
        assert r.overlap_pct == pytest.approx(0.0)
        assert r.duplicated == []

    def test_half_shared_is_half_overlapped(self):
        r = build_xray([f("A", [AAPL, MSFT]), f("B", [AAPL, XOM])])
        assert r.overlap_pct == pytest.approx(50.0)

    def test_single_fund_overlaps_nothing(self):
        r = build_xray([f("A", [AAPL, MSFT])])
        assert r.overlap_pct == pytest.approx(0.0)
        assert r.fund_count == 1

    def test_duplicated_names_are_ranked_and_attributed(self):
        r = build_xray([f("A", [AAPL, MSFT]), f("B", [AAPL, XOM])])
        assert [d["name"] for d in r.duplicated] == ["Apple Inc"]
        assert sorted(r.duplicated[0]["held_by"]) == ["A", "B"]

    def test_matches_by_name_when_ticker_is_missing(self):
        """N-PORT rows often have no ticker; the same company must still match."""
        a = [{"name": "Apple Inc.", "weight": 100}]
        b = [{"name": "Apple Inc", "weight": 100}]
        assert build_xray([f("A", a), f("B", b)]).overlap_pct == pytest.approx(100.0)


class TestWeightNormalisation:

    def test_weights_are_normalised_per_fund(self):
        """N-PORT pctVal doesn't reliably total 100 (rounding, cash, excluded
        asset classes). Funds must be normalised before being combined, or a
        partially-disclosed fund silently counts for less than its real share."""
        small = [{"ticker": "AAPL", "weight": 5}, {"ticker": "MSFT", "weight": 5}]
        r = build_xray([f("A", [AAPL, MSFT]), f("B", small)])
        assert r.overlap_pct == pytest.approx(100.0)

    def test_same_security_listed_twice_is_summed(self):
        dup = [{"ticker": "AAPL", "weight": 30}, {"ticker": "AAPL", "weight": 20},
               {"ticker": "MSFT", "weight": 50}]
        r = build_xray([f("A", dup), f("B", [AAPL, MSFT])])
        assert r.overlap_pct == pytest.approx(100.0)

    def test_zero_and_negative_weights_ignored(self):
        odd = [{"ticker": "AAPL", "weight": 100}, {"ticker": "JUNK", "weight": 0},
               {"ticker": "SHORT", "weight": -5}]
        r = build_xray([f("A", odd)])
        assert r.largest_position["ticker"] == "AAPL"
        assert r.largest_position["effective_weight_pct"] == pytest.approx(100.0)


class TestFundWeighting:

    def test_equal_weight_when_no_amounts(self):
        r = build_xray([f("A", [AAPL, MSFT], expense_ratio=0.10),
                        f("B", [XOM, CVX], expense_ratio=0.50)])
        assert r.amount_weighted is False
        assert r.blended_expense_ratio == pytest.approx(0.30)
        assert any("equal-sized" in n for n in r.notes)

    def test_amounts_shift_the_blend(self):
        r = build_xray([f("A", [AAPL, MSFT], expense_ratio=0.10, amount=9000),
                        f("B", [XOM, CVX], expense_ratio=0.50, amount=1000)])
        assert r.amount_weighted is True
        assert r.blended_expense_ratio == pytest.approx(0.14)

    def test_partial_amounts_fall_back_to_equal_weight(self):
        """Two of three funds priced would otherwise treat the third as
        worthless — worse than not weighting at all."""
        r = build_xray([f("A", [AAPL], amount=1000), f("B", [XOM], amount=1000),
                        f("C", [CVX])])
        assert r.amount_weighted is False

    def test_zero_amount_does_not_count_as_weighted(self):
        r = build_xray([f("A", [AAPL], amount=0), f("B", [XOM], amount=100)])
        assert r.amount_weighted is False


class TestMoneyOnlyWithAmounts:

    def test_no_currency_figures_without_amounts(self):
        """The feature must never invent a portfolio size to produce a
        satisfying number — the exact failure this codebase already removed
        once from the fund summaries."""
        r = build_xray([f("A", [AAPL, MSFT], expense_ratio=0.10),
                        f("B", [AAPL, XOM], expense_ratio=0.50)])
        assert r.annual_fee is None
        assert r.fee_on_overlap is None
        assert r.total_amount is None

    def test_currency_appears_once_amounts_are_known(self):
        r = build_xray([f("A", [AAPL, MSFT], expense_ratio=0.10, amount=5000),
                        f("B", [AAPL, XOM], expense_ratio=0.10, amount=5000)])
        assert r.total_amount == pytest.approx(10000)
        assert r.annual_fee == pytest.approx(10.0)          # 10000 * 0.10%
        # 50% overlap → half the fee is paid on duplicated exposure
        assert r.fee_on_overlap == pytest.approx(5.0)

    def test_blended_ratio_skips_funds_with_no_ratio(self):
        """A missing expense ratio must not read as a 0% fee."""
        r = build_xray([f("A", [AAPL], expense_ratio=0.50), f("B", [XOM])])
        assert r.blended_expense_ratio == pytest.approx(0.50)
        assert any("No expense ratio" in n for n in r.notes)


class TestCoverageHonesty:

    def test_top10_only_coverage_is_declared(self):
        r = build_xray([f("A", [AAPL, MSFT]),
                        f("B", [AAPL, XOM], source="yfinance_top10")])
        assert r.complete_holdings is False
        assert any("higher than shown" in n for n in r.notes)

    def test_full_nport_coverage_is_not_flagged(self):
        r = build_xray([f("A", [AAPL, MSFT]), f("B", [AAPL, XOM])])
        assert r.complete_holdings is True


class TestExcludedFunds:
    """A fund whose holdings can't be read is left out of the maths — that has
    to be said, not hidden behind a confident percentage."""

    def test_excluded_funds_are_declared(self):
        r = build_xray([f("A", [AAPL, MSFT]), f("B", [AAPL, XOM])],
                       excluded=["BOND"])
        assert r.complete_holdings is False
        assert any("BOND" in n and "left out" in n for n in r.notes)

    def test_excluded_singular_reads_correctly(self):
        r = build_xray([f("A", [AAPL])], excluded=["BOND"])
        assert any("BOND could not be read and is left out" in n for n in r.notes)

    def test_all_funds_excluded_still_reports_them(self):
        r = build_xray([], excluded=["BOND", "CASH"])
        assert r.fund_count == 0
        assert r.complete_holdings is False
        assert any("BOND, CASH" in n for n in r.notes)

    def test_excluded_reported_when_holdings_all_normalise_away(self):
        """The 'nothing usable' exit must report exclusions too, not just the
        empty-portfolio and normal exits."""
        r = build_xray([f("A", [{"name": "", "weight": 0}])], excluded=["BOND"])
        assert r.complete_holdings is False
        assert any("BOND" in n for n in r.notes)

    def test_no_excluded_note_when_everything_loaded(self):
        r = build_xray([f("A", [AAPL, MSFT])])
        assert r.complete_holdings is True
        assert not any("left out" in n for n in r.notes)


class TestConcentration:

    def test_top10_concentration_and_largest_position(self):
        holdings = [{"ticker": f"T{i}", "name": f"Co {i}", "weight": 1} for i in range(20)]
        holdings[0]["weight"] = 30
        r = build_xray([f("A", holdings)])
        assert r.largest_position["ticker"] == "T0"
        assert 0 < r.concentration_top10_pct <= 100


class TestEmptyStates:

    def test_no_funds(self):
        r = build_xray([])
        assert r.fund_count == 0
        assert r.notes and "Add at least one fund" in r.notes[0]

    def test_funds_with_no_holdings_are_dropped(self):
        r = build_xray([f("A", []), f("B", [])])
        assert r.fund_count == 0

    def test_holdings_that_all_normalise_away(self):
        r = build_xray([f("A", [{"name": "", "weight": 0}])])
        assert r.overlap_pct == 0.0
        assert r.notes


class TestHoldingKeyReuse:

    def test_pairwise_compare_still_matches_identically(self):
        """holding_key was extracted out of _compare_holdings so the X-Ray and
        the two-fund compare agree; the pairwise result must be unchanged."""
        from app.funds import _compare_holdings, holding_key

        a = [{"ticker": "AAPL", "name": "Apple Inc", "weight": 60},
             {"name": "Microsoft Corp", "weight": 40}]
        b = [{"ticker": "AAPL", "name": "Apple", "weight": 30},
             {"name": "Exxon Mobil", "weight": 70}]
        cmp = _compare_holdings(a, b)
        assert cmp["overlap_count"] == 1
        assert cmp["shared"][0]["ticker"] == "AAPL"
        assert holding_key(a[0]) == "AAPL"
        assert holding_key(a[1]) == holding_key({"name": "Microsoft Corporation"})


# ── API surface ───────────────────────────────────────────────────────────────

class TestXRayApi:
    """The endpoint contract the polling UI depends on."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        import uuid
        from fastapi.testclient import TestClient
        import app.funds as fm
        import app.main as mm
        from app.main import app
        from app.store import RecommendationStore

        store = RecommendationStore(str(tmp_path / "xray.db"))
        monkeypatch.setattr(fm, "_store", store)
        monkeypatch.setattr(mm, "store", store)
        fm._XRAY_CACHE.clear()
        fm._XRAY_PENDING.clear()

        c = TestClient(app)
        c.post("/api/auth/register",
               json={"email": f"x-{uuid.uuid4().hex[:10]}@t.com", "password": "longpass123"})
        return c

    def test_requires_login(self):
        from fastapi.testclient import TestClient
        from app.main import app
        assert TestClient(app).get("/api/funds/portfolio/xray").status_code == 401

    def test_empty_portfolio_is_an_honest_state_not_an_error(self, client):
        r = client.get("/api/funds/portfolio/xray")
        assert r.status_code == 200
        assert r.json()["status"] == "empty"
        assert r.json()["notes"]

    def test_cold_portfolio_returns_200_and_queues_once(self, client, monkeypatch):
        from unittest.mock import patch
        import app.funds as fm

        with patch("app.funds.get_fund_info", return_value={"name": "V", "expense_ratio": 0.04}):
            client.post("/api/funds", json={"symbol": "VOO"})

        with patch.object(fm, "_compute_xray_bg") as bg:
            first = client.get("/api/funds/portfolio/xray")
            second = client.get("/api/funds/portfolio/xray")
        assert first.status_code == second.status_code == 200
        assert first.json()["status"] == "computing"
        assert bg.call_count <= 1, "repeated polls must not queue duplicate work"

    def test_amount_roundtrip_and_validation(self, client):
        from unittest.mock import patch

        with patch("app.funds.get_fund_info", return_value={"name": "V", "expense_ratio": 0.04}):
            client.post("/api/funds", json={"symbol": "VOO"})

        assert client.patch("/api/funds/VOO/amount", json={"amount": 5000}).json()["amount"] == 5000
        assert client.patch("/api/funds/VOO/amount", json={"amount": -1}).status_code == 422
        assert client.patch("/api/funds/NOPE/amount", json={"amount": 10}).status_code == 404
        # Clearing returns the fund to equal-weighting rather than storing 0.
        assert client.patch("/api/funds/VOO/amount", json={"amount": None}).json()["amount"] is None

    def test_setting_an_amount_invalidates_the_cached_xray(self, client):
        """The amount changes every weighting, so a stale X-Ray must not survive."""
        from unittest.mock import patch
        import app.funds as fm
        from app.portfolio import XRayResult

        with patch("app.funds.get_fund_info", return_value={"name": "V", "expense_ratio": 0.04}):
            client.post("/api/funds", json={"symbol": "VOO"})

        uid = client.get("/api/auth/me").json()["id"]
        fm._XRAY_CACHE[uid] = XRayResult(fund_count=1)
        assert client.get("/api/funds/portfolio/xray").json()["status"] == "ready"

        client.patch("/api/funds/VOO/amount", json={"amount": 1000})
        assert uid not in fm._XRAY_CACHE

    def test_adding_a_fund_invalidates_the_cached_xray(self, client):
        """The X-Ray describes the whole portfolio, so it is wrong the moment
        the portfolio changes — not 6 hours later when the TTL expires."""
        from unittest.mock import patch
        import app.funds as fm
        from app.portfolio import XRayResult

        uid = client.get("/api/auth/me").json()["id"]
        fm._XRAY_CACHE[uid] = XRayResult(fund_count=0)
        with patch("app.funds.get_fund_info", return_value={"name": "V", "expense_ratio": 0.04}):
            client.post("/api/funds", json={"symbol": "VOO"})
        assert uid not in fm._XRAY_CACHE

    def test_removing_a_fund_invalidates_the_cached_xray(self, client):
        from unittest.mock import patch
        import app.funds as fm
        from app.portfolio import XRayResult

        with patch("app.funds.get_fund_info", return_value={"name": "V", "expense_ratio": 0.04}):
            client.post("/api/funds", json={"symbol": "VOO"})
        uid = client.get("/api/auth/me").json()["id"]
        fm._XRAY_CACHE[uid] = XRayResult(fund_count=1)

        client.delete("/api/funds/VOO")
        assert uid not in fm._XRAY_CACHE

    def test_unreadable_funds_are_reported_not_dropped(self, client):
        """End-to-end: a fund with no holdings must show up in the notes."""
        from unittest.mock import patch
        import app.funds as fm

        with patch("app.funds.get_fund_info", return_value={"name": "V", "expense_ratio": 0.04}):
            client.post("/api/funds", json={"symbol": "VOO"})
            client.post("/api/funds", json={"symbol": "BOND"})

        def holdings(sym):
            if sym == "VOO":
                return ([{"ticker": "AAPL", "name": "Apple Inc", "weight": 100}],
                        None, "nport", [])
            return ([], None, None, [])

        uid = client.get("/api/auth/me").json()["id"]
        with patch.object(fm, "_load_all_holdings", side_effect=holdings), \
             patch("app.funds.get_fund_info", return_value={"name": "V", "expense_ratio": 0.04}):
            fm._compute_xray_bg(uid)

        d = client.get("/api/funds/portfolio/xray").json()
        assert d["status"] == "ready"
        assert d["fund_count"] == 1
        assert d["complete_holdings"] is False
        assert any("BOND" in n for n in d["notes"])
