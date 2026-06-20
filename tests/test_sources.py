"""Tests for the Finnhub client mapping and Morningstar graceful degradation."""
import pytest

from app.sources.finnhub import FinnhubClient, FinnhubError
from app.sources.morningstar import MorningstarScraper


# ── Finnhub ───────────────────────────────────────────────────────────────────

def test_missing_key_raises():
    with pytest.raises(FinnhubError):
        FinnhubClient("")


def test_recommendations_mapping(monkeypatch):
    client = FinnhubClient("fake-key")
    monkeypatch.setattr(client, "get_recommendation_trend", lambda s: {
        "period": "2026-06-01", "strongBuy": 10, "buy": 15,
        "hold": 5, "sell": 2, "strongSell": 1,
    })
    monkeypatch.setattr(client, "get_price_target", lambda s: {
        "targetMean": 200, "targetMedian": 210,
    })

    recs = client.get_recommendations("AAPL", entry_date="2026-06-14")
    by_action = {r.action: r for r in recs}
    assert by_action["buy"].count == 25      # strongBuy + buy
    assert by_action["sell"].count == 3      # strongSell + sell
    assert by_action["hold"].count == 5
    # Median preferred for the target; only the buy bucket carries it.
    assert by_action["buy"].target_price == 210
    assert by_action["hold"].target_price is None
    assert all(r.source == "finnhub" for r in recs)


def test_recommendations_no_coverage(monkeypatch):
    client = FinnhubClient("fake-key")
    monkeypatch.setattr(client, "get_recommendation_trend", lambda s: None)
    assert client.get_recommendations("ZZZZ") == []


def test_zero_buckets_skipped(monkeypatch):
    client = FinnhubClient("fake-key")
    monkeypatch.setattr(client, "get_recommendation_trend", lambda s: {
        "period": "2026-06-01", "strongBuy": 0, "buy": 0,
        "hold": 3, "sell": 0, "strongSell": 0,
    })
    monkeypatch.setattr(client, "get_price_target", lambda s: None)
    recs = client.get_recommendations("AAPL")
    assert len(recs) == 1 and recs[0].action == "hold"


# ── Morningstar ───────────────────────────────────────────────────────────────

def test_morningstar_disabled_returns_none():
    assert MorningstarScraper(enabled=False).get_analyst_view("AAPL") is None


def test_morningstar_fetch_failure_returns_none(monkeypatch):
    scraper = MorningstarScraper(enabled=True)
    monkeypatch.setattr(scraper, "_fetch_html", lambda s: None)
    assert scraper.get_analyst_view("AAPL") is None  # never raises


def test_morningstar_unparseable_returns_none(monkeypatch):
    scraper = MorningstarScraper(enabled=True)
    monkeypatch.setattr(scraper, "_fetch_html", lambda s: "<html>no rating here</html>")
    assert scraper.get_analyst_view("AAPL") is None


# Unique symbol per case so the module-level scraper cache can't leak results.
@pytest.mark.parametrize("stars,action,sym", [
    (5, "buy", "AA"), (4, "buy", "BB"), (3, "hold", "CC"),
    (2, "sell", "DD"), (1, "sell", "EE"),
])
def test_morningstar_star_to_action(monkeypatch, stars, action, sym):
    scraper = MorningstarScraper(enabled=True)
    monkeypatch.setattr(scraper, "_fetch_html", lambda s: f'{{"starRating":"{stars}"}}')
    view = scraper.get_analyst_view(sym, entry_date="2026-06-14")
    assert view is not None
    assert view.action == action
    assert view.count == 1
    assert view.firm == "Morningstar"
