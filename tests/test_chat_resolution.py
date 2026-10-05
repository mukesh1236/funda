"""The chat must answer about the stock the user means, with real data.

Regressions pinned here:
  - "Facebook fundamentals" answered "no fundamentals in the dataset": "Facebook"
    is neither META's ticker nor a word in "Meta Platforms", so nothing resolved.
  - A stock found by the live search could be one we track, yet the prompt called
    it untracked and dropped its analyst ratings.
  - Only P/E, market cap and dividend reached the model; EPS, margins, growth and
    leverage were dropped, so a "fundamentals" answer was thin.
  - Morningstar re-records the same rating daily, so the named-firm context (12
    slots) could be all Morningstar copies, crowding out firms that acted.
"""
from datetime import date, timedelta
from unittest.mock import patch

from app.analytics import distinct_named_calls
from app.chat import _alias_symbol, _fmt_fundamentals, _fmt_symbol, answer_question
from app.config import Settings
from app.models import AnalystRecommendation, Fundamentals, StockOverview
from app.store import RecommendationStore

LLM = Settings(summary_provider="openrouter", openrouter_api_key="test-key")
RULE = Settings(summary_provider="rule")

META_FUND = Fundamentals(
    sector="Communication Services", market_cap=2.1e12, pe_ratio=28.5, forward_pe=24.0,
    peg_ratio=1.4, eps=23.9, revenue_growth=22.1, profit_margin=37.9, roe=36.0,
    debt_to_equity=27.0, dividend_yield=0.3, beta=1.2, price_to_book=9.1,
    week52_low=450.0, week52_high=800.0,
)


def _store(tmp_path, *syms):
    s = RecommendationStore(str(tmp_path / "t.db"))
    for sym in syms:
        s.add_recommendation(AnalystRecommendation(
            symbol=sym, source="yahoo", action="buy", count=20, entry_date=date.today().isoformat()))
    return s


def _ask(store, q, settings=LLM, overview=None, search=None):
    ov = overview or StockOverview(symbol="META", company_name="Meta Platforms", price=700.0,
                                   fundamentals=META_FUND)
    with patch("app.chat.generate_narrative", return_value="reasoned") as gen, \
         patch("app.service.build_stock_overview", return_value=ov), \
         patch("app.sources.search.search_tickers", return_value=search or []):
        answer, _, source = answer_question(store, settings, q)
    return (gen.call_args[0][0] if gen.called else None), answer, source


def test_alias_matches_whole_words_only():
    assert _alias_symbol("Facebook fundamentals") == "META"
    assert _alias_symbol("how is google doing") == "GOOGL"
    assert _alias_symbol("googled the news") is None


def test_facebook_fundamentals_reaches_the_model_with_full_fundamentals(tmp_path):
    prompt, _, source = _ask(_store(tmp_path, "META"), "Facebook fundamentals")
    assert source == "llm"
    assert "FOCUS STOCK META" in prompt                     # tracked: keeps analyst ratings
    assert "COMPANY PROFILE + NEWS for META" in prompt
    for fragment in ("P/E 28.5", "EPS $23.9", "Profit margin 37.9%", "Revenue growth 22.1%",
                     "Debt/Equity 27", "52-week range $450-$800"):
        assert fragment in prompt, fragment
    assert "no analyst recommendations tracked" not in prompt


def test_search_hit_on_a_tracked_stock_is_treated_as_tracked(tmp_path):
    prompt, _, _ = _ask(_store(tmp_path, "META"), "what are the fundamentals of the instagram owner company",
                        search=[{"symbol": "META", "name": "Meta Platforms"}])
    assert "FOCUS STOCK META" in prompt


def test_untracked_stock_still_gets_market_data(tmp_path):
    ko = StockOverview(symbol="KO", company_name="Coca-Cola", price=62.0,
                       fundamentals=Fundamentals(pe_ratio=24.1, eps=2.5))
    prompt, _, _ = _ask(_store(tmp_path, "NVDA"), "coca cola fundamentals", overview=ko,
                        search=[{"symbol": "KO", "name": "Coca-Cola"}])
    assert "STOCK KO" in prompt and "P/E 24.1" in prompt and "EPS $2.5" in prompt


def test_missing_fundamentals_are_named_as_unavailable_not_absent(tmp_path):
    bare = StockOverview(symbol="META", company_name="Meta Platforms", price=700.0)
    prompt, _, _ = _ask(_store(tmp_path, "META"), "Facebook fundamentals", overview=bare)
    assert "Fundamentals: unavailable right now" in prompt


def test_without_an_llm_the_rule_answer_still_gives_fundamentals(tmp_path):
    _, answer, source = _ask(_store(tmp_path, "META"), "Facebook fundamentals", settings=RULE)
    assert source == "rule"
    assert "META" in answer and "P/E 28.5" in answer and "EPS $23.9" in answer


