"""Generic stock overview: any searched ticker gets finance-site basics even
when it's outside the tracked analyst universe."""
from unittest.mock import patch

from fastapi.testclient import TestClient


def _mocks(profile=None, price=None, fundamentals=None):
    return [
        patch("app.service.fetch_profile", return_value=profile),
        patch("app.service.get_current_price", return_value=price),
        patch("app.service.fetch_fundamentals", return_value=fundamentals or {}),
        patch("app.service.get_news", return_value=[
            {"title": "Zeta wins big contract", "url": "http://x", "source": "Wire"}]),
        patch("app.service.fetch_ownership", return_value={
            "inst_pct": 61.0, "insider_pct": 2.0, "fund_holders": 12,
            "institutions": [], "funds": [], "recent_buyers": []}),
        patch("app.sources.sec_insider.fetch_insider_trades", return_value=[]),
    ]


def test_untracked_ticker_gets_full_overview():
    import app.service as service_mod
    service_mod._OVERVIEW_CACHE.clear()
    from app.main import app

    patches = _mocks(
        profile={"company_name": "Zeta Corp",
                 "returns": {"one_month": 3.2, "three_month": 9.1,
                              "six_month": None, "twelve_month": 24.0}},
        price=123.456,
        fundamentals={"pe_ratio": 21.5, "market_cap": 5.2e9,
                       "sector": "Technology", "industry": "Software"},
    )
    for p in patches:
        p.start()
    try:
        r = TestClient(app).get("/api/stocks/ZETA")
    finally:
        for p in patches:
            p.stop()

    assert r.status_code == 200
    d = r.json()
    assert d["company_name"] == "Zeta Corp"
    assert d["price"] == 123.46
    assert d["returns"]["one_month"] == 3.2
    assert d["fundamentals"]["sector"] == "Technology"
    assert d["ownership"]["inst_pct"] == 61.0
    assert d["news"][0]["title"] == "Zeta wins big contract"
    assert d["tracked"] is False   # not in the analyst universe


def test_unresolvable_ticker_404s():
    import app.service as service_mod
    service_mod._OVERVIEW_CACHE.clear()
    from app.main import app

    patches = _mocks(profile=None, price=None, fundamentals={})
    for p in patches:
        p.start()
    try:
        r = TestClient(app).get("/api/stocks/XXNOPE")
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 404


def test_tracked_flag_true_for_universe_symbol():
    import app.service as service_mod
    service_mod._OVERVIEW_CACHE.clear()
    from app.main import app

    patches = _mocks(profile={"company_name": "NVIDIA", "returns": {}}, price=800.0)
    for p in patches:
        p.start()
    try:
        r = TestClient(app).get("/api/stocks/NVDA")   # NVDA is in the default themes
    finally:
        for p in patches:
            p.stop()
    assert r.status_code == 200
    assert r.json()["tracked"] is True
