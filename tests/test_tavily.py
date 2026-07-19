"""Tavily web search: must never raise (no key / bad response / network
error all degrade to an empty result), so a search hiccup can never break
the chat answer."""
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.sources.tavily import search_web


def _resp(status, results=None):
    m = MagicMock()
    m.status_code = status
    m.text = "error body"
    m.json.return_value = {"results": results or []}
    return m


def test_no_key_returns_empty_without_network_call():
    settings = Settings(tavily_api_key="")
    with patch("app.sources.tavily.httpx.post") as post:
        out = search_web("why is NVDA falling", settings)
    assert out == []
    assert not post.called


def test_success_maps_results():
    settings = Settings(tavily_api_key="tvly-key")
    results = [{"title": "Chips slide on export curbs", "url": "https://x.test/1",
                "content": "Semiconductor stocks fell after new export restrictions..."}]
    with patch("app.sources.tavily.httpx.post", return_value=_resp(200, results)):
        out = search_web("why are semiconductor stocks falling", settings)
    assert out == [{"title": "Chips slide on export curbs", "url": "https://x.test/1",
                     "content": "Semiconductor stocks fell after new export restrictions..."}]


def test_http_error_returns_empty():
    settings = Settings(tavily_api_key="tvly-key")
    with patch("app.sources.tavily.httpx.post", return_value=_resp(401)):
        out = search_web("why is NVDA falling", settings)
    assert out == []


def test_network_exception_returns_empty():
    settings = Settings(tavily_api_key="tvly-key")
    with patch("app.sources.tavily.httpx.post", side_effect=Exception("boom")):
        out = search_web("why is NVDA falling", settings)
    assert out == []