def test_fundamentals_block_skips_missing_values():
    text = _fmt_fundamentals(Fundamentals(pe_ratio=10.0))
    assert text == "Fundamentals: P/E 10"


# ── Morningstar repeats ──────────────────────────────────────────────────────
def _with_morningstar_days(tmp_path, days=15):
    s = _store(tmp_path)
    for i in range(days):
        s.add_recommendation(AnalystRecommendation(
            symbol="META", source="morningstar", action="buy", firm="Morningstar",
            note="Morningstar: 4-star rating (undervalued)",
            entry_date=(date.today() - timedelta(days=i)).isoformat()))
    s.add_recommendation(AnalystRecommendation(
        symbol="META", source="yahoo_upgrades", action="buy", firm="Citi",
        note="Citi raised PT to $900", target_price=900.0,
        entry_date=(date.today() - timedelta(days=20)).isoformat()))
    return s


def test_daily_morningstar_snapshots_collapse_to_one(tmp_path):
    s = _with_morningstar_days(tmp_path)
    calls = distinct_named_calls(s.list_for_symbol("META"))
    assert [c.firm for c in calls] == ["Morningstar", "Citi"]
    assert calls[0].entry_date == date.today().isoformat()   # the newest is kept


def test_distinct_calls_by_the_same_firm_are_kept(tmp_path):
    s = _store(tmp_path)
    for pt, d in ((850.0, 3), (900.0, 1)):
        s.add_recommendation(AnalystRecommendation(
            symbol="META", source="yahoo_upgrades", action="buy", firm="Citi",
            note=f"Citi raised PT to ${pt:g}", target_price=pt,
            entry_date=(date.today() - timedelta(days=d)).isoformat()))
    assert len(distinct_named_calls(s.list_for_symbol("META"))) == 2


def test_chat_context_is_not_crowded_out_by_morningstar(tmp_path):
    ctx = _fmt_symbol(_with_morningstar_days(tmp_path), "META")
    assert ctx.count("4-star rating") == 1 and "Citi" in ctx


def test_detail_panel_lists_morningstar_once(tmp_path):
    import app.service as service
    core = service.build_detail_core(_with_morningstar_days(tmp_path), "META")
    named = [r.firm for r in core.recommendations if r.firm]
    assert named == ["Morningstar", "Citi"]


# ── the open row is a default, not an override ───────────────────────────────
CAT = StockOverview(symbol="CAT", company_name="Caterpillar Inc.", price=350.0,
                    fundamentals=Fundamentals(pe_ratio=17.2, eps=20.3))
CAT_HIT = [{"symbol": "CAT", "name": "Caterpillar Inc."}]


def _ask_with_open_row(store, q, open_sym="META", settings=LLM, overview=CAT, search=None):
    with patch("app.chat.generate_narrative", return_value="reasoned") as gen, \
         patch("app.service.build_stock_overview", return_value=overview) as ov, \
         patch("app.sources.search.search_tickers", return_value=search or []) as srch:
        answer, _, source = answer_question(store, settings, q, "us", open_sym)
    return (gen.call_args[0][0] if gen.called else None), answer, srch, ov


def test_a_named_company_beats_the_open_row(tmp_path):
    """The reported bug: META's row was open, 'caterpillar' was asked about."""
    prompt, _, srch, _ = _ask_with_open_row(
        _store(tmp_path, "META"), "What is caterpillar company fundamentals", search=CAT_HIT)
    assert srch.call_args[0][0] == "caterpillar"        # not "caterpillar company"
    assert "STOCK CAT" in prompt and "FOCUS STOCK META" not in prompt
    assert "P/E 17.2" in prompt


def test_pronoun_question_stays_on_the_open_stock_without_searching(tmp_path):
    prompt, _, srch, _ = _ask_with_open_row(
        _store(tmp_path, "META"), "what are its fundamentals",
        overview=StockOverview(symbol="META", company_name="Meta Platforms", fundamentals=META_FUND))
    assert not srch.called
    assert "FOCUS STOCK META" in prompt and "P/E 28.5" in prompt


def test_a_tracked_stock_named_in_the_question_beats_the_open_row(tmp_path):
    prompt, _, _, _ = _ask_with_open_row(
        _store(tmp_path, "META", "NVDA"), "how is NVDA rated", open_sym="META")
    assert "FOCUS STOCK NVDA" in prompt and "FOCUS STOCK META" not in prompt


