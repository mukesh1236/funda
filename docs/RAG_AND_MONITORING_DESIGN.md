# RAG Chatbot + Evaluation & Monitoring — Design Doc

Design for upgrading the `/api/chat` assistant from **context-stuffing** to a
real **Retrieval-Augmented Generation (RAG)** system, plus an evaluation and
drift-monitoring layer.

**Decisions locked in:**
- **Embeddings:** local `sentence-transformers/all-MiniLM-L6-v2` (384-dim),
  with a **Gemini embedding API fallback** (mirrors the existing multi-provider
  LLM pattern in `app/llm.py`).
- **Vector store:** **FAISS** in-process (`IndexFlatIP` on L2-normalized
  vectors = cosine similarity), persisted to disk next to `recommendations.db`.
- **Generation:** reuse existing `app/llm.py` (Gemini / Grok / Ollama).
- **Routing:** keep the existing rule-first engine (`_rule_answer`); RAG only
  handles open-ended questions that rules don't catch.

> Status: design only. Nothing here is implemented yet. Build order is in
> §7 (phases). This is the substrate for backlog items #4 (RAG) and #5
> (monitoring/drift) from the AIML improvement plan.

---

## 1. Why (the gap today)

`app/chat.py` currently calls `_fmt_feed()` which dumps the **top-40 stocks**
into the LLM prompt, explicitly capped "to stay within free-tier limits." That
is context-stuffing, not retrieval:

- It does **no relevance ranking** — question about one stock still ships 40.
- It **doesn't scale** past the cap; once the universe grows, relevant data
  falls off the end.
- There is **no grounding signal** — we can't tell which facts the answer used,
  so we can't measure hallucination.

RAG fixes all three: embed a corpus → retrieve only what's relevant → generate
grounded in those chunks → measure groundedness.

---

## 2. Corpus — what we embed

All four are short, dated, symbol-tagged text snippets already produced by the
app. No new data sources needed.

| Doc type | Source in code | Example rendered text |
|---|---|---|
| `news` | `app/sources/market_news.py` headlines | `[NEWS 2026-06-20 NVDA] Nvidia data-center revenue beats; guidance raised` |
| `analyst_note` | `recommendations.note` (`store.py`) | `[ANALYST UBS NVDA 2026-06-19] Buy, PT $275→$280, raised on AI demand` |
| `summary` | `build_rule_summary()` headline + reasons (`summarize.py`) | `[SUMMARY NVDA] Strongly bullish · 28/34 rate Buy · +18% to target …` |
| `fundamentals` | `Fundamentals.notes` (`models.py`) | `[FUNDAMENTALS NVDA] P/E 38 implies premium vs sector; FCF margin strong` |

### Document schema
```python
@dataclass
class RagDoc:
    doc_id: str        # stable hash of (doc_type, symbol, date, text)
    text: str          # the rendered string above
    symbol: str | None # for metadata-filtered retrieval
    date: str          # ISO; enables recency weighting / filtering
    doc_type: str      # news | analyst_note | summary | fundamentals
    source: str        # yahoo, finnhub, ubs, rule, ...
```

### Chunking
Snippets are already short (1–3 sentences), so **one doc = one chunk**. No
splitter needed initially. If `summary`/`fundamentals` notes grow long, add a
sentence-window splitter (≈256 tokens, 1-sentence overlap) — deferred.

---

## 3. Embeddings — local first, Gemini fallback

New module `app/rag/embeddings.py`, mirroring `app/llm.py`'s provider pattern.

```python
def embed_texts(texts: list[str], settings) -> list[list[float]]:
    """384-dim vectors. Try local sentence-transformers; on ImportError or
    load failure, fall back to the Gemini embedding API; else raise."""
```

- **Primary:** `sentence-transformers/all-MiniLM-L6-v2`. Free, offline, ~80MB,
  no data leaves the box. Model loaded once and cached at module level.
- **Fallback:** Gemini `text-embedding-004` via the key already in settings.
  > ⚠️ Dimension mismatch: Gemini returns 768-dim, MiniLM 384-dim. The index
  > is built with **one** provider. Persist the provider+dimension in the index
  > sidecar metadata (§4) and **refuse to mix** — if the active provider's dim
  > ≠ the index's dim, rebuild the index. Never query a 384-dim index with a
  > 768-dim vector.
