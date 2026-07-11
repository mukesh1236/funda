"""
Fund Tracker tests — covers DB CRUD, API routes, data layer, RAG, and chat.

Run with:
  pytest tests/test_funds.py -v
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_store(tmp_path):
    from app.store import RecommendationStore
    return RecommendationStore(str(tmp_path / "test.db"))


def _make_user(store) -> int:
    from app.auth import hash_password
    return store.create_user("test@example.com", hash_password("password123"), "Tester")


# ── DB CRUD ───────────────────────────────────────────────────────────────────

class TestFundPortfolioDB:

    def test_add_and_list_fund(self, tmp_path):
        store = _make_store(tmp_path)
        uid = _make_user(store)
        store.add_fund(uid, "SPY")
        rows = store.list_fund_portfolio(uid)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "SPY"

    def test_duplicate_add_ignored(self, tmp_path):
        store = _make_store(tmp_path)
        uid = _make_user(store)
        first = store.add_fund(uid, "QQQ")
        second = store.add_fund(uid, "QQQ")
        assert first is True
        assert second is False
        assert len(store.list_fund_portfolio(uid)) == 1

    def test_remove_fund(self, tmp_path):
        store = _make_store(tmp_path)
        uid = _make_user(store)
        store.add_fund(uid, "VTI")
        removed = store.remove_fund(uid, "VTI")
        assert removed is True
        assert store.list_fund_portfolio(uid) == []

    def test_remove_nonexistent_returns_false(self, tmp_path):
        store = _make_store(tmp_path)
        uid = _make_user(store)
        assert store.remove_fund(uid, "NOPE") is False

    def test_funds_are_user_scoped(self, tmp_path):
        store = _make_store(tmp_path)
        uid1 = store.create_user("a@x.com", "h", None)
        uid2 = store.create_user("b@x.com", "h", None)
        store.add_fund(uid1, "SPY")
        assert store.list_fund_portfolio(uid2) == []


# ── Expense ratio scaling ─────────────────────────────────────────────────────

class TestExpenseRatioScaling:

    def test_annual_report_expense_ratio_is_scaled(self):
        """annualReportExpenseRatio is a fraction — must be multiplied x100."""
        mock_info = {
            "longName": "SPDR S&P 500 ETF Trust",
            "annualReportExpenseRatio": 0.0003,
        }
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info
        mock_ticker.funds_data = None

        with patch("yfinance.Ticker", return_value=mock_ticker):
            from app.fund_data import get_fund_info
            # Clear cache to force fresh fetch
            from app.fund_data import _INFO_CACHE
            _INFO_CACHE.clear()
            result = get_fund_info("SPY")

        assert result is not None
        assert result["expense_ratio"] == pytest.approx(0.03, abs=0.001)

    def test_net_expense_ratio_used_as_is(self):
        """netExpenseRatio is already a percent — must NOT be multiplied."""
        mock_info = {
            "longName": "Invesco QQQ Trust",
            "netExpenseRatio": 0.20,
        }
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info
        mock_ticker.funds_data = None

        with patch("yfinance.Ticker", return_value=mock_ticker):
            from app.fund_data import get_fund_info, _INFO_CACHE
            _INFO_CACHE.clear()
            result = get_fund_info("QQQ")

        assert result is not None
        assert result["expense_ratio"] == pytest.approx(0.20, abs=0.001)


# ── Holdings overlap math ─────────────────────────────────────────────────────

class TestCompareOverlapMath:

    def test_one_shared_holding(self):
        from app.funds import _compare_holdings
        h1 = [
            {"ticker": "AAPL", "name": "Apple Inc", "weight": 7.5},
            {"ticker": "MSFT", "name": "Microsoft", "weight": 6.0},
        ]
        h2 = [
            {"ticker": "AAPL", "name": "Apple Inc", "weight": 8.2},
            {"ticker": "NVDA", "name": "Nvidia", "weight": 5.0},
        ]
        result = _compare_holdings(h1, h2)
        assert result["overlap_count"] == 1
        assert result["shared"][0]["ticker"] == "AAPL"
        assert result["shared"][0]["weight_a"] == pytest.approx(7.5)
        assert result["shared"][0]["weight_b"] == pytest.approx(8.2)
        assert result["overlap_weight_a"] == pytest.approx(7.5)
        assert result["overlap_weight_b"] == pytest.approx(8.2)

    def test_no_overlap(self):
        from app.funds import _compare_holdings
        h1 = [{"ticker": "AAPL", "name": "Apple", "weight": 5.0}]
        h2 = [{"ticker": "TSLA", "name": "Tesla", "weight": 4.0}]
        result = _compare_holdings(h1, h2)
        assert result["overlap_count"] == 0
        assert result["overlap_weight_a"] == 0

    def test_name_normalisation_matches(self):
        """'Apple Inc' and 'Apple Incorporated' should match via name normalisation."""
        from app.funds import _compare_holdings
        h1 = [{"ticker": None, "name": "Apple Inc", "weight": 7.5}]
        h2 = [{"ticker": None, "name": "Apple Incorporated", "weight": 8.0}]
        result = _compare_holdings(h1, h2)
        assert result["overlap_count"] == 1


# ── Fund context injection ────────────────────────────────────────────────────

class TestFundContextInjection:

    def test_build_fund_context_returns_data(self):
        """_build_fund_context returns a non-empty string with key metrics."""
        from app.fund_data import _INFO_CACHE, _PERF_CACHE

        mock_info = {
            "symbol": "SPY", "name": "SPDR S&P 500 ETF", "category": "Large Blend",
            "expense_ratio": 0.0945, "inception_date": "1993-01-22",
            "sector_weights": {"Technology": 29.0}, "holdings": [],
        }
        mock_perf = {
            "since_inception_cagr": 10.5, "total_return_pct": 1200.0,
            "cagr_1y": 25.0, "cagr_3y": 12.0, "cagr_5y": 15.0,
            "inception_date": "1993-01-22", "years_since_inception": 31.0,
        }

        with patch("app.fund_data.get_fund_info", return_value=mock_info), \
             patch("app.fund_data.get_fund_performance", return_value=mock_perf):
            import app.chat as chat_mod
            ctx = chat_mod._build_fund_context("SPY")

        assert "SPY" in ctx
        assert "0.09" in ctx or "expense" in ctx.lower()

    def test_detect_fund_ticker_known_etf(self):
        """Known ETF symbol in a question should be detected."""
        from app.chat import _detect_fund_ticker
        result = _detect_fund_ticker("What is SPY expense ratio?")
        assert result == "SPY"

    def test_detect_fund_ticker_with_keywords(self):
        """Fund keywords trigger ticker detection even for unknown symbols."""
        from app.chat import _detect_fund_ticker
        result = _detect_fund_ticker("What is SCHD expense ratio?")
        # Should detect a ticker (SCHD is in _KNOWN_FUND_SYMBOLS)
        assert result is not None


# ── RAG ───────────────────────────────────────────────────────────────────────

class TestFundRAG:

    def test_missing_index_returns_empty(self):
        """query_fund_docs returns '' when no FAISS index exists."""
        from app.fund_rag import query_fund_docs
        result = query_fund_docs("NOSUCHFUND_XYZ", "what is the expense ratio?")
        assert result == ""

    def test_ingest_and_query(self, tmp_path, monkeypatch):
        """Ingest a synthetic doc and query returns relevant chunks."""
        pytest.importorskip("faiss", reason="faiss-cpu not installed")
        pytest.importorskip("sentence_transformers", reason="sentence-transformers not installed")

        import app.fund_rag as rag_mod
        try:  # model weights must be downloadable (offline/proxied CI can't)
            rag_mod._model()
        except Exception as e:
            pytest.skip(f"embedding model unavailable in this environment: {e}")
        # Redirect index storage to tmp dir
        monkeypatch.setattr(rag_mod, "_INDEX_DIR", tmp_path)
        # Clear in-memory cache
        rag_mod._cache.clear()

        # Provide deterministic fund data
        mock_info = {
            "name": "Test Fund", "category": "Large Blend",
            "expense_ratio": 0.05, "sector_weights": {}, "holdings": [],
        }
        mock_perf = {
            "inception_date": "2000-01-01", "years_since_inception": 24.0,
            "since_inception_cagr": 8.0, "total_return_pct": 500.0,
            "cagr_1y": 10.0, "cagr_3y": 9.0, "cagr_5y": 8.5,
        }

        with patch("app.fund_data.get_fund_info", return_value=mock_info), \
             patch("app.fund_data.get_fund_performance", return_value=mock_perf):
            ok = rag_mod.ingest_fund_docs("TESTFUND")

        assert ok is True

        with patch("app.fund_data.get_fund_info", return_value=mock_info), \
             patch("app.fund_data.get_fund_performance", return_value=mock_perf):
            ctx = rag_mod.query_fund_docs("TESTFUND", "expense ratio")

        assert ctx != ""
        assert "expense" in ctx.lower() or "0.05" in ctx
