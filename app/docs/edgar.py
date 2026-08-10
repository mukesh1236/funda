"""US fund documents from SEC EDGAR.

Independent of the India service — the two share only the DocFetcher protocol.

Form priority, best first:
  497K     Summary Prospectus. 4-10 pages, and Form N-1A Items 2-8 mandate
           exactly the sections a retail reader needs. This IS the fact sheet.
  497      Other definitive material. Sometimes a summary prospectus, often a
           one-page supplement — accepted only if it actually has the sections.
  485BPOS  Full statutory prospectus. Always exists, but 50-200 pages and
           usually combined across every series in the trust.
  N-CSR    Shareholder report, for manager commentary. Additive, not primary.

Reuses app/sources/nport.py for the parts that are already solved there:
_get() (EDGAR etiquette headers, error surfacing), fund_identity() (ticker ->
CIK for both mutual funds and ETF trusts) and iter_filings().
"""
import logging
import re
import time
from typing import List, Optional

from app.docs.base import DocumentRef, RawDocument
from app.docs.parse import count_canonical_headings, parse_html
from app.sources import nport

logger = logging.getLogger(__name__)

# Ordered best-first; index in this tuple is the sort key for candidates.
FORM_PRIORITY = ("497K", "497", "485BPOS")
COMMENTARY_FORMS = ("N-CSR", "N-CSRS")

_MAX_CANDIDATES = 6            # cap EDGAR fetches per fund
_VALIDATE_CHARS = 20_000       # how far in to look for proof of the right fund
_MIN_SECTIONS_FOR_497 = 3      # a bare supplement has almost none


def _max_doc_bytes() -> int:
    from app.config import get_settings

    return get_settings().factsheet_max_doc_bytes

# "Ticker Symbol: VFIAX", "Ticker: VOO" — how a filing labels a series.
# The label is matched case-insensitively but the ticker itself is not: lowering
# the whole pattern would make any 3-6 letter word look like a ticker.
#
# Deliberately does NOT match a bare "(VFIAX)". Prospectuses are full of
# parenthesised acronyms — (SEC), (NAV), (ETF), (IRA) — and treating those as
# series tickers makes an ordinary single-fund filing look combined, which
# truncates it at the first acronym and then fails the match entirely. The
# labelled form plus the fund-name heading below are the reliable signals.
_TICKER_LABEL_RE = re.compile(
    r"(?i:ticker(?:\s+symbol)?s?)\s*[:\-]\s*([A-Z]{3,6})\b")

# A fund-name heading: a short line ending in Fund / ETF / Portfolio / Trust.
# Second, independent signal for "this filing covers more than one fund" —
# needed because a combined filing whose FIRST series is the one we asked for
# would otherwise pass the ticker check and be accepted whole.
_FUND_HEADING_RE = re.compile(
    r"^[A-Z][A-Za-z0-9&.,'\- ]{2,60}\b(Fund|ETF|Portfolio|Trust)\s*$", re.M)

_NAME_NOISE_RE = re.compile(
    r"\b(fund|etf|trust|index|investor|admiral|institutional|class|shares?|inc|the)\b", re.I)


def _norm_name(name: str) -> str:
    """Loose fund-name key for matching. Local rather than importing
    app.funds._norm — app/funds.py is a FastAPI router, and a source module
    should not depend on the web layer."""
    return re.sub(r"[^a-z0-9]", "", _NAME_NOISE_RE.sub(" ", (name or "").lower()))


def _fund_name(symbol: str) -> str:
    try:
        from app.fund_data import get_fund_info
        return (get_fund_info(symbol) or {}).get("name") or ""
    except Exception as e:
        logger.debug("edgar: no fund name for %s: %s", symbol, e)
        return ""


