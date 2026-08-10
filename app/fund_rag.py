"""Fund document RAG — FAISS retrieval over real disclosure filings.

Index storage: {FUND_INDEX_DIR}/{SYMBOL}/
  index.faiss  — IndexFlatIP (cosine similarity on normalised vectors)
  chunks.json  — chunk records, one per FAISS row id
  meta.json    — schema version, embedding model, source documents

Chunks are records, not bare strings: each carries the form type, filing date,
section and EDGAR URL it came from, which is what lets an answer cite the
filing section rather than asserting a fact from nowhere.

Retrieval keeps similarity scores and applies a floor, so an off-topic question
returns nothing instead of the nearest paragraph. It also caps how many chunks
one section may contribute, so a ten-paragraph risk section cannot crowd out
the one paragraph that answers a question about fees.

Everything degrades to False / [] when faiss-cpu or sentence-transformers are
missing, matching the rest of the project's fail-soft convention.
"""
import json
import logging
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from cachetools import LRUCache

logger = logging.getLogger(__name__)

# Bumped whenever the on-disk chunk record shape changes. v1 stored a flat
# list of strings; loading one of those as records would crash, so an index
# whose meta.json is missing or older is treated as absent and rebuilt.
SCHEMA_VERSION = 2

_EMBED_MODEL = "all-MiniLM-L6-v2"

# ~250 words is about one coherent risk factor or strategy paragraph. The old
# 80-word window was sized for a 180-word synthesized blurb and produces
# meaningless fragments over a real 60-page prospectus.
_WORDS_PER_CHUNK = 250
_CHUNK_OVERLAP = 50

_DEFAULT_MIN_SCORE = 0.25      # cosine floor for MiniLM; tuned against fixtures
_MAX_PER_SECTION = 2

_lock = threading.Lock()
# Bounded: a FAISS index plus ~1500 chunks is tens of MB, and the previous
# unbounded dict grew for the process lifetime — an OOM on a small container.
_cache: LRUCache = LRUCache(maxsize=8)

# One ingest at a time per process: two simultaneous "add fund" clicks would
# otherwise run two MiniLM encodes concurrently and starve the web workers.
_ingest_sem = threading.Semaphore(1)


@dataclass
class ChunkRecord:
    """One indexed passage plus everything needed to cite it."""
    text: str
    symbol: str = ""
    source: str = ""            # 'edgar' | 'amfi' | 'amc'
    form_type: str = ""
    filed_date: Optional[str] = None
    accession: Optional[str] = None
    section: str = "other"
    heading: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return dict(text=self.text, symbol=self.symbol, source=self.source,
                    form_type=self.form_type, filed_date=self.filed_date,
                    accession=self.accession, section=self.section,
                    heading=self.heading, url=self.url)

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkRecord":
        return cls(**{k: d.get(k) for k in
                      ("text", "symbol", "source", "form_type", "filed_date",
                       "accession", "section", "heading", "url")
                      if d.get(k) is not None})

    def citation_label(self) -> str:
        bits = [b for b in (self.form_type, self.heading or self.section) if b]
        if self.filed_date:
            bits.append(f"filed {self.filed_date}")
        return ", ".join(bits)


@dataclass
class ChunkHit:
    score: float
    record: ChunkRecord


def _index_dir() -> Path:
    """Root directory for FAISS indexes, read from settings on every call.

    Deliberately not captured at import time: tests redirect it to a tmp_path,
    and in production it must resolve to the mounted volume
    (FUND_INDEX_DIR=/data/fund_index) rather than relative to the cwd.
    """
    from app.config import get_settings

    return Path(get_settings().fund_index_dir)


def _limits() -> Tuple[int, int]:
    from app.config import get_settings

    s = get_settings()
    return (getattr(s, "factsheet_max_chunks", 1500),
            getattr(s, "factsheet_max_doc_bytes", 4_000_000))


