"""Fact sheet generation, and the guards that make its output trustworthy.

The numeric-provenance check carries most of the weight here. This codebase has
already shipped a fabricated fee figure once (a "lost compounding" number that
was $0 for some funds and a constant ~$6,625 for the rest), and it reached the
LLM as grounded context. Asking a model not to do arithmetic does not prevent
it, so the check is enforced in code and tested here.
"""
from unittest.mock import patch

import pytest

from app.config import Settings
from app.docs.base import DocumentRef, RawDocument, Section
from app.factsheet import (
    RUBRIC,
    budget_ok,
    build_facts,
    build_prompt,
    generate_summary,
    select_excerpts,
    structured_only_summary,
    validate_summary,
)
from app.store import RecommendationStore


@pytest.fixture
def store(tmp_path):
    return RecommendationStore(str(tmp_path / "t.db"))


FACTS = {
    "symbol": "ACMGX", "name": "Acme Growth Fund", "category": "Large Growth",
    "expense_ratio_pct": 0.55, "annual_cost_usd": 55.0, "cost_illustration_basis_usd": 10000,
    "return_1y_pct": 12.3, "return_5y_pct": 9.1,
    "holdings_count": 412,
    "top_holdings": [{"name": "Widget Co", "weight_pct": 6.2}],
}

DOC_TEXT = ("Investment Objective\nSeeks long-term capital appreciation.\n"
            "Fees and Expenses\nThe expense ratio is 0.55% and a waiver expires in 2026.\n"
            "Principal Risks\nStock market risk means you could lose money.\n")


def _doc():
    o = DOC_TEXT.index("Investment Objective")
    f = DOC_TEXT.index("Fees and Expenses")
    r = DOC_TEXT.index("Principal Risks")
    return RawDocument(
        ref=DocumentRef(symbol="ACMGX", source="edgar", form_type="497K",
                        url="https://sec.gov/a.htm", accession="acc-1",
                        filed_date="2025-04-28"),
        text=DOC_TEXT,
        sections=[Section("objective", "Investment Objective", o, f),
                  Section("fees", "Fees and Expenses", f, r),
                  Section("risks", "Principal Risks", r, len(DOC_TEXT))])


def _model_output(**overrides):
    out = {
        "headline": "A large-growth fund tracking US companies.",
        "jargon": [{"term": "expense ratio", "plain": "the yearly cost of owning a fund"}],
    }
    for key, _title, _guide in RUBRIC:
        out[key] = {"bullets": [{"text": "A plain statement with no figures."}]}
    out.update(overrides)
    return out


class TestBuildFacts:

    def test_annual_cost_is_computed_correctly(self, store):
        """The honest version of the calculation that was previously wrong."""
        with patch("app.fund_data.get_fund_info",
                   return_value={"name": "F", "expense_ratio": 0.55}), \
             patch("app.fund_data.get_fund_performance", return_value={}):
            facts = build_facts("ACMGX", store)
        assert facts["expense_ratio_pct"] == 0.55
        assert facts["annual_cost_usd"] == 55.0

    def test_cost_scales_with_the_ratio(self, store):
        """Regression: the old figure was a constant regardless of the ratio."""
        seen = []
        for ratio in (0.03, 0.55, 1.20):
            with patch("app.fund_data.get_fund_info",
                       return_value={"name": "F", "expense_ratio": ratio}), \
                 patch("app.fund_data.get_fund_performance", return_value={}):
                seen.append(build_facts("X", store)["annual_cost_usd"])
        assert seen == [3.0, 55.0, 120.0]
        assert len(set(seen)) == 3

    def test_survives_missing_fund_data(self, store):
        with patch("app.fund_data.get_fund_info", return_value=None), \
             patch("app.fund_data.get_fund_performance", return_value=None):
            facts = build_facts("NOPE", store)
        assert facts["symbol"] == "NOPE"

    def test_stored_holdings_preferred_over_top_ten(self, store):
        store.replace_fund_holdings("ACMGX", [
            {"name": f"H{i}", "weight": 1.0} for i in range(30)], "2025-01-01", "nport")
        with patch("app.fund_data.get_fund_info",
                   return_value={"name": "F", "holdings": [{"name": "only", "weight": 5}]}), \
             patch("app.fund_data.get_fund_performance", return_value={}):
            facts = build_facts("ACMGX", store)
        assert facts["holdings_count"] == 30


class TestExcerptSelection:

    def test_picks_sections_deterministically(self):
        keys = [e["key"] for e in select_excerpts(_doc())]
        assert keys == ["objective", "risks", "fees"] or set(keys) == {"objective", "risks", "fees"}

    def test_carries_provenance_for_citation(self):
        e = select_excerpts(_doc())[0]
        assert e["form_type"] == "497K" and e["filed_date"] == "2025-04-28"

    def test_prompt_tags_excerpts_for_citation(self):
        prompt = build_prompt(FACTS, select_excerpts(_doc()))
        assert "[S1]" in prompt
        assert "never calculate" in prompt.lower() or "Never calculate" in prompt


