"""SEC EDGAR fund-document service.

The load-bearing behaviour here is negative: one trust files a single 485BPOS
covering dozens of series, so the fetcher must refuse to hand back a document
it cannot prove belongs to the requested fund. A confident plain-English
summary of the WRONG fund is the worst output this feature could produce.

No network: nport._get / fund_identity / iter_filings are patched throughout.
"""
from unittest.mock import patch

import pytest

from app.docs.base import DocumentRef
from app.docs.edgar import EdgarFundDocs, locate_fund_span

SUMMARY_HTML = """<html><body>
<p><b>Acme Growth Fund</b></p>
<p>Ticker Symbol: ACMGX</p>
<p><b>Investment Objective</b></p><p>Long-term capital appreciation.</p>
<p><b>Fees and Expenses</b></p><p>Annual operating expenses 0.55%.</p>
<p><b>Principal Investment Strategies</b></p><p>Invests in common stocks.</p>
<p><b>Principal Risks</b></p><p>Stock market risk. You could lose money.</p>
<p><b>Tax Information</b></p><p>Distributions may be taxable.</p>
</body></html>"""

# One filing, three series — the combined-prospectus problem.
COMBINED_HTML = """<html><body>
<p><b>Acme Growth Fund</b></p><p>Ticker Symbol: ACMGX</p>
<p><b>Principal Risks</b></p><p>Growth risk applies to this fund. """ + ("g" * 600) + """</p>
<p><b>Acme Value Fund</b></p><p>Ticker Symbol: ACMVX</p>
<p><b>Principal Risks</b></p><p>Value risk applies to this fund. """ + ("v" * 600) + """</p>
<p><b>Acme Bond Fund</b></p><p>Ticker Symbol: ACMBX</p>
<p><b>Principal Risks</b></p><p>Interest rate risk. """ + ("b" * 600) + """</p>
</body></html>"""

SUPPLEMENT_HTML = ("<html><body><p>Supplement dated May 1, 2025</p>"
                   "<p>Ticker Symbol: ACMGX</p>"
                   "<p>The fee table is amended as follows.</p></body></html>")


class _Resp:
    """Minimal stand-in for the httpx.Response nport._get returns."""
    def __init__(self, text):
        self.text = text
        self.content = text.encode()


def _filing(form, acc, date="2025-04-28"):
    return {"form": form, "accession": acc, "primary_doc": f"{acc}.htm",
            "filing_date": date,
            "url": f"https://www.sec.gov/Archives/edgar/data/1/{acc}/{acc}.htm"}


def _ref(form="497K", acc="0001-25-1", symbol="ACMGX"):
    return DocumentRef(symbol=symbol, source="edgar", form_type=form,
                       url="https://www.sec.gov/x.htm", accession=acc,
                       filed_date="2025-04-28")


class TestLocateFundSpan:

    def test_accepts_when_ticker_present(self):
        assert locate_fund_span("Ticker Symbol: ACMGX ...", "ACMGX") is not None

    def test_accepts_when_only_fund_name_present(self):
        span = locate_fund_span("The Acme Growth Fund seeks growth.", "ACMGX",
                                "Acme Growth Fund")
        assert span is not None

    def test_rejects_when_fund_is_absent(self):
        """Neither ticker nor name -> unprovable -> must refuse."""
        assert locate_fund_span("Some entirely unrelated filing text.", "ACMGX",
                                "Acme Growth Fund") is None

    def test_combined_filing_slices_to_target_fund(self):
        from app.docs.parse import html_to_text
        text = html_to_text(COMBINED_HTML)
        span = locate_fund_span(text, "ACMVX", "Acme Value Fund")
        assert span is not None
        sliced = text[span[0]:span[1]]
        assert "Value risk applies" in sliced
        assert "Growth risk applies" not in sliced
        assert "Interest rate risk" not in sliced

    def test_combined_filing_rejects_fund_not_in_it(self):
        from app.docs.parse import html_to_text
        text = html_to_text(COMBINED_HTML)
        assert locate_fund_span(text, "OTHER", "Some Other Fund") is None

    def test_combined_filing_slices_even_when_target_is_first(self):
        """Regression: the first fund in a combined filing passes the naive
        'is my ticker in the opening text' check, so without a second
        multi-fund signal the whole 20-fund document would be accepted."""
        from app.docs.parse import html_to_text
        text = html_to_text(COMBINED_HTML)
        span = locate_fund_span(text, "ACMGX", "Acme Growth Fund")
        assert span is not None
        sliced = text[span[0]:span[1]]
        assert "Growth risk applies" in sliced
        assert "Value risk applies" not in sliced
        assert "Interest rate risk" not in sliced

    def test_single_fund_document_is_not_sliced(self):
        """The common case must stay whole — no false combined detection."""
        from app.docs.parse import html_to_text
        text = html_to_text(SUMMARY_HTML)
        assert locate_fund_span(text, "ACMGX", "Acme Growth Fund") == (0, len(text))


