"""Endpoint-level test for the streaming chat SSE response — confirms the
wire format (data: <json> frames) round-trips through TestClient."""
import json
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_chat_stream_endpoint_emits_sse_frames(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.settings, "summary_provider", "openrouter")
    monkeypatch.setattr(main.settings, "openrouter_api_key", "test-key")

    with patch("app.llm.generate_narrative_stream", return_value=iter(["Hi", " there"])):
        with TestClient(main.app) as c:
            r = c.post("/api/chat/stream",
                       json={"question": "which stocks have the strongest buy consensus?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    frames = [f for f in r.text.split("\n\n") if f.strip()]
    events = [json.loads(f[len("data:"):].strip()) for f in frames]
    assert {"delta": "Hi"} in events
    assert {"delta": " there"} in events
    assert events[-1] == {"done": True, "source": "llm"}


def test_chat_stream_is_not_gzipped_and_disables_proxy_buffering(monkeypatch):
    """GZip buffers a whole response to compress it, which defeats SSE — the
    stream route must bypass compression and tell proxies not to buffer, or the
    browser sits on 'Thinking…' and gets everything at once."""
    import app.main as main

    monkeypatch.setattr(main.settings, "summary_provider", "openrouter")
    monkeypatch.setattr(main.settings, "openrouter_api_key", "test-key")

    with patch("app.llm.generate_narrative_stream", return_value=iter(["Hi", " there"])):
        with TestClient(main.app) as c:
            r = c.post("/api/chat/stream",
                       json={"question": "strongest buys?"},
                       headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") != "gzip"   # not compressed/buffered
    assert r.headers.get("x-accel-buffering") == "no"    # proxy buffering off
    assert r.headers.get("cache-control") == "no-cache"
