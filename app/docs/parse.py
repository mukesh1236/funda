"""HTML filing -> plain text + canonical sections.

EDGAR fund filings are HTML, but rarely *clean* HTML: many are Word exports
where every paragraph is its own <table> and headings are bold <p> runs rather
than <h1>-<h4>. So heading detection runs twice:

  1. structural — real heading tags, if the filing has them;
  2. phrase — regex over the wording Form N-1A actually mandates.

The phrase pass is what makes this reliable: an issuer may format a summary
prospectus however it likes, but the section is still called "Principal Risks",
because the SEC requires it to be. That legal constraint is the parser's anchor.
"""
import logging
import re
from typing import List, Optional, Tuple

from app.docs.base import Section

logger = logging.getLogger(__name__)

# Form N-1A Items 2-8 wording -> canonical section key. Ordered: the first
# pattern that matches a heading wins, so put the specific before the generic.
_HEADING_PATTERNS: List[Tuple[str, "re.Pattern"]] = [
    ("objective",     re.compile(r"investment\s+objectives?", re.I)),
    ("fees",          re.compile(r"(fees?\s+and\s+expenses|annual\s+fund\s+operating\s+expenses"
                                 r"|shareholder\s+fees)", re.I)),
    ("fees",          re.compile(r"^\s*example\b", re.I)),
    ("fees",          re.compile(r"portfolio\s+turnover", re.I)),
    ("strategy",      re.compile(r"principal\s+(investment\s+)?strateg", re.I)),
    ("risks",         re.compile(r"principal\s+risks?", re.I)),
    ("risks",         re.compile(r"^\s*risks?\s*(of\s+investing)?\s*$", re.I)),
    ("performance",   re.compile(r"(past\s+performance|average\s+annual\s+total\s+returns"
                                 r"|annual\s+total\s+returns?)", re.I)),
    # Commentary must precede the generic `management` pattern, or
    # "Management's Discussion of Fund Performance" is misfiled as management.
    ("commentary",    re.compile(r"(management'?s?\s+discussion"
                                 r"|discussion\s+of\s+fund\s+performance"
                                 r"|letter\s+to\s+shareholders)", re.I)),
    ("management",    re.compile(r"(investment\s+adviser|portfolio\s+manager|management\b)", re.I)),
    ("purchase_sale", re.compile(r"(purchase\s+and\s+sale\s+of\s+fund\s+shares"
                                 r"|buying\s+and\s+selling\s+shares|minimum\s+investment)", re.I)),
    ("tax",           re.compile(r"tax\s+information", re.I)),
    # Anchored: "Distributions" as a heading, not the word inside a sentence.
    ("distributions", re.compile(r"^\s*(dividends?[, ]+(and\s+)?capital\s+gains"
                                 r"|distributions?)\b", re.I)),
]

# A heading is short. Anything longer is prose that merely mentions the words.
_MAX_HEADING_CHARS = 120
_MAX_HEADING_WORDS = 12

_BLOCK_TAGS = ("p", "div", "tr", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table")


def classify_heading(text: str) -> Optional[str]:
    """Canonical section key for a heading string, or None if it isn't one.

    Prose that merely mentions the words is rejected on shape before any
    pattern runs: real headings are short and don't end in sentence
    punctuation, whereas "Distributions may be taxable." is a sentence that
    would otherwise open a bogus section and truncate the real one.
    """
    t = (text or "").strip()
    if not t or len(t) > _MAX_HEADING_CHARS:
        return None
    if t[-1] in ".!?" and len(t.split()) > 3:
        return None
    if len(t.split()) > _MAX_HEADING_WORDS:
        return None
    for key, pat in _HEADING_PATTERNS:
        if pat.search(t):
            return key
    return None


def html_to_text(html: str) -> str:
    """Readable plain text from filing HTML, with block structure preserved."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style"]):
        bad.decompose()
    text = soup.get_text("\n")
    # EDGAR HTML is padded with non-breaking spaces and blank rows.
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text)
    return text.strip()


def _structural_headings(html: str) -> List[Tuple[str, str]]:
    """(key, heading_text) from real <h1>-<h4> tags, in document order."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        heading = " ".join(tag.get_text(" ").split())
        key = classify_heading(heading)
        if key:
            out.append((key, heading))
    return out


def find_sections(text: str) -> List[Section]:
    """Locate canonical sections by scanning the plain text line by line.

    Works on the text rather than the DOM so it behaves identically whether the
    headings were <h2> tags or bold paragraphs — the Word-export case that makes
    DOM-only detection unreliable.
    """
    lines = text.split("\n")
    hits: List[Tuple[int, str, str]] = []   # (char_offset, key, heading)
    offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped:
            key = classify_heading(stripped)
            if key:
                hits.append((offset, key, stripped))
        offset += len(line) + 1   # +1 for the newline removed by split

    if not hits:
        return []

    # Collapse consecutive hits for the same key (e.g. "Fees and Expenses"
    # immediately followed by "Annual Fund Operating Expenses") into one span.
    merged: List[Tuple[int, str, str]] = []
    for h in hits:
        if merged and merged[-1][1] == h[1]:
            continue
        merged.append(h)

    sections: List[Section] = []
    for i, (start, key, heading) in enumerate(merged):
        end = merged[i + 1][0] if i + 1 < len(merged) else len(text)
        if end > start:
            sections.append(Section(key=key, heading=heading, start=start, end=end))
    return sections


def _sections_from_structural(text: str, html: str) -> List[Section]:
    """Locate headings via the DOM, then find them in the flattened text.

    Second pass, used only when the line scan found nothing: some filings wrap
    a heading in markup that puts it on the same text line as neighbouring
    content, so it never appears as a line of its own.
    """
    hits = []
    for key, heading in _structural_headings(html):
        idx = text.find(heading)
        if idx >= 0 and all(idx != h[0] for h in hits):
            hits.append((idx, key, heading))
    hits.sort()

    sections = []
    for i, (start, key, heading) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        if end > start:
            sections.append(Section(key=key, heading=heading, start=start, end=end))
    return sections


def parse_html(html: str) -> Tuple[str, List[Section]]:
    """(plain_text, sections). Sections may be empty for an unparseable filing."""
    text = html_to_text(html)
    sections = find_sections(text)
    if not sections:
        sections = _sections_from_structural(text, html)
        if sections:
            logger.debug("parse_html: line scan found nothing, recovered %d "
                         "sections from the DOM", len(sections))
    return text, sections


def count_canonical_headings(text: str) -> int:
    """How many DISTINCT canonical sections a document contains.

    Used to reject 497 supplements: a one-page sticker amending a fee table is
    filed under the same form type as a full summary prospectus, but has almost
    none of the mandated sections.
    """
    return len({s.key for s in find_sections(text)})
