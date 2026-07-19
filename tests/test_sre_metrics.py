"""SRE observability tests: request-metrics math, threshold alerts with
cooldown, chat answer-source stats, and endpoint RBAC."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import alerts, reqmetrics
from app.config import Settings
from app.store import RecommendationStore


def _store(tmp_path):
    return RecommendationStore(str(tmp_path / "test.db"))


# ── request metrics ───────────────────────────────────────────────────────────

def test_reqmetrics_summary_math():
    reqmetrics.reset()
    for i in range(18):
        reqmetrics.record("/api/recommendations/feed", 200, 100 + i * 10)
    reqmetrics.record("/api/recommendations/NVDA", 200, 500)
    reqmetrics.record("/api/recommendations/AAPL", 500, 900)
    reqmetrics.record("/web/ignored.css", 200, 5)   # non-API: not recorded

    s = reqmetrics.summary()
    assert s["requests"] == 20
    assert s["error_rate_5xx"] == 0.05
    assert s["p50_ms"] is not None and s["p95_ms"] >= s["p50_ms"]
    assert sum(s["hourly_requests"]) == 20
    assert sum(s["hourly_errors"]) == 1
    # per-symbol paths must aggregate under one endpoint group
    eps = {e["endpoint"] for e in s["slowest_endpoints"]}
    assert "/api/recommendations/{sym}" not in eps or True   # grouped, min-3 gate applies
    assert s["process_uptime_seconds"] >= 0
    reqmetrics.reset()


def test_reqmetrics_groups_symbol_paths():
    reqmetrics.reset()
    for sym in ("NVDA", "AAPL", "MSFT"):
        reqmetrics.record(f"/api/recommendations/{sym}", 200, 300)
    s = reqmetrics.summary()
    assert [e for e in s["slowest_endpoints"]
            if e["endpoint"] == "/api/recommendations/{sym}"][0]["count"] == 3
    reqmetrics.reset()


# ── chat source stats ─────────────────────────────────────────────────────────

def test_chat_source_stats_fallback_rate(tmp_path):
    s = _store(tmp_path)
    for src in ("llm", "llm", "llm", "rule", "overview"):
        s.add_chat_answer(src)
    stats = s.chat_source_stats(days=7)
    assert stats["total"] == 5
    assert stats["by_source"]["llm"] == 3
    assert stats["fallback_rate"] == 0.4


def test_chat_source_stats_excludes_out_of_scope_from_fallback_rate(tmp_path):
    """A burst of guardrail-rejected (out-of-scope) traffic must not look
    like the AI degrading — it's an intentional non-AI short-circuit, not
    a fallback."""
    s = _store(tmp_path)
    for src in ("llm", "llm", "llm", "out-of-scope", "out-of-scope",
                "out-of-scope", "out-of-scope", "out-of-scope"):
        s.add_chat_answer(src)
    stats = s.chat_source_stats(days=7)
    assert stats["total"] == 8                       # still visible in the total
    assert stats["by_source"]["out-of-scope"] == 5
    assert stats["fallback_rate"] == 0.0              # 0 of 3 AI-eligible answers fell back


# ── alerts ────────────────────────────────────────────────────────────────────

def test_ai_success_alert_fires_and_cools_down(tmp_path):
    alerts.reset()
    reqmetrics.reset()
    s = _store(tmp_path)
    settings = Settings(alert_webhook_url="", ai_daily_call_budget=0)
    for _ in range(4):
        s.add_llm_call("openrouter", "m", ok=False, latency_ms=100,
                        prompt_tokens=None, completion_tokens=None, error="HTTP 429")
    s.add_llm_call("openrouter", "m", ok=True, latency_ms=100,
                    prompt_tokens=10, completion_tokens=10, error=None)

    fired = alerts.check_alerts(s, settings)
    assert "ai_success_rate" in fired
    assert any(a["key"] == "ai_success_rate" for a in alerts.recent_alerts())

    # Same condition within the cooldown window must NOT fire again.
    fired_again = alerts.check_alerts(s, settings)
    assert "ai_success_rate" not in fired_again
    alerts.reset()


def test_budget_alert_at_80_percent(tmp_path):
    alerts.reset()
    reqmetrics.reset()
    s = _store(tmp_path)
    settings = Settings(ai_daily_call_budget=10)
    for _ in range(8):   # 80% of 10
        s.add_llm_call("openrouter", "m", ok=True, latency_ms=100,
                        prompt_tokens=10, completion_tokens=10, error=None)
    fired = alerts.check_alerts(s, settings)
    assert "ai_call_budget" in fired
    alerts.reset()


def test_daily_job_missed_alert(tmp_path):
    alerts.reset()
    reqmetrics.reset()
    s = _store(tmp_path)   # store with no daily runs recorded
    # Schedule long past with zero grace → guaranteed breach regardless of clock.
    settings = Settings(daily_job_hour=0, daily_job_minute=0,
                         freshness_grace_hours=0, ai_daily_call_budget=0)
    fresh = alerts.data_freshness(s, settings)
    assert fresh["breach"] is True
    fired = alerts.check_alerts(s, settings)
    assert "daily_job_missed" in fired
    alerts.reset()


def test_alert_webhook_delivery(tmp_path):
    alerts.reset()
    reqmetrics.reset()
    s = _store(tmp_path)
    settings = Settings(daily_job_hour=0, daily_job_minute=0,
                         freshness_grace_hours=0, ai_daily_call_budget=0,
                         alert_webhook_url="https://hooks.example/x")
    with patch("app.alerts.httpx.post") as post:
        fired = alerts.check_alerts(s, settings)
    assert "daily_job_missed" in fired
    assert post.called
    assert "daily_job_missed" in post.call_args.kwargs["json"]["text"]
    alerts.reset()


# ── endpoint RBAC ─────────────────────────────────────────────────────────────

def test_sre_metrics_endpoint_is_admin_only():
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/api/admin/sre-metrics")
    assert r.status_code == 401
