"""Persisted stock detail (table stock_extras): opening a row must not wait on Yahoo
or SEC. Fresh rows are served as-is, stale rows are served AND refreshed in the
background, missing or very old rows are rebuilt live, and a failed upstream call
must never erase data we already hold."""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.jobs as jobs
import app.service as service
import app.summarize as summarize
from app.config import Settings
from app.models import AnalystRecommendation
from app.store import RecommendationStore

NEWS = [{"title": "AI chip demand lifts guidance", "publisher": "Wire", "url": "https://x.test/1"}]
FUND = {"pe_ratio": 30.0, "sector": "Technology"}
OWN = {"inst_pct": 70.0}


@pytest.fixture
def store(tmp_path):
    s = RecommendationStore(str(tmp_path / "t.db"))
    for sym in ("NVDA", "AMD"):
        s.add_recommendation(AnalystRecommendation(
            symbol=sym, source="yahoo", action="buy", count=10, entry_date=date.today().isoformat()))
    service._DETAIL_CACHE.clear()
    service._EXTRAS_CACHE.clear()
    summarize._SUMMARY_CACHE.clear()
    service._refreshing.clear()
    return s


class Net:
    """Patch the four upstream fetchers; records how many times each is called."""
    def __init__(self, news=None, fund=None, own=None):
        self.news = NEWS if news is None else news
        self.fund = FUND if fund is None else fund
        self.own = OWN if own is None else own
        self.patches = [
            patch("app.service.get_news", side_effect=lambda s: self.news),
            patch("app.service.fetch_ownership", side_effect=lambda s: self.own),
            patch("app.service.fetch_fundamentals", side_effect=lambda s: self.fund),
            patch("app.sources.sec_insider.fetch_insider_trades", return_value=[]),
        ]

    def __enter__(self):
        self.mocks = [p.start() for p in self.patches]
        return self

    def __exit__(self, *a):
        for p in self.patches:
            p.stop()

    @property
    def calls(self):
        return self.mocks[0].call_count


def _age(store, symbol, hours):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with store._connect() as c:
        c.execute("UPDATE stock_extras SET fetched_at = ? WHERE symbol = ?", (stamp, symbol))


# ── the table ────────────────────────────────────────────────────────────────
def test_roundtrip_upsert_and_case_insensitive(store):
    assert store.get_stock_extras("NVDA") is None
    store.put_stock_extras("nvda", '{"a": 1}')
    store.put_stock_extras("NVDA", '{"a": 2}')
    row = store.get_stock_extras("Nvda")
    assert json.loads(row["payload"]) == {"a": 2} and row["fetched_at"]


def test_table_is_added_to_an_existing_database(tmp_path):
    path = str(tmp_path / "old.db")
    RecommendationStore(path)
    with sqlite3.connect(path) as c:
        c.execute("DROP TABLE stock_extras")      # an older database that predates it
    s = RecommendationStore(path)                  # opening must create it, not fail
    s.put_stock_extras("NVDA", "{}")
    assert s.get_stock_extras("NVDA")


# ── serving ──────────────────────────────────────────────────────────────────
def test_first_open_builds_live_then_every_later_open_reads_the_row(store):
    with Net() as net:
        first = service.build_detail_extras(store, "NVDA")
        assert net.calls == 1 and first.as_of and first.fundamentals.pe_ratio == 30.0
        service._EXTRAS_CACHE.clear()               # e.g. after a deploy: memory is gone
        again = service.build_detail_extras(store, "NVDA")
        assert net.calls == 1                       # served from SQLite, no network
    assert again.fundamentals.pe_ratio == 30.0 and again.news[0].title == NEWS[0]["title"]
    assert again.as_of == first.as_of


def test_stale_row_is_served_instantly_and_refreshed_in_the_background(store):
    with Net() as net:
        service.build_detail_extras(store, "NVDA")
        service._EXTRAS_CACHE.clear()
        _age(store, "NVDA", hours=10)               # older than 6 h, younger than 7 d
        net.news = [{"title": "Fresh headline", "publisher": "W", "url": None}]
        stale = service.build_detail_extras(store, "NVDA")
        assert stale.news[0].title == NEWS[0]["title"]   # old data, returned at once
        for t in list(__import__("threading").enumerate()):
            if t.name.startswith("extras-refresh-"):
                t.join(timeout=5)
        service._EXTRAS_CACHE.clear()
        fresh = service.build_detail_extras(store, "NVDA")
    assert fresh.news[0].title == "Fresh headline"