def locate_fund_span(text: str, symbol: str, fund_name: str = "") -> Optional[tuple]:
    """(start, end) of the span describing THIS fund, or None if unprovable.

    Returning None is a deliberate, load-bearing outcome: one trust files a
    single 485BPOS covering dozens of series, and confidently summarising the
    wrong fund is the worst failure this feature could produce. When we cannot
    prove which span belongs to the requested fund, the caller reports the fact
    sheet as unavailable rather than guessing.
    """
    sym = symbol.upper().strip()
    head = text[:_VALIDATE_CHARS]

    labelled = [(m.start(), m.group(1)) for m in _TICKER_LABEL_RE.finditer(text)]
    distinct_tickers = {t for _, t in labelled}
    name_headings = {m.group(0).strip() for m in _FUND_HEADING_RE.finditer(text)}

    combined = len(distinct_tickers) > 1 or len(name_headings) > 1

    if combined:
        # Anchor on our ticker, else on our fund-name heading; slice from there
        # to wherever the next fund starts.
        anchors = [p for p, t in labelled if t == sym]
        key = _norm_name(fund_name)
        if not anchors and key:
            anchors = [m.start() for m in _FUND_HEADING_RE.finditer(text)
                       if _norm_name(m.group(0)) == key]
        if not anchors:
            return None            # our fund isn't in this filing at all

        start = anchors[0]
        boundaries = [p for p, t in labelled if t != sym and p > start]
        boundaries += [m.start() for m in _FUND_HEADING_RE.finditer(text)
                       if m.start() > start and _norm_name(m.group(0)) != _norm_name(fund_name)]
        end = min(boundaries) if boundaries else len(text)
        return (start, end) if end - start > 500 else None

    # Single-series document: the ticker or the fund name must actually appear.
    if sym in head.upper():
        return (0, len(text))

    key = _norm_name(fund_name)
    if key and len(key) >= 6 and key in _norm_name(head):
        return (0, len(text))

    return None


class EdgarFundDocs:
    """DocFetcher for US SEC-registered funds and ETFs."""

    source = "edgar"

    def find_documents(self, symbol: str) -> List[DocumentRef]:
        sym = symbol.upper().strip()
        ident = nport.fund_identity(sym)
        if ident is None:
            logger.info("edgar: no SEC identity for %s (%s)", sym, nport.last_error)
            return []
        cik, _series = ident

        wanted = set(FORM_PRIORITY) | set(COMMENTARY_FORMS)
        filings = nport.iter_filings(cik, forms=wanted, limit=40)
        if not filings:
            return []

        refs = []
        for f in filings:
            form = f["form"]
            refs.append(DocumentRef(
                symbol=sym,
                source=self.source,
                form_type=form,
                url=f["url"],
                accession=f["accession"],
                filed_date=f["filing_date"],
                doc_role="commentary" if form in COMMENTARY_FORMS else "primary",
            ))

        def rank(r: DocumentRef):
            try:
                pri = FORM_PRIORITY.index(r.form_type)
            except ValueError:
                pri = len(FORM_PRIORITY)          # commentary forms last
            return (pri, _neg_date(r.filed_date))

        refs.sort(key=rank)
        return refs[:_MAX_CANDIDATES]

    def fetch(self, ref: DocumentRef) -> Optional[RawDocument]:
        resp = nport._get(ref.url, timeout=60)
        time.sleep(0.15)      # stay far under EDGAR's 10 req/s ceiling
        if resp is None:
            return None
        raw = resp.content or b""
        if len(raw) > _max_doc_bytes():
            logger.info("edgar: %s %s too large (%d bytes) — skipped",
                        ref.symbol, ref.form_type, len(raw))
            return None

        text, sections = parse_html(resp.text)
        if not text.strip():
            return None

        # A 497 may be a one-page supplement rather than a summary prospectus.
        if ref.form_type == "497" and count_canonical_headings(text) < _MIN_SECTIONS_FOR_497:
            logger.info("edgar: %s 497 %s looks like a supplement — skipped",
                        ref.symbol, ref.accession)
            return None

        span = locate_fund_span(text, ref.symbol, _fund_name(ref.symbol))
        if span is None:
            logger.info("edgar: could not prove %s is the subject of %s %s — skipped",
                        ref.symbol, ref.form_type, ref.accession)
            return None

        start, end = span
        if (start, end) != (0, len(text)):
            text = text[start:end]
            sections = [s for s in sections if s.start >= start and s.end <= end]
            for s in sections:
                s.start -= start
                s.end -= start

        return RawDocument(ref=ref, text=text, sections=sections)


def _neg_date(d: Optional[str]) -> str:
    """Sort key that puts newer filing dates first ('2025-04-28' -> inverted)."""
    if not d:
        return "9999"
    return "".join(chr(ord("9") - (ord(c) - ord("0"))) if c.isdigit() else c for c in d)