def test_leftover_words_do_not_hijack_the_open_row(tmp_path):
    """A fuzzy hit for 'good'/'going up' must not replace the open stock."""
    junk = [{"symbol": "GM", "name": "General Motors Company"}]
    prompt, _, _, _ = _ask_with_open_row(
        _store(tmp_path, "META"), "is the sentiment good", search=junk,
        overview=StockOverview(symbol="META", company_name="Meta Platforms"))
    assert "FOCUS STOCK META" in prompt and "STOCK GM" not in prompt


def test_without_an_open_row_a_search_hit_is_accepted_as_before(tmp_path):
    prompt, _, _, _ = _ask_with_open_row(
        _store(tmp_path, "NVDA"), "caterpillar fundamentals", open_sym=None, search=CAT_HIT)
    assert "STOCK CAT" in prompt


def test_company_boilerplate_never_reaches_the_search(tmp_path):
    from app.chat import _detect_untracked_symbol
    with patch("app.sources.search.search_tickers", return_value=CAT_HIT) as srch:
        assert _detect_untracked_symbol("what is caterpillar company inc fundamentals", "us") == "CAT"
    assert srch.call_args[0][0] == "caterpillar"
    with patch("app.sources.search.search_tickers") as srch:
        assert _detect_untracked_symbol("what are its fundamentals", "us") is None
    assert not srch.called


def test_rule_answer_prefers_the_question_over_the_open_row(tmp_path):
    from app.chat import _rule_answer
    from app.service import build_feed
    store = _store(tmp_path, "META", "NVDA")
    feed = build_feed(store, days=30, market="us")
    with patch("app.service.build_stock_overview", return_value=None):
        out = _rule_answer(store, "us", "how is NVDA rated", "META", feed)
    assert out.startswith("NVDA")


# ── "I ask about dell" / "coke": leftover words, brands, and honest failure ──

from app.chat import _detect_untracked_symbol, _asks_about_a_company  # noqa: E402


def test_request_words_do_not_spoil_the_company_search():
    """"I Ask about dell" searched "i ask dell", which Yahoo's phrase match finds nothing for."""
    with patch("app.sources.search.search_tickers",
               return_value=[{"symbol": "DELL", "name": "Dell Technologies"}]) as s:
        assert _detect_untracked_symbol("I Ask about dell", "us") == "DELL"
    assert s.call_args[0][0] == "dell"


def test_search_falls_back_to_single_words_when_the_phrase_finds_nothing():
    def fake(q, market="us", limit=1, **kw):
        return [{"symbol": "DELL", "name": "Dell Technologies"}] if q == "dell" else []
    with patch("app.sources.search.search_tickers", side_effect=fake) as s:
        assert _detect_untracked_symbol("zzz quuxer dell", "us") == "DELL"
    assert [c[0][0] for c in s.call_args_list][0] == "zzz quuxer dell"
    assert len(s.call_args_list) <= 4


def test_fallback_still_respects_the_open_stock():
    """With a stock open, a hit must share a word with what was asked."""
    with patch("app.sources.search.search_tickers",
               return_value=[{"symbol": "XYZ", "name": "Unrelated Corp"}]):
        assert _detect_untracked_symbol("what about dell", "us", require_name_match=True) is None


def test_coke_resolves_through_the_brand_alias():
    assert _alias_symbol("tell me about coke") == "KO"
    assert _alias_symbol("how is pepsi doing") == "PEP"
    assert _alias_symbol("coking coal prices") is None


def test_unresolved_company_gets_an_honest_note_not_a_dataset_claim(tmp_path):
    prompt, _, _ = _ask(_store(tmp_path, "META"), "I ask about the Zzqx stock", overview=None, search=[])
    assert "could not be matched to a ticker" in prompt
    assert "ask for its" in prompt


def test_broad_questions_get_no_unresolved_note(tmp_path):
    prompt, _, _ = _ask(_store(tmp_path, "META"), "what are the top picks today?", search=[])
    assert "could not be matched to a ticker" not in prompt
    assert not _asks_about_a_company("what are the top picks today?")


def test_the_word_I_is_not_mistaken_for_a_ticker():
    """The real cause of "I ask about dell" failing: a capital "I" matched the
    typed-in-caps ticker rule, so "I" was looked up and the company never was."""
    with patch("app.sources.search.search_tickers", return_value=[]):
        assert _detect_untracked_symbol("I want to know about Dell", "us") is None
    assert _detect_untracked_symbol("What is AAPL doing, I wonder", "us") == "AAPL"


def test_general_questions_are_not_treated_as_a_failed_company_lookup(tmp_path):
    prompt, _, _ = _ask(_store(tmp_path, "META"), "how does consensus work", search=[])
    assert "could not be matched to a ticker" not in prompt
    assert not _asks_about_a_company("how does consensus work")
    assert _asks_about_a_company("What is the Zzqx stock price")