@lru_cache(maxsize=1)
def _model():
    """Load the embedding model once per process — constructing
    SentenceTransformer per call costs seconds of disk load each time."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(_EMBED_MODEL)


def _embed(texts: List[str]):
    return _model().encode(texts, normalize_embeddings=True,
                           show_progress_bar=False, batch_size=32)


# ── chunking ──────────────────────────────────────────────────────────────────

def chunk_section(text: str, words_per_chunk: int = _WORDS_PER_CHUNK,
                  overlap: int = _CHUNK_OVERLAP) -> List[str]:
    """Split one section's text into overlapping word windows.

    Callers apply this per section, never across the whole document, so a chunk
    never straddles two sections and always inherits one section's metadata.
    """
    words = text.split()
    if not words:
        return []
    if len(words) <= words_per_chunk:
        return [" ".join(words)]

    stride = max(1, words_per_chunk - overlap)
    out = []
    i = 0
    while i < len(words):
        piece = " ".join(words[i:i + words_per_chunk])
        if piece.strip():
            out.append(piece)
        if i + words_per_chunk >= len(words):
            break
        i += stride
    return out


def chunks_from_document(doc) -> List[ChunkRecord]:
    """Chunk a RawDocument into provenance-carrying records.

    Falls back to chunking the whole text when the parser found no sections, so
    an unsectionable filing still yields usable (if uncited) passages.
    """
    ref = doc.ref
    base = dict(symbol=ref.symbol, source=ref.source, form_type=ref.form_type,
                filed_date=ref.filed_date, accession=ref.accession, url=ref.url)

    records: List[ChunkRecord] = []
    if doc.sections:
        for sec in doc.sections:
            body = doc.text[sec.start:sec.end].strip()
            for piece in chunk_section(body):
                records.append(ChunkRecord(text=piece, section=sec.key,
                                           heading=sec.heading, **base))
    else:
        for piece in chunk_section(doc.text):
            records.append(ChunkRecord(text=piece, **base))

    max_chunks, _ = _limits()
    if len(records) > max_chunks:
        logger.info("RAG: %s produced %d chunks, capping at %d",
                    ref.symbol, len(records), max_chunks)
        records = records[:max_chunks]
    return records


# ── index build / load ────────────────────────────────────────────────────────

def fund_index_exists(symbol: str) -> bool:
    """True only for an index of the CURRENT schema version."""
    sym = symbol.upper().strip()
    base = _index_dir() / sym
    if not (base / "index.faiss").exists():
        return False
    return _read_meta(base) is not None


def _read_meta(base: Path) -> Optional[dict]:
    try:
        meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return None                      # v1 index (no meta.json) or corrupt
    if int(meta.get("schema_version") or 0) != SCHEMA_VERSION:
        return None
    return meta


def build_index(symbol: str, records: List[ChunkRecord],
                doc_keys: Optional[List[str]] = None) -> bool:
    """Embed records and write the index. True on success."""
    sym = symbol.upper().strip()
    if not records:
        return False
    try:
        import faiss
    except ImportError:
        logger.debug("faiss-cpu not installed — RAG skipped")
        return False

    with _ingest_sem:
        try:
            embeddings = _embed([r.text for r in records]).astype("float32")
            index = faiss.IndexFlatIP(embeddings.shape[1])
            index.add(embeddings)

            base = _index_dir() / sym
            base.mkdir(parents=True, exist_ok=True)
            faiss.write_index(index, str(base / "index.faiss"))
            (base / "chunks.json").write_text(
                json.dumps([r.to_dict() for r in records], ensure_ascii=False),
                encoding="utf-8")
            (base / "meta.json").write_text(json.dumps({
                "schema_version": SCHEMA_VERSION,
                "symbol": sym,
                "embed_model": _EMBED_MODEL,
                "chunk_count": len(records),
                "doc_keys": doc_keys or [],
            }), encoding="utf-8")

            with _lock:
                _cache[sym] = (index, records)
            logger.info("RAG: indexed %d chunks for %s", len(records), sym)
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.error("build_index(%s): %s", sym, e)
            return False


def _load(sym: str):
    """(index, records) from cache or disk, or None."""
    with _lock:
        entry = _cache.get(sym)
    if entry is not None:
        return entry
    try:
        import faiss
    except ImportError:
        return None

    base = _index_dir() / sym
    if not (base / "index.faiss").exists():
        return None
    if _read_meta(base) is None:
        logger.info("RAG: %s index is an older schema — treating as absent", sym)
        return None
    try:
        index = faiss.read_index(str(base / "index.faiss"))
        raw = json.loads((base / "chunks.json").read_text(encoding="utf-8"))
        records = [ChunkRecord.from_dict(d) for d in raw if isinstance(d, dict)]
        if not records:
            return None
        entry = (index, records)
        with _lock:
            _cache[sym] = entry
        return entry
    except Exception as e:
        logger.debug("RAG load(%s): %s", sym, e)
        return None


def drop_index(symbol: str) -> None:
    """Forget a fund's index (used before a forced rebuild)."""
    sym = symbol.upper().strip()
    with _lock:
        _cache.pop(sym, None)


