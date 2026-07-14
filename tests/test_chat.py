"""Chat regression tests — provider routing and fund-ticker detection.

These pin the fixes for two bugs that made the chatbot misbehave:
  1. "grok" was missing from _LLM_PROVIDERS, so SUMMARY_PROVIDER=grok silently
     disabled the LLM for chat (every open-ended question got the canned
     overview, fund questions returned raw data dumps).
  2. _detect_fund_ticker uppercased the whole question before token extraction,
     so ANY 2-6 letter word ("WHAT", "BEST") became a ticker candidate whenever
     a fund keyword like "return" or "performance" appeared — hijacking
     ordinary questions into the fund path.
"""
from datetime import date
from unittest.mock import patch

from app.chat import _LLM_PROVIDERS, _detect_fund_ticker, answer_question
from app.config import Settings
from app.models import AnalystRecommendation
from app.store import RecommendationStore


def _make_store(tmp_path, seed: bool = False) -> RecommendationStore:
    store = RecommendationStore(str(tmp_path / "test.db"))
    if seed:
        # One rated stock so the rule engine doesn't short-circuit with the
        # "no analyst-rated stocks yet" message before the LLM path is reached.
        store.add_recommendation(AnalystRecommendation(
            symbol="NVDA", source="yahoo", action="buy", count=10,
            entry_date=date.today().isoformat(),
        ))
    return store


# ── provider routing ──────────────────────────────────────────────────────────

def test_grok_is_an_llm_provider():
    assert "grok" in _LLM_PROVIDERS
    assert set(_LLM_PROVIDERS) >= {"gemini", "grok", "ollama", "auto"}


def test_grok_provider_reaches_llm(tmp_path):
    """With SUMMARY_PROVIDER=grok, an open-ended question must call the LLM."""
    store = _make_store(tmp_path, seed=True)
    settings = Settings(summary_provider="grok", grok_api_key="test-key")
    with patch("app.chat.generate_narrative", return_value="grok answer") as gen:
        answer, error, source = answer_question(
            store, settings, "summarize the overall picture of the data")
    assert gen.called
    assert answer == "grok answer"
    assert source == "llm"
    assert error is None


def test_rule_provider_never_calls_llm(tmp_path):
    store = _make_store(tmp_path)
    settings = Settings(summary_provider="rule")
    with patch("app.chat.generate_narrative", return_value="should not happen") as gen:
        answer, error, source = answer_question(
            store, settings, "tell me something interesting about the data")
    assert not gen.called
    assert answer  # graceful fallback, never empty
    assert source in ("rule", "overview")   # some non-LLM layer answered
    assert error is None


def test_llm_answers_even_rule_shaped_questions(tmp_path):
    """LLM-FIRST regression: a question full of rule-engine keywords must
    still be answered by the LLM when a provider is configured — the old
    rules-first order made the bot parrot canned dashboard lists."""
    store = _make_store(tmp_path, seed=True)
    settings = Settings(summary_provider="gemini", gemini_api_key="test-key")
    with patch("app.chat.generate_narrative", return_value="reasoned answer") as gen:
        answer, error, source = answer_question(
            store, settings, "which stocks have the strongest buy consensus and why?")
    assert gen.called
    assert answer == "reasoned answer"
    assert source == "llm"


def test_llm_failure_falls_back_to_rule_engine(tmp_path):
    """LLM configured but unreachable → the rule engine answers, and the
    source labels it as a fallback."""
    store = _make_store(tmp_path, seed=True)
    settings = Settings(summary_provider="gemini", gemini_api_key="test-key")
    with patch("app.chat.generate_narrative", return_value=None) as gen:
        answer, error, source = answer_question(
            store, settings, "which stocks have the strongest buy consensus?")
    assert gen.called
    assert answer and "NVDA" in answer   # rule engine's strongest-buy list
    assert source == "rule"


# ── fund ticker detection ─────────────────────────────────────────────────────

def test_known_fund_symbol_detected_case_insensitively():
    assert _detect_fund_ticker("what is spy expense ratio?") == "SPY"
    assert _detect_fund_ticker("What is SCHD expense ratio?") == "SCHD"


def test_lowercase_words_are_not_tickers():
    """A lowercase question with fund keywords must not invent a ticker
    out of ordinary words like 'what' or 'best'."""
    assert _detect_fund_ticker("what fund has the best return this year?") is None
    assert _detect_fund_ticker("which sector had the best performance?") is None


def test_explicit_uppercase_ticker_with_fund_keyword():
    """A ticker the user actually typed in caps still gets picked up."""
    assert _detect_fund_ticker("what is the expense ratio of VWO?") == "VWO"


def test_no_fund_keywords_no_detection():
    assert _detect_fund_ticker("hello there") is None