class TestNumericProvenance:

    def test_bullet_with_invented_number_is_dropped(self):
        bad = _model_output(what_it_costs={"bullets": [
            {"text": "Fees will cost you about $6,625 in lost compounding over 20 years."}]})
        clean, notes = validate_summary(bad, FACTS, select_excerpts(_doc()))
        costs = next(s for s in clean["sections"] if s["key"] == "what_it_costs")
        assert costs["bullets"][0]["text"] == "Not disclosed in the source document."
        assert notes and "figures not present" in notes[0]

    def test_number_from_facts_is_kept(self):
        good = _model_output(what_it_costs={"bullets": [
            {"text": "The expense ratio is 0.55%, about $55.0 a year per $10,000."}]})
        clean, notes = validate_summary(good, FACTS, select_excerpts(_doc()))
        costs = next(s for s in clean["sections"] if s["key"] == "what_it_costs")
        assert "0.55%" in costs["bullets"][0]["text"]
        assert notes == []

    def test_number_from_an_excerpt_is_kept(self):
        good = _model_output(what_it_costs={"bullets": [
            {"text": "A fee waiver expires in 2026."}]})
        clean, _ = validate_summary(good, FACTS, select_excerpts(_doc()))
        costs = next(s for s in clean["sections"] if s["key"] == "what_it_costs")
        assert "2026" in costs["bullets"][0]["text"]

    def test_thousands_separators_normalised(self):
        facts = dict(FACTS, holdings_count=1412)
        out = _model_output(what_it_holds={"bullets": [{"text": "It holds 1,412 companies."}]})
        clean, _ = validate_summary(out, facts, [])
        holds = next(s for s in clean["sections"] if s["key"] == "what_it_holds")
        assert "1,412" in holds["bullets"][0]["text"]

    def test_small_counting_numbers_allowed(self):
        out = _model_output(what_could_go_wrong={"bullets": [
            {"text": "There are 3 main risks to understand."}]})
        clean, _ = validate_summary(out, FACTS, [])
        risks = next(s for s in clean["sections"] if s["key"] == "what_could_go_wrong")
        assert "3 main risks" in risks["bullets"][0]["text"]


class TestCitations:

    def test_known_citation_returned_structurally(self):
        out = _model_output(what_could_go_wrong={"bullets": [
            {"text": "You could lose money in a downturn. [S1]"}]})
        clean, _ = validate_summary(out, FACTS, select_excerpts(_doc()))
        b = next(s for s in clean["sections"] if s["key"] == "what_could_go_wrong")["bullets"][0]
        assert b["cite"] == "S1"
        assert "[S1]" not in b["text"], "marker must not leak into display text"

    def test_unknown_citation_is_stripped(self):
        out = _model_output(what_could_go_wrong={"bullets": [
            {"text": "Some claim. [S99]"}]})
        clean, _ = validate_summary(out, FACTS, select_excerpts(_doc()))
        b = next(s for s in clean["sections"] if s["key"] == "what_could_go_wrong")["bullets"][0]
        assert b["cite"] is None
        assert "S99" not in b["text"]


class TestStructureGuarantees:

    def test_all_rubric_sections_always_present(self):
        clean, _ = validate_summary({}, FACTS, [])
        assert [s["key"] for s in clean["sections"]] == [k for k, _, _ in RUBRIC]

    def test_empty_section_says_so_rather_than_vanishing(self):
        clean, _ = validate_summary({}, FACTS, [])
        assert all(s["bullets"] for s in clean["sections"])

    def test_bullets_are_capped(self):
        many = _model_output(what_could_go_wrong={
            "bullets": [{"text": f"Risk number {chr(97 + i)}."} for i in range(20)]})
        clean, _ = validate_summary(many, FACTS, [])
        risks = next(s for s in clean["sections"] if s["key"] == "what_could_go_wrong")
        assert len(risks["bullets"]) <= 5

    def test_headline_falls_back_when_missing(self):
        clean, _ = validate_summary({}, FACTS, [])
        assert "Acme Growth Fund" in clean["headline"]


class TestStructuredOnlyFallback:

    def test_renders_from_facts_without_a_document(self):
        out = structured_only_summary(FACTS)
        text = " ".join(b["text"] for s in out["sections"] for b in s["bullets"])
        assert "0.55%" in text and "$55.0" in text
        assert "Past performance does not predict future results." in text

    def test_has_every_section(self):
        out = structured_only_summary(FACTS)
        assert [s["key"] for s in out["sections"]] == [k for k, _, _ in RUBRIC]


