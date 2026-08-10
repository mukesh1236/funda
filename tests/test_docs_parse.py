"""Filing HTML -> canonical sections.

The parser's whole job is to survive real EDGAR HTML, which is frequently a
Word export where headings are bold <p> runs inside <table> cells rather than
<h1>-<h4>. These tests pin that behaviour, plus the shape rules that stop body
prose from being mistaken for a heading.
"""
import pytest

from app.docs.parse import (
    classify_heading,
    count_canonical_headings,
    find_sections,
    html_to_text,
    parse_html,
)

CLEAN_HTML = """<html><body>
<h1>Acme Growth Fund</h1>
<p>Ticker Symbol: ACMGX</p>
<h2>Investment Objective</h2>
<p>The Fund seeks long-term capital appreciation.</p>
<h2>Fees and Expenses</h2>
<p>Annual Fund Operating Expenses are 0.55%.</p>
<h2>Principal Investment Strategies</h2>
<p>The Fund invests primarily in common stocks.</p>
<h2>Principal Risks</h2>
<p>Stock market risk. You could lose money.</p>
<h2>Tax Information</h2>
<p>Distributions may be taxable.</p>
</body></html>"""

# Same content, Word-export shape: no heading tags at all.
WORD_HTML = """<html><body>
<table><tr><td><p><b>Acme Growth Fund</b></p></td></tr></table>
<p>Ticker Symbol: ACMGX</p>
<table><tr><td><p><b>Investment Objective</b></p></td></tr></table>
<p>The Fund seeks long-term capital appreciation.</p>
<table><tr><td><p><b>Fees and Expenses</b></p></td></tr></table>
<p>Annual Fund Operating Expenses are 0.55%.</p>
<table><tr><td><p><b>Principal Investment Strategies</b></p></td></tr></table>
<p>The Fund invests primarily in common stocks.</p>
<table><tr><td><p><b>Principal Risks</b></p></td></tr></table>
<p>Stock market risk. You could lose money.</p>
<table><tr><td><p><b>Tax Information</b></p></td></tr></table>
<p>Distributions may be taxable.</p>
</body></html>"""


class TestHeadingClassification:

    @pytest.mark.parametrize("heading,expected", [
        ("Investment Objective", "objective"),
        ("Investment Objectives", "objective"),
        ("Fees and Expenses", "fees"),
        ("Annual Fund Operating Expenses", "fees"),
        ("Portfolio Turnover", "fees"),
        ("Principal Investment Strategies", "strategy"),
        ("Principal Risks", "risks"),
        ("Past Performance", "performance"),
        ("Average Annual Total Returns", "performance"),
        ("Portfolio Managers", "management"),
        ("Purchase and Sale of Fund Shares", "purchase_sale"),
        ("Tax Information", "tax"),
        ("Dividends and Capital Gains", "distributions"),
    ])
    def test_mandated_n1a_headings(self, heading, expected):
        """Form N-1A fixes this wording, which is what makes parsing reliable."""
        assert classify_heading(heading) == expected

    @pytest.mark.parametrize("prose", [
        "Distributions may be taxable.",
        "The fund is subject to principal risks of many kinds in a downturn.",
        "You will pay fees and expenses when you buy and hold shares of this fund.",
    ])
    def test_prose_is_not_a_heading(self, prose):
        """Body sentences mentioning the words must not open a bogus section —
        doing so truncates the real section that precedes them."""
        assert classify_heading(prose) is None

    def test_blank_and_overlong_rejected(self):
        assert classify_heading("") is None
        assert classify_heading("   ") is None
        assert classify_heading("Principal Risks " + "x" * 200) is None


class TestParsing:

    def test_clean_html_sections(self):
        text, sections = parse_html(CLEAN_HTML)
        keys = [s.key for s in sections]
        assert keys == ["objective", "fees", "strategy", "risks", "tax"]

    def test_word_export_sections_match_clean(self):
        """The hard case: identical sections with zero heading tags."""
        _, clean = parse_html(CLEAN_HTML)
        _, word = parse_html(WORD_HTML)
        assert [s.key for s in word] == [s.key for s in clean]

    def test_section_offsets_are_ordered_and_non_overlapping(self):
        text, sections = parse_html(WORD_HTML)
        for a, b in zip(sections, sections[1:]):
            assert a.end == b.start, "sections must tile the document"
            assert a.start < a.end
        assert sections[-1].end <= len(text)

    def test_section_text_contains_its_body(self):
        text, sections = parse_html(WORD_HTML)
        risks = next(s for s in sections if s.key == "risks")
        body = text[risks.start:risks.end]
        assert "Principal Risks" in body
        assert "You could lose money" in body
        # and must not bleed into the next section
        assert "Tax Information" not in body

    def test_tax_section_keeps_its_body(self):
        """Regression: 'Distributions may be taxable.' used to be read as a
        heading, cutting the tax section down to just its title."""
        text, sections = parse_html(WORD_HTML)
        tax = next(s for s in sections if s.key == "tax")
        assert "Distributions may be taxable" in text[tax.start:tax.end]

    def test_nbsp_and_whitespace_normalised(self):
        text = html_to_text("<p>a&nbsp;&nbsp;b</p>\n\n\n\n<p>c</p>")
        assert "\xa0" not in text
        assert "\n\n\n" not in text

    def test_empty_document(self):
        text, sections = parse_html("<html><body></body></html>")
        assert sections == []
        assert text.strip() == ""


class TestSupplementDetection:

    def test_full_summary_prospectus_has_many_sections(self):
        text, _ = parse_html(WORD_HTML)
        assert count_canonical_headings(text) >= 3

    def test_one_page_supplement_has_almost_none(self):
        """A 497 sticker is filed under the same form type as a real summary
        prospectus; section count is what tells them apart."""
        text, _ = parse_html(
            "<html><body><p>Supplement dated May 1, 2025</p>"
            "<p>The following replaces the corresponding table.</p></body></html>")
        assert count_canonical_headings(text) < 3


class TestXmlSafety:

    def test_billion_laughs_is_not_expanded(self):
        """The parser must not expand entities on hostile third-party input."""
        payload = """<!DOCTYPE lolz [
          <!ENTITY lol "lol">
          <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
          <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
        ]><html><body><p>&lol3;</p></body></html>"""
        text = html_to_text(payload)          # html.parser does not expand entities
        assert len(text) < 10_000
        assert find_sections(text) == []
