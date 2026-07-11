"""Fund document RAG — FAISS-backed retrieval over synthesized fund documents.

Index storage: data/fund_index/{SYMBOL}/
  index.faiss  — FAISS IndexFlatIP (cosine similarity on normalised vectors)
  chunks.json  — list of text chunks corresponding to FAISS row IDs

Usage:
  ingest_fund_docs(symbol)             -> bool   build/rebuild the index
  query_fund_docs(symbol, question, k) -> str    top-k chunks as context
  fund_index_exists(symbol)            -> bool   quick check without loading

All functions return gracefully (empty string / False) when sentence-transformers
or faiss-cpu are not installed, so the feature degrades silently.
"""
import json
import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_INDEX_DIR = Path("data/fund_index")
_WORDS_PER_CHUNK = 80
_CHUNK_STRIDE = 60        # overlap = 80 - 60 = 20 words
_EMBED_MODEL = "all-MiniLM-L6-v2"

_lock = threading.Lock()
_cache: Dict[str, Tuple] = {}   # sym -> (faiss_index, chunks)


# ── helpers ───────────────────────────────────────────────────────────────────

def _chunk(text: str) -> List[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i: i + _WORDS_PER_CHUNK])
        if chunk.strip():
            chunks.append(chunk)
        i += _CHUNK_STRIDE
    return chunks


@lru_cache(maxsize=1)
def _model():
    """Load the embedding model once per process — constructing
    SentenceTransformer per call costs seconds of disk load each time."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(_EMBED_MODEL)


def _embed(texts: List[str]):
    return _model().encode(texts, normalize_embeddings=True, show_progress_bar=False)


def _synthesize(symbol: str) -> str:
    """Build a rich text document for a fund from yfinance data."""
    from app.fund_data import get_fund_info, get_fund_performance

    info = get_fund_info(symbol) or {}
    perf = get_fund_performance(symbol) or {}

    name = info.get("name", symbol)
    category = info.get("category", "")
    expense = info.get("expense_ratio")
    sectors = info.get("sector_weights", {})
    holdings = info.get("holdings", [])
    inception = info.get("inception_date") or perf.get("inception_date", "")

    parts = [
        f"Fund name: {name} Ticker: {symbol}",
        f"Category: {category}." if category else "",
        f"Fund inception date: {inception}." if inception else "",
        (f"Expense ratio: {expense}% per year. "
         f"On a $10,000 investment you pay ${10000 * expense / 100:.0f} annually in fees.")
        if expense is not None else "",
    ]

    if perf:
        parts.append(
            f"Performance since inception: total return {perf.get('total_return_pct')}%, "
            f"CAGR {perf.get('since_inception_cagr')}% over {perf.get('years_since_inception')} years. "
            f"1-year CAGR {perf.get('cagr_1y')}%. "
            f"3-year CAGR {perf.get('cagr_3y')}%. "
            f"5-year CAGR {perf.get('cagr_5y')}%."
        )

    if sectors:
        top = sorted(sectors.items(), key=lambda x: x[1], reverse=True)[:6]
        parts.append("Sector allocation: " + ", ".join(f"{k} {v:.1f}%" for k, v in top) + ".")

    if holdings:
        h_text = ", ".join(
            f"{h.get('name') or h.get('ticker', 'Unknown')} {h.get('weight', 0):.1f}%"
            for h in holdings[:10]
        )
        parts.append(f"Top holdings: {h_text}.")

    if expense is not None:
        parts.append(
            f"The annual expense ratio of {expense}% means that over 20 years on a $10,000 "
            f"investment (assuming 7% annual return), fees cost approximately "
            f"${int(10000 * ((1.07 ** 20) - (1.06 ** 20 if expense < 1 else 1.07 ** 20))):.0f} "
            f"in lost compounding."
        )

    parts.append(
        f"{name} is a {category or 'diversified'} fund that provides exposure to a broad "
        f"portfolio of securities. Investors use it for long-term wealth building."
    )

    return "\n".join(p for p in parts if p)


# ── public API ────────────────────────────────────────────────────────────────

def fund_index_exists(symbol: str) -> bool:
    sym = symbol.upper().strip()
    return (_INDEX_DIR / sym / "index.faiss").exists()


def ingest_fund_docs(symbol: str) -> bool:
    """Build/rebuild FAISS index for the fund. Returns True on success."""
    sym = symbol.upper().strip()
    try:
        import faiss
        import numpy as np

        doc = _synthesize(sym)
        if not doc.strip():
            logger.warning("No content for %s — skipping RAG ingest", sym)
            return False

        chunks = _chunk(doc)
        if not chunks:
            return False

        embeddings = _embed(chunks).astype("float32")
        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)

        idx_dir = _INDEX_DIR / sym
        idx_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(idx_dir / "index.faiss"))
        (idx_dir / "chunks.json").write_text(
            json.dumps(chunks, ensure_ascii=False), encoding="utf-8"
        )
        with _lock:
            _cache[sym] = (index, chunks)

        logger.info("RAG: ingested %d chunks for %s", len(chunks), sym)
        return True
    except ImportError:
        logger.debug("faiss-cpu or sentence-transformers not installed — RAG skipped")
        return False
    except Exception as e:
        logger.error("ingest_fund_docs(%s): %s", sym, e)
        return False


def query_fund_docs(symbol: str, question: str, k: int = 4) -> str:
    """Return top-k chunks as a context string. Empty string if no index."""
    sym = symbol.upper().strip()
    if not question.strip():
        return ""
    try:
        import faiss
        import numpy as np

        with _lock:
            entry = _cache.get(sym)

        if entry is None:
            idx_file = _INDEX_DIR / sym / "index.faiss"
            chunks_file = _INDEX_DIR / sym / "chunks.json"
            if not idx_file.exists():
                return ""
            index = faiss.read_index(str(idx_file))
            chunks = json.loads(chunks_file.read_text(encoding="utf-8"))
            with _lock:
                _cache[sym] = (index, chunks)
            entry = (index, chunks)

        index, chunks = entry
        q_emb = _embed([question]).astype("float32")
        k_actual = min(k, len(chunks))
        _, idxs = index.search(q_emb, k_actual)
        result = [chunks[i] for i in idxs[0] if 0 <= i < len(chunks)]
        return "\n\n".join(result)
    except ImportError:
        return ""
    except Exception as e:
        logger.debug("query_fund_docs(%s): %s", sym, e)
        return ""