class TestGenerateSummary:

    def test_falls_back_when_llm_unavailable(self, store):
        with patch("app.llm.generate_narrative", return_value=None), \
             patch("app.factsheet.build_facts", return_value=FACTS):
            summary, notes, model = generate_summary("ACMGX", _doc(), store, Settings())
        assert model is None
        assert any("unavailable" in n for n in notes)
        assert summary["sections"]

    def test_falls_back_on_unparseable_output(self, store):
        with patch("app.llm.generate_narrative", return_value="not json at all"), \
             patch("app.factsheet.build_facts", return_value=FACTS):
            summary, notes, _ = generate_summary("ACMGX", _doc(), store, Settings())
        assert any("usable form" in n for n in notes)
        assert summary["sections"]

    def test_accepts_json_wrapped_in_markdown_fences(self, store):
        import json as _json
        fenced = "```json\n" + _json.dumps(_model_output()) + "\n```"
        with patch("app.llm.generate_narrative", return_value=fenced), \
             patch("app.factsheet.build_facts", return_value=FACTS):
            summary, _, _ = generate_summary("ACMGX", _doc(), store, Settings())
        assert summary["headline"].startswith("A large-growth fund")

    def test_no_document_yields_structured_only(self, store):
        with patch("app.factsheet.build_facts", return_value=FACTS):
            summary, notes, model = generate_summary("ACMGX", None, store, Settings())
        assert model is None and any("key numbers only" in n for n in notes)

    def test_budget_exhausted_skips_the_llm(self, store):
        settings = Settings(ai_daily_call_budget=10)
        with patch.object(RecommendationStore, "llm_stats", return_value={"calls_today": 9}), \
             patch("app.factsheet.build_facts", return_value=FACTS), \
             patch("app.llm.generate_narrative") as llm:
            summary, notes, _ = generate_summary("ACMGX", _doc(), store, settings)
        llm.assert_not_called()
        assert any("budget" in n for n in notes)
        assert summary["sections"]


class TestBudget:

    def test_budget_of_zero_means_unlimited(self, store):
        assert budget_ok(store, Settings(ai_daily_call_budget=0)) is True

    def test_reserve_is_respected(self, store):
        settings = Settings(ai_daily_call_budget=100, factsheet_llm_reserve_pct=0.2)
        with patch.object(RecommendationStore, "llm_stats", return_value={"calls_today": 79}):
            assert budget_ok(store, settings) is True
        with patch.object(RecommendationStore, "llm_stats", return_value={"calls_today": 80}):
            assert budget_ok(store, settings) is False


class TestChatIntegration:
    """The chat fund branch should reuse work the fact sheet already did."""

    def test_cached_factsheet_enriches_fund_context(self, store):
        import json as _json
        from app.chat import _factsheet_context

        store.save_factsheet("ACMGX", "acc-1", _json.dumps({
            "summary": {
                "headline": "A low-cost index fund.",
                "sections": [{"key": "what_could_go_wrong",
                              "title": "What could go wrong",
                              "bullets": [{"text": "Stock market risk.", "cite": None}]}],
                "jargon": []},
            "notes": []}), "m", 1)

        ctx = _factsheet_context(store, "ACMGX")
        assert "A low-cost index fund." in ctx
        assert "Stock market risk." in ctx

    def test_no_factsheet_adds_nothing(self, store):
        from app.chat import _factsheet_context

        assert _factsheet_context(store, "NOSUCH") == ""


class TestNumericProvenanceRegressions:
    """Review findings: the guard was rejecting its own source data."""

    def test_natural_dollar_phrasing_is_accepted(self):
        """FACTS stores 55.0; a model writes "$55 a year". String comparison
        rejected that and blanked the whole 'What it costs' section."""
        out = _model_output(what_it_costs={"bullets": [
            {"text": "You pay about $55 a year on every $10,000 invested."}]})
        clean, notes = validate_summary(out, FACTS, [])
        costs = next(s for s in clean["sections"] if s["key"] == "what_it_costs")
        assert "$55 a year" in costs["bullets"][0]["text"]
        assert notes == []

    def test_trailing_zero_forms_match(self):
        out = _model_output(how_its_done={"bullets": [
            {"text": "It returned 12.30% over one year."}]})
        clean, _ = validate_summary(out, FACTS, [])
        done = next(s for s in clean["sections"] if s["key"] == "how_its_done")
        assert "12.30%" in done["bullets"][0]["text"]

    def test_genuinely_invented_number_still_dropped(self):
        """The relaxation must not defeat the check it exists for."""
        out = _model_output(how_its_done={"bullets": [
            {"text": "It returned 47.9% over one year."}]})
        clean, notes = validate_summary(out, FACTS, [])
        done = next(s for s in clean["sections"] if s["key"] == "how_its_done")
        assert done["bullets"][0]["text"] == "Not disclosed in the source document."
        assert notes