def test_background_refresh_runs_once_per_symbol_at_a_time(store):
    service._refreshing.add("NVDA")                 # one already in flight
    assert service._refresh_in_background(store, "NVDA") is None
    service._refreshing.clear()


def test_a_week_old_row_is_not_trusted(store):
    with Net() as net:
        service.build_detail_extras(store, "NVDA")
        service._EXTRAS_CACHE.clear()
        _age(store, "NVDA", hours=24 * 8)
        service.build_detail_extras(store, "NVDA")
        assert net.calls == 2                       # rebuilt live


def test_unreadable_row_is_rebuilt_not_an_error(store):
    store.put_stock_extras("NVDA", "not json at all")
    with Net() as net:
        out = service.build_detail_extras(store, "NVDA")
        assert net.calls == 1 and out.fundamentals.pe_ratio == 30.0


# ── never erase good data ────────────────────────────────────────────────────
def test_failed_upstream_keeps_the_previous_values(store):
    with Net():
        service.build_detail_extras(store, "NVDA")
    service._EXTRAS_CACHE.clear()
    with Net(news=[], fund={}, own={}):             # every source down: all come back empty
        out = service.refresh_stock_extras(store, "NVDA")
    assert out.fundamentals.pe_ratio == 30.0
    assert out.news[0].title == NEWS[0]["title"]
    assert out.ownership.inst_pct == 70.0


def test_all_empty_with_nothing_held_is_not_persisted(store):
    with Net(news=[], fund={}, own={}):
        out = service.refresh_stock_extras(store, "NVDA")
    assert out is not None
    assert store.get_stock_extras("NVDA") is None   # an empty row would be served for 7 days


# ── consistency with current ratings ─────────────────────────────────────────
def test_summary_is_rebuilt_from_current_ratings_not_stored(store):
    with Net():
        service.build_detail_extras(store, "NVDA")
        store.add_recommendation(AnalystRecommendation(
            symbol="NVDA", source="finnhub", action="sell", count=30, entry_date=date.today().isoformat()))
        service._EXTRAS_CACHE.clear()
        core = service.build_detail_core(store, "NVDA")
        extras = service.build_detail_extras(store, "NVDA", core=core)
    summary = extras.summary or core.summary
    assert f"{core.consensus.sell_count} Sell" in summary.reasons[0]


def test_llm_narrative_is_reused_within_a_day(store):
    with Net(), patch("app.summarize.maybe_llm_narrative", return_value="Thesis text.") as llm:
        service.refresh_stock_extras(store, "NVDA")
        service.refresh_stock_extras(store, "NVDA")
        assert llm.call_count == 1                  # not once per refresh
        service._EXTRAS_CACHE.clear()
        out = service.build_detail_extras(store, "NVDA")
    assert out.summary is not None and out.summary.narrative == "Thesis text."


def test_untracked_symbol_is_never_fetched_or_stored(store):
    with Net() as net:
        assert service.build_detail_extras(store, "ZZZZ") is None
        assert service.refresh_stock_extras(store, "ZZZZ") is None
        assert net.calls == 0
    assert store.get_stock_extras("ZZZZ") is None


# ── the daily job ────────────────────────────────────────────────────────────
def test_daily_refresh_covers_every_tracked_symbol_and_survives_a_failure(store):
    seen = []

    def fake(st, sym, settings=None, core=None):
        seen.append(sym)
        if sym == "AMD":
            raise RuntimeError("upstream down")
        return object()

    with patch("app.service.refresh_stock_extras", side_effect=fake):
        done = jobs.refresh_stock_details(store, Settings())
    assert sorted(seen) == ["AMD", "NVDA"] and done == 1


def test_daily_refresh_respects_its_time_budget(store):
    with patch("app.service.refresh_stock_extras") as r:
        assert jobs.refresh_stock_details(store, Settings(), budget_seconds=-1) == 0
        r.assert_not_called()


@pytest.mark.parametrize("flag,expected", [(True, 1), (False, 0)])
def test_daily_job_runs_the_refresh_only_when_enabled(store, flag, expected):
    s = Settings(precompute_stock_details=flag)
    with patch.object(jobs, "collect", return_value=0), patch.object(jobs, "validate", return_value=0), \
         patch.object(jobs, "refresh_profiles", return_value=0), \
         patch.object(jobs, "build_feed", return_value=SimpleNamespace(stocks=[])), \
         patch.object(jobs, "get_notifier"), patch.object(jobs, "send_whatsapp_briefs", return_value=0), \
         patch.object(jobs, "refresh_stock_details") as r:
        jobs.run_daily(store, s)
    assert r.call_count == expected
