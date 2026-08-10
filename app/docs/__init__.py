"""Fund disclosure documents — one independent service per country.

get_fetcher() is the country switch and the only coupling point between them.
The US service talks to SEC EDGAR; the India service (app/docs/india.py) will
talk to AMFI and the AMCs. They share no code, because they share no problem:
EDGAR publishes machine-readable HTML keyed by ticker under mandated headings,
while Indian funds are keyed by AMFI scheme code and their factsheets are
per-AMC PDFs with no common layout.

Everything after acquisition — sectioning, chunking, embedding, summarising —
is shared and works against app/docs/base.py's normalised types.
"""
import logging
from typing import Optional

from app.docs.base import DocFetcher, DocumentRef, RawDocument, Section, SECTION_KEYS

logger = logging.getLogger(__name__)

__all__ = ["get_fetcher", "DocFetcher", "DocumentRef", "RawDocument", "Section",
           "SECTION_KEYS", "unsupported_reason"]


def get_fetcher(symbol: str, market: str = "us") -> Optional[DocFetcher]:
    """The document service for this market, or None when unsupported.

    Returning None is not an error path — callers surface it as an honest
    "no source document available" rather than failing the request.
    """
    m = (market or "us").strip().lower()
    if m == "us":
        from app.docs.edgar import EdgarFundDocs
        return EdgarFundDocs()
    if m == "in":
        # India service not built yet — see docs/ plan, Stage 8.
        return None
    return None


def unsupported_reason(market: str = "us") -> str:
    """Why no documents are available for this market, in the user's words."""
    if (market or "us").strip().lower() == "in":
        return ("Indian mutual funds don't file with the SEC, and their factsheets "
                "aren't published in a single machine-readable place yet — so there's "
                "no source document to summarise for this fund.")
    return "No disclosure documents are available for this fund."