# ── retrieval ─────────────────────────────────────────────────────────────────

def retrieve(symbol: str, question: str, k: int = 6,
             min_score: float = _DEFAULT_MIN_SCORE,
             max_per_section: int = _MAX_PER_SECTION) -> List[ChunkHit]:
    """Top passages for a question, best first.

    Returns [] rather than the nearest available text when nothing clears
    `min_score` — the caller can then say honestly that the filings don't
    cover the question instead of answering from an unrelated paragraph.
    """
    sym = symbol.upper().strip()
    if not question.strip():
        return []
    entry = _load(sym)
    if entry is None:
        return []

    index, records = entry
    try:
        q_emb = _embed([question]).astype("float32")
        # Over-fetch so per-section capping still has candidates to fall back on.
        depth = min(len(records), max(k * 4, k))
        scores, idxs = index.search(q_emb, depth)
    except ImportError:
        return []
    except Exception as e:
        logger.debug("retrieve(%s): %s", sym, e)
        return []

    hits: List[ChunkHit] = []
    per_section: dict = {}
    for score, i in zip(scores[0], idxs[0]):
        if i < 0 or i >= len(records):
            continue
        if float(score) < min_score:
            continue
        rec = records[i]
        if per_section.get(rec.section, 0) >= max_per_section:
            continue
        per_section[rec.section] = per_section.get(rec.section, 0) + 1
        hits.append(ChunkHit(score=float(score), record=rec))
        if len(hits) >= k:
            break
    return hits


def query_fund_docs(symbol: str, question: str, k: int = 4) -> str:
    """Top-k passages as a plain context string.

    Kept for callers that only want text (app/chat.py). New code should use
    retrieve(), which preserves the metadata needed for citations.
    """
    hits = retrieve(symbol, question, k=k)
    return "\n\n".join(h.record.text for h in hits)


# ── ingest ────────────────────────────────────────────────────────────────────

def ingest_fund_docs(symbol: str, market: str = "us") -> bool:
    """Fetch this fund's best disclosure document and index it.

    Returns False when no document could be obtained — including the deliberate
    case where the fetcher refused to hand back a filing it could not prove
    belongs to this fund.
    """
    from app.docs import get_fetcher

    sym = symbol.upper().strip()
    fetcher = get_fetcher(sym, market)
    if fetcher is None:
        logger.info("RAG: no document service for market %r", market)
        return False

    try:
        refs = fetcher.find_documents(sym)
    except Exception as e:
        logger.warning("RAG: find_documents(%s) failed: %s", sym, e)
        return False
    if not refs:
        return False

    for ref in refs:
        if ref.doc_role != "primary":
            continue
        try:
            doc = fetcher.fetch(ref)
        except Exception as e:
            logger.warning("RAG: fetch(%s %s) failed: %s", sym, ref.form_type, e)
            continue
        if doc is None:
            continue
        records = chunks_from_document(doc)
        if not records:
            continue
        if build_index(sym, records, doc_keys=[ref.doc_key]):
            return True
    logger.info("RAG: no usable document for %s", sym)
    return False
