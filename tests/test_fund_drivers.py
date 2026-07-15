"""Fund return-driver (Pareto attribution) tests: pure math, N-PORT XML
parsing, and the /api/funds/{symbol}/drivers endpoint with mocked data."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.fund_analytics import build_headline, pareto_drivers
from app.sources.nport import _parse_nport_xml


# ── pareto math ───────────────────────────────────────────────────────────────

def _h(ticker, weight, name=None):
    return {"ticker": ticker, "name": name or ticker, "weight": weight}


def test_concentrated_fund_small_pareto_set():
    """3 mega-caps carry the fund → the Pareto set is exactly those 3."""
    holdings = [_h("AAA", 20), _h("BBB", 15), _h("CCC", 10)] + [
        _h(f"S{i}", 1.0) for i in range(20)
    ]
    returns = {"AAA": 50.0, "BBB": 40.0, "CCC": 30.0, **{f"S{i}": 1.0 for i in range(20)}}
    r = pareto_drivers(holdings, returns)
    # contributions: AAA 10pp (52%), BBB 6pp (cum 83% ≥ 80% → set closes at 2),
    # CCC 3pp and the 0.01pp tail stay outside the Pareto set.
    assert r["pareto_count"] == 2
    assert r["items"][0]["ticker"] == "AAA" and r["items"][0]["pareto"] is True
    assert r["items"][1]["ticker"] == "BBB" and r["items"][1]["pareto"] is True
    assert r["items"][2]["ticker"] == "CCC" and r["items"][2]["pareto"] is False
    assert r["items"][5]["pareto"] is False
    # cumulative must be monotonic and end at 100
    cums = [i["cum_pct"] for i in r["items"] if i["cum_pct"] is not None]
    assert cums == sorted(cums)
    assert abs(cums[-1] - 100.0) < 0.2


def test_uniform_fund_pareto_share_matches_threshold():
    """Perfectly uniform fund → ~80% of holdings needed for 80% of return."""
    holdings = [_h(f"E{i}", 1.0) for i in range(10)]
    returns = {f"E{i}": 10.0 for i in range(10)}
    r = pareto_drivers(holdings, returns)
    assert r["pareto_count"] == 8


def test_negative_contributors_excluded_from_pareto():
    holdings = [_h("UP", 50), _h("DOWN", 50)]
    returns = {"UP": 20.0, "DOWN": -30.0}
    r = pareto_drivers(holdings, returns)
    assert r["pareto_count"] == 1
    assert r["items"][0]["ticker"] == "UP" and r["items"][0]["pareto"]
    down = r["items"][1]
    assert down["contribution"] < 0 and not down["pareto"] and down["cum_pct"] is None
    assert r["total_contribution_pct"] == -5.0   # 10pp - 15pp


def test_missing_data_skipped_and_counted():
    holdings = [_h("AAA", 10), _h(None, 5, "Cash"), _h("NOPRICE", 5)]
    r = pareto_drivers(holdings, {"AAA": 10.0})
    assert r["skipped"] == 2
    assert r["coverage_pct"] == 10.0
    assert len(r["items"]) == 1


def test_all_negative_fund_has_no_pareto_set():
    holdings = [_h("A", 50), _h("B", 50)]
    r = pareto_drivers(holdings, {"A": -10.0, "B": -20.0})
    assert r["pareto_count"] == 0
    assert "No positive return drivers" in build_headline("XXX", "1y", r, 2)


def test_empty_holdings():
    r = pareto_drivers([], {})
    assert r["items"] == [] and r["pareto_count"] == 0


# ── N-PORT XML parsing ────────────────────────────────────────────────────────

_NPORT_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo><seriesId>S000012345</seriesId><repPdDate>2026-03-31</repPdDate></genInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>APPLE INC</name><cusip>037833100</cusip>
        <identifiers><ticker value="AAPL"/></identifiers>
        <pctVal>7.25</pctVal>
      </invstOrSec>
      <invstOrSec>
        <name>MYSTERY CORP</name><cusip>123456789</cusip>
        <pctVal>2.5</pctVal>
      </invstOrSec>
      <invstOrSec>
        <name>ZERO WEIGHT</name><cusip>000000000</cusip>
        <pctVal>0</pctVal>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>"""


def test_nport_parse_extracts_holdings():
    parsed = _parse_nport_xml(_NPORT_FIXTURE, want_series="S000012345")
    assert parsed is not None
    assert parsed["as_of"] == "2026-03-31"
    assert len(parsed["holdings"]) == 2          # zero-weight row dropped
    top = parsed["holdings"][0]
    assert top["ticker"] == "AAPL" and top["cusip"] == "037833100"
    assert top["weight"] == 7.25
    assert parsed["holdings"][1]["ticker"] is None   # unresolved, cusip kept


def test_nport_parse_rejects_wrong_series():
    assert _parse_nport_xml(_NPORT_FIXTURE, want_series="S000099999") is None


def test_nport_parse_garbage_returns_none():
    assert _parse_nport_xml(b"not xml at all", want_series=None) is None


# ── endpoint (mocked, no network) ─────────────────────────────────────────────

def test_drivers_endpoint_ready_path(tmp_path, monkeypatch):
    import app.funds as funds_mod
    from app.main import app

    store = funds_mod._store
    store.replace_fund_holdings("TESTFND", [
        {"ticker": "AAA", "cusip": None, "name": "Alpha", "weight": 30.0},
        {"ticker": "BBB", "cusip": None, "name": "Beta", "weight": 20.0},
    ], as_of="2026-03-31", source="nport")
    funds_mod._DRIVERS_CACHE.clear()

    with patch("app.funds.batch_period_returns",
               return_value={"AAA": 40.0, "BBB": 5.0}):
        client = TestClient(app)
        r = client.get("/api/funds/TESTFND/drivers?period=1y")

    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ready"
    assert d["holdings_count"] == 2
    assert d["source"] == "nport"
    assert d["items"][0]["ticker"] == "AAA"
    assert d["items"][0]["pareto"] is True
    assert "drove 80%" in d["headline"]

    # second call is served from cache without recomputation
    with patch("app.funds.batch_period_returns", side_effect=AssertionError("should not run")):
        r2 = TestClient(app).get("/api/funds/TESTFND/drivers?period=1y")
    assert r2.status_code == 200


def test_drivers_endpoint_cold_fund_goes_background(monkeypatch):
    import app.funds as funds_mod
    from app.main import app

    funds_mod._DRIVERS_CACHE.clear()
    funds_mod._DRIVERS_PENDING.clear()
    monkeypatch.setattr(funds_mod._store, "get_fund_holdings_stored", lambda s: [])
    with patch("app.funds._compute_drivers_bg") as bg:
        r = TestClient(app).get("/api/funds/COLDFND/drivers")
    assert r.status_code == 200
    assert r.json()["status"] == "computing"
    assert bg.called
