"""AI observability tests: llm_calls recording, aggregation math, and the
admin-only stats endpoint."""
from fastapi.testclient import TestClient

from app.store import RecommendationStore


def _store(tmp_path):
    return RecommendationStore(str(tmp_path / "test.db"))


def test_llm_stats_aggregates(tmp_path):
    s = _store(tmp_path)
    s.add_llm_call("openrouter", "m1:free", ok=True, latency_ms=800,
                    prompt_tokens=1200, completion_tokens=300, error=None)
    s.add_llm_call("openrouter", "m1:free", ok=True, latency_ms=1200,
                    prompt_tokens=1000, completion_tokens=250, error=None)
    s.add_llm_call("openrouter", "m1:free", ok=False, latency_ms=300,
                    prompt_tokens=None, completion_tokens=None, error="HTTP 429")
    s.add_llm_call("gemini", "flash", ok=True, latency_ms=600,
                    prompt_tokens=900, completion_tokens=200, error=None)

    stats = s.llm_stats(days=7)

    assert stats["calls"] == 4
    assert stats["success_rate"] == 0.75
    assert stats["prompt_tokens"] == 3100
    assert stats["completion_tokens"] == 750
    assert stats["calls_today"] == 4
    assert stats["tokens_today"] == 3850
    # failed calls must not pollute the latency average: (800+1200+600)/3
    assert stats["avg_latency_ms"] == 867
    by_model = {(m["provider"], m["model"]): m for m in stats["by_model"]}
    assert by_model[("openrouter", "m1:free")]["calls"] == 3
    assert by_model[("openrouter", "m1:free")]["ok_calls"] == 2
    assert by_model[("gemini", "flash")]["tokens"] == 1100
    assert stats["recent_errors"][0]["error"] == "HTTP 429"
    assert len(stats["latency_series"]) == 3   # successful calls only


def test_llm_stats_empty(tmp_path):
    stats = _store(tmp_path).llm_stats()
    assert stats["calls"] == 0
    assert stats["success_rate"] is None
    assert stats["by_model"] == []


def test_ai_stats_endpoint_is_admin_only():
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/api/admin/ai-stats")
    assert r.status_code == 401