class TestFindDocuments:

    def test_returns_empty_without_sec_identity(self):
        with patch("app.sources.nport.fund_identity", return_value=None):
            assert EdgarFundDocs().find_documents("NOTAFUND") == []

    def test_497k_ranks_above_485bpos_and_ncsr(self):
        filings = [_filing("N-CSR", "a"), _filing("485BPOS", "b"),
                   _filing("497", "c"), _filing("497K", "d")]
        with patch("app.sources.nport.fund_identity", return_value=(123, "S1")), \
             patch("app.sources.nport.iter_filings", return_value=filings):
            refs = EdgarFundDocs().find_documents("ACMGX")
        assert [r.form_type for r in refs] == ["497K", "497", "485BPOS", "N-CSR"]

    def test_newer_filing_of_same_form_ranks_first(self):
        filings = [_filing("497K", "old", "2023-01-01"),
                   _filing("497K", "new", "2025-06-30")]
        with patch("app.sources.nport.fund_identity", return_value=(123, None)), \
             patch("app.sources.nport.iter_filings", return_value=filings):
            refs = EdgarFundDocs().find_documents("ACMGX")
        assert [r.accession for r in refs] == ["new", "old"]

    def test_ncsr_marked_as_commentary(self):
        with patch("app.sources.nport.fund_identity", return_value=(1, None)), \
             patch("app.sources.nport.iter_filings", return_value=[_filing("N-CSR", "a")]):
            refs = EdgarFundDocs().find_documents("ACMGX")
        assert refs[0].doc_role == "commentary"


class TestFetch:

    def test_parses_a_summary_prospectus(self):
        with patch("app.sources.nport._get", return_value=_Resp(SUMMARY_HTML)), \
             patch("app.docs.edgar._fund_name", return_value="Acme Growth Fund"):
            doc = EdgarFundDocs().fetch(_ref())
        assert doc is not None
        assert {s.key for s in doc.sections} >= {"objective", "fees", "strategy", "risks"}
        assert "You could lose money" in doc.section_text("risks")

    def test_returns_none_on_fetch_failure(self):
        with patch("app.sources.nport._get", return_value=None):
            assert EdgarFundDocs().fetch(_ref()) is None

    def test_rejects_497_supplement(self):
        """Same form type as a real summary prospectus, but no real sections."""
        with patch("app.sources.nport._get", return_value=_Resp(SUPPLEMENT_HTML)), \
             patch("app.docs.edgar._fund_name", return_value="Acme Growth Fund"):
            assert EdgarFundDocs().fetch(_ref(form="497")) is None

    def test_rejects_document_about_a_different_fund(self):
        """Fails closed rather than summarising a sibling fund."""
        with patch("app.sources.nport._get", return_value=_Resp(SUMMARY_HTML)), \
             patch("app.docs.edgar._fund_name", return_value="Zeta Income Fund"):
            assert EdgarFundDocs().fetch(_ref(symbol="ZETAX")) is None

    def test_combined_filing_sections_reindexed_to_slice(self):
        """After slicing, section offsets must address the sliced text."""
        with patch("app.sources.nport._get", return_value=_Resp(COMBINED_HTML)), \
             patch("app.docs.edgar._fund_name", return_value="Acme Value Fund"):
            doc = EdgarFundDocs().fetch(_ref(symbol="ACMVX"))
        assert doc is not None
        for s in doc.sections:
            assert 0 <= s.start < s.end <= len(doc.text)
        assert "Growth risk applies" not in doc.text

    def test_oversized_document_skipped(self):
        big = "<html><body>" + ("x" * 5_000_000) + "</body></html>"
        with patch("app.sources.nport._get", return_value=_Resp(big)):
            assert EdgarFundDocs().fetch(_ref()) is None


class TestDocumentRef:

    def test_doc_key_prefers_accession(self):
        assert _ref(acc="0001-25-1").doc_key == "0001-25-1"

    def test_doc_key_falls_back_to_url(self):
        r = DocumentRef(symbol="X", source="amc", form_type="factsheet_pdf",
                        url="https://amc.example/f.pdf")
        assert r.doc_key == "https://amc.example/f.pdf"