- **Normalize** every vector to unit length so FAISS inner-product = cosine.

New settings (`app/config.py`): `EMBEDDING_PROVIDER=auto|local|gemini`,
`EMBEDDING_MODEL_LOCAL`, `GEMINI_EMBED_MODEL`.

New deps (`requirements.txt`): `sentence-transformers`, `faiss-cpu`.

---

## 4. Vector store — FAISS in-process

New module `app/rag/index.py`.

- **Index type:** `IndexFlatIP` over L2-normalized vectors (exact cosine).
  Corpus is small (hundreds–low-thousands of docs) so exact search is instant;
  no need for IVF/HNSW yet.
- **ID mapping:** wrap in `IndexIDMap2`; FAISS stores int64 ids → keep a
  `id → RagDoc` sidecar (JSON or a `rag_docs` SQLite table) for metadata +
  text retrieval after a search returns ids.
- **Persistence:** `data/rag.faiss` + `data/rag_meta.json` (holds
  `{provider, dim, built_at, doc_count}`). Rebuilt by the daily job; loaded
  once at process start.
- **Metadata filtering:** FAISS Flat has no native filter. Two options:
  1. **Over-fetch + post-filter** (retrieve top-50, keep those matching
     `symbol`/`date`, take top-k) — simplest, fine at this scale. ✅ start here.
  2. Per-symbol sub-indexes — defer unless corpus explodes.

```python
class RagIndex:
    def build(self, docs: list[RagDoc], settings) -> None: ...
    def search(self, query: str, k: int = 6, *, symbol: str | None = None,
               settings) -> list[tuple[RagDoc, float]]: ...   # (doc, score)
    def save(self, path) / load(self, path) -> None: ...
```

---

## 5. Retrieval + generation — upgrade `chat.py`

Keep the 3-layer structure; insert RAG as the new layer 2.

```
answer_question():
  1. _rule_answer()           # unchanged — fast, free, deterministic
  2. RAG (open-ended):
       symbol = detect_symbol(question)
       hits   = index.search(question, k=6, symbol=symbol)   # filtered
       prompt = _rag_prompt(question, hits)                  # ONLY retrieved text
       answer = generate_narrative(prompt, settings)
       return answer, citations=[h.doc_id for h in hits]
  3. _overview()              # unchanged — graceful fallback if LLM down
```

