"""Shared vocabulary for fund disclosure documents.

Every country has its own regulator, filing formats and identifiers, so
document *acquisition* is country-specific (app/docs/edgar.py for the US SEC,
app/docs/india.py for AMFI/AMC). Everything downstream — sectioning, chunking,
embedding, summarising, answering — is identical, so it works against the
normalised types defined here rather than against any one regulator's shapes.

A fetcher's only job: given a fund symbol, find candidate documents and return
their text split into canonical sections.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Protocol

# Canonical section vocabulary. US summary prospectuses (Form N-1A Items 2-8)
# map onto these almost one-to-one, which is why they are worth normalising to:
# a question like "what are the risks" resolves to `risks` regardless of whether
# the filing called it "Principal Risks" or "Principal Risks of Investing".
SECTION_KEYS = (
    "objective",       # what the fund is trying to do
    "fees",            # fee table, expense example, waivers, loads
    "strategy",        # principal investment strategies
    "risks",           # principal risks
    "performance",     # bar chart / average annual total returns
    "management",      # adviser, sub-adviser, portfolio managers
    "purchase_sale",   # minimums, how to buy and redeem
    "tax",             # tax information
    "distributions",   # dividends and capital gains
    "commentary",      # manager discussion of fund performance (N-CSR)
    "other",
)


@dataclass
class Section:
    """One titled span of a document, located by character offset into its text."""
    key: str            # one of SECTION_KEYS
    heading: str        # the heading verbatim, for display and citation
    start: int
    end: int

    def text_from(self, doc_text: str) -> str:
        return doc_text[self.start:self.end]

    def to_dict(self) -> dict:
        return {"key": self.key, "heading": self.heading,
                "start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, d: dict) -> "Section":
        return cls(key=d.get("key") or "other", heading=d.get("heading") or "",
                   start=int(d.get("start") or 0), end=int(d.get("end") or 0))


@dataclass(frozen=True)
class DocumentRef:
    """A locatable document — enough to fetch it, cite it, and dedupe it.

    `accession` is the stable identity for EDGAR filings and doubles as the
    cache key for a generated summary: a fund's fact sheet is only regenerated
    when it files something new.
    """
    symbol: str
    source: str                        # 'edgar' | 'amfi' | 'amc'
    form_type: str                     # '497K' | '497' | '485BPOS' | 'N-CSR' | ...
    url: str
    accession: Optional[str] = None
    filed_date: Optional[str] = None
    period_date: Optional[str] = None
    title: Optional[str] = None
    doc_role: str = "primary"          # 'primary' | 'commentary'
    mime: str = "text/html"

    @property
    def doc_key(self) -> str:
        """Stable identity used to key caches. Falls back to the URL for
        sources without an accession-number equivalent (e.g. AMC PDFs)."""
        return self.accession or self.url


@dataclass
class RawDocument:
    """A fetched document: its full plain text plus where its sections are."""
    ref: DocumentRef
    text: str
    sections: List[Section] = field(default_factory=list)

    def section(self, key: str) -> Optional[Section]:
        for s in self.sections:
            if s.key == key:
                return s
        return None

    def section_text(self, key: str, limit: Optional[int] = None) -> str:
        s = self.section(key)
        if s is None:
            return ""
        out = self.text[s.start:s.end].strip()
        return out[:limit] if limit else out


class DocFetcher(Protocol):
    """What every country service must implement. Nothing else is shared."""

    source: str

    def find_documents(self, symbol: str) -> List[DocumentRef]:
        """Candidate documents for this fund, best first. [] when none."""
        ...

    def fetch(self, ref: DocumentRef) -> Optional[RawDocument]:
        """Download and section one document. None when it can't be used."""
        ...
