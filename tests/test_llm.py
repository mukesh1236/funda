"""OpenRouter robustness tests: the model fallback chain must survive a bad
or retired model slug, and must stop immediately on an auth failure."""
from unittest.mock import MagicMock, patch

from app import llm
from app.config import Settings


def _resp(status, content=""):
    m = MagicMock()
    m.status_code = status
    m.text = "error body"
    m.json.return_value = {"choices": [{"message": {"content": content}}]}
    return m


def _client(*responses):
    c = MagicMock()
    c.__enter__.return_value = c
    c.__exit__.return_value = False
    c.post.side_effect = list(responses)
    return c


def test_openrouter_falls_back_past_dead_model_slug():
    """A 404 on the configured model (renamed/retired slug) must roll over to
    the next free model instead of killing the AI feature."""
    settings = Settings(openrouter_api_key="k", openrouter_model="dead/model:free")
    client = _client(_resp(404), _resp(200, "answer from fallback"))
    with patch("app.llm.httpx.Client", return_value=client), \
         patch("app.llm._openrouter_candidates",
               return_value=["dead/model:free", "good/model:free"]):
        out = llm.openrouter_generate("hello", settings)
    assert out == "answer from fallback"
    assert client.post.call_count == 2
    assert llm.last_gemini_error is None


def test_openrouter_rate_limit_rolls_to_next_model():
    settings = Settings(openrouter_api_key="k", openrouter_model="m1:free")
    client = _client(_resp(429), _resp(200, "second model answer"))
    with patch("app.llm.httpx.Client", return_value=client), \
         patch("app.llm._openrouter_candidates", return_value=["m1:free", "m2:free"]):
        out = llm.openrouter_generate("hello", settings)
    assert out == "second model answer"


def test_openrouter_auth_failure_stops_chain_immediately():
    """401 means the key is wrong — retrying other models would only burn
    time; the chain must stop and say what to fix."""
    settings = Settings(openrouter_api_key="bad", openrouter_model="m1:free")
    client = _client(_resp(401))
    with patch("app.llm.httpx.Client", return_value=client), \
         patch("app.llm._openrouter_candidates", return_value=["m1:free", "m2:free"]):
        out = llm.openrouter_generate("hello", settings)
    assert out is None
    assert client.post.call_count == 1
    assert "OPENROUTER_API_KEY" in llm.last_gemini_error


def test_openrouter_all_models_fail_reports_details():
    settings = Settings(openrouter_api_key="k", openrouter_model="m1:free")
    client = _client(_resp(429), _resp(500))
    with patch("app.llm.httpx.Client", return_value=client), \
         patch("app.llm._openrouter_candidates", return_value=["m1:free", "m2:free"]):
        out = llm.openrouter_generate("hello", settings)
    assert out is None
    assert "exhausted" in llm.last_gemini_error
    assert "m1:free" in llm.last_gemini_error


def test_openrouter_no_key_short_circuits():
    settings = Settings(openrouter_api_key="", openrouter_model="m1:free")
    out = llm.openrouter_generate("hello", settings)
    assert out is None
    assert "OPENROUTER_API_KEY" in llm.last_gemini_error