- **Prompt** keeps the existing strict rules ("use ONLY the provided context,
  no training knowledge, 2–5 sentences"). Difference: context is now the
  **retrieved chunks**, each prefixed with `[n]` so the model can cite.
- **Citations:** `ChatResponse` gains `citations: list[Citation]`
  (`{doc_id, doc_type, symbol, source, snippet}`). UI can render "Sources:".
  Citations are also what the eval layer (§6) scores groundedness against.
- **Recency:** blend FAISS score with the existing `_recency_weight` decay so
  fresh news outranks stale notes of equal similarity.

---

## 6. Evaluation harness (item #5, part A)

New: `tests/rag_eval.jsonl` (golden set, ~20–30 Q&A) + `app/rag/eval.py`.

Golden record:
```json
{"q": "Why is NVDA bullish?", "relevant_doc_ids": ["..."],
 "must_mention": ["target", "buy"]}
```

| Metric | Question it answers | Method |
|---|---|---|
| **Retrieval hit@k / MRR** | Did we retrieve the right doc? | compare returned ids vs `relevant_doc_ids` |
| **Faithfulness** | Is every claim supported by retrieved context? | LLM-as-judge: "for each sentence, is it grounded in these chunks? yes/no" → % grounded |
| **Answer relevance** | Did it answer the question? | LLM-judge 1–5 |
| **Context precision** | Were retrieved chunks actually used / relevant? | judge + overlap |
| **Latency / cost** | p50/p95 ms, token estimate | timer around the call |

> These are the **RAGAS** metrics (faithfulness, answer-relevance,
> context-precision/recall). Implement them directly (better interview story —
> shows you understand them) rather than importing the library.

Run as `pytest -m rag_eval` (opt-in; needs an LLM key) and from a CLI script
for ad-hoc reporting.

---

## 7. Monitoring & drift (item #5, parts B–D)

### B. Production logging — new `chat_logs` table
`question, detected_symbol, retrieved_doc_ids, answer, provider, latency_ms,
top1_score, faithfulness_score (async/sampled), ts`. Daily aggregates roll up
into the **existing `metrics_daily`** table (same pattern already in `store.py`).

### C. Three drift signals (name all three — shows range)
1. **Embedding / query drift:** track mean cosine distance of incoming query
   embeddings to a reference centroid (or PSI on score buckets). Rising → users
   asking things the corpus doesn't cover.
2. **Retrieval drift:** rolling avg of `top1_score`. Falling → stale index or
   off-distribution questions.
3. **Answer-quality drift:** rolling faithfulness/relevance from the LLM-judge
   on **sampled** live traffic. Falling → provider regression or corpus rot.

### D. Tie-in to the hit-probability model (#1)
The same `metrics_daily` job tracks **calibration** of target-hit predictions
as outcomes resolve (rolling **Brier score**). One "observability" story
covering both the RAG bot and the ML model.

---

## 8. File layout

```
app/rag/
  __init__.py
  corpus.py        # build RagDoc[] from store + sources
  embeddings.py    # local-first, Gemini-fallback embed_texts()
  index.py         # FAISS RagIndex: build/search/save/load
  retrieve.py      # query → filtered top-k + recency blend
  eval.py          # RAGAS-style metrics + LLM-judge
data/
  rag.faiss        # persisted index (gitignored)
  rag_meta.json    # {provider, dim, built_at, doc_count}
tests/
  rag_eval.jsonl   # golden Q&A set
  test_rag.py      # corpus build, dim-guard, retrieval, fallback
docs/
  RAG_AND_MONITORING_DESIGN.md   # this file
```

Wire-in points: `chat.py` (new layer 2), `models.py` (`Citation`,
`ChatResponse.citations`, `chat_logs`), `jobs.py` (rebuild index in daily job),
`store.py` (`chat_logs` + `metrics_daily` rows), `config.py` (embed settings),
`requirements.txt` (`sentence-transformers`, `faiss-cpu`).

---

## 9. Build phases

- **Phase 1 — RAG core:** `corpus` + `embeddings` (local+fallback) + FAISS
  `index` + `retrieve` wired into `chat.py` with citations; index rebuilt in
  the daily job; `test_rag.py`. Ship a working grounded bot.
- **Phase 2 — Evaluation:** `rag_eval.jsonl` + `eval.py` (hit@k, faithfulness,
  relevance) + `pytest -m rag_eval`.
- **Phase 3 — Monitoring:** `chat_logs` + `metrics_daily` aggregates + the
  three drift signals + a small `/api/admin/rag-metrics` view.

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Embedding-dim mismatch (local 384 vs Gemini 768) | Store provider+dim in `rag_meta.json`; rebuild if active provider differs; never cross-query. |
| `sentence-transformers` is a heavy dep (~torch) | Lazy-import; if it fails, fall back to Gemini embeddings so the app still runs (graceful degradation, consistent with existing design). |
| Small corpus → thin retrieval | Over-fetch + recency blend; rule-first layer still answers most common questions. |
| LLM-judge cost/flakiness | Sample (e.g. 10%) live traffic; run full judge only in the opt-in eval suite. |
| Stale index | Rebuild in the daily scheduler job; `built_at` surfaced in admin metrics. |

---

## 11. Interview talking points

- *"Local MiniLM embeddings with a Gemini fallback — retrieval is free and
  offline; the LLM is only used for final generation."*
- *"FAISS `IndexFlatIP` on normalized vectors = exact cosine; corpus is small
  so I skipped ANN — right-sizing the tool."*
- *"I evaluate with faithfulness, answer-relevance, and context-precision
  against a golden set using LLM-as-judge (the RAGAS metrics)."*
- *"Three drift signals: query-embedding drift, retrieval-similarity drift, and
  answer-faithfulness drift on sampled traffic — plus Brier-score calibration
  monitoring on the prediction model."*
- *"Rule-first routing means common questions never hit the LLM — cheaper,
  faster, deterministic; RAG handles the long tail."*
