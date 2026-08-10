"""Chunking, indexing and scored retrieval.

The embedding model can't be downloaded in every environment, so these tests
substitute a deterministic 3-dimensional embedder and run the REAL faiss index
underneath it. That keeps the logic that actually decides answer quality —
score floor, per-section capping, schema versioning — under test everywhere,
rather than skipping whenever model weights are unavailable.
"""
import json

import pytest

import app.fund_rag as rag
from app.docs.base import DocumentRef, RawDocument, Section

np = pytest.importorskip("numpy")
pytest.importorskip("faiss", reason="faiss-cpu not installed")


def fake_embed(texts):
    """Topic vector over (fees, risks, strategy), unit-normalised so that
    IndexFlatIP inner product equals cosine similarity — same contract as the
    real normalised MiniLM embeddings."""
    rows = []
    for t in texts:
        tl = t.lower()
        v = np.array([
            1.0 if any(w in tl for w in ("fee", "expense", "cost")) else 0.0,
            1.0 if any(w in tl for w in ("risk", "lose", "volat")) else 0.0,
            1.0 if any(w in tl for w in ("strateg", "index", "track")) else 0.0,
        ], dtype="float32")
        n = float(np.linalg.norm(v))
        rows.append(v / n if n else v)
    return np.array(rows, dtype="float32")


@pytest.fixture
def rag_env(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "_index_dir", lambda: tmp_path)
    monkeypatch.setattr(rag, "_embed", fake_embed)
    rag._cache.clear()
    return tmp_path


def rec(text, section="other", heading="", **kw):
    base = dict(symbol="TF", source="edgar", form_type="497K",
                filed_date="2025-04-28", accession="acc-1",
                url="https://sec.gov/x.htm")
    base.update(kw)
    return rag.ChunkRecord(text=text, section=section, heading=heading, **base)


FEE = rec("The annual operating expense ratio is 0.05% and you pay that fee yearly.",
          "fees", "Fees and Expenses")
RISK1 = rec("Stock market risk means you could lose money in a downturn.",
            "risks", "Principal Risks")
RISK2 = rec("Interest rate risk may cause the value to fall and you could lose value.",
            "risks", "Principal Risks")
RISK3 = rec("Concentration risk increases volatility and you could lose capital.",
            "risks", "Principal Risks")
STRAT = rec("The fund uses an indexing strategy to track a broad market index.",
            "strategy", "Principal Investment Strategies")


class TestChunking:

    def test_short_text_is_one_chunk(self):
        assert rag.chunk_section("a b c") == ["a b c"]

    def test_empty_text_yields_nothing(self):
        assert rag.chunk_section("   ") == []

    def test_long_text_splits_with_overlap(self):
        words = " ".join(f"w{i}" for i in range(600))
        chunks = rag.chunk_section(words, words_per_chunk=250, overlap=50)
        assert len(chunks) > 1
        # consecutive chunks must share their overlap region
        first_tail = chunks[0].split()[-50:]
        assert first_tail == chunks[1].split()[:50]

    def test_chunks_never_cross_a_section_boundary(self):
        """Each chunk inherits exactly one section's metadata — the property
        that makes a citation meaningful."""
        text = ("Fees and Expenses\n" + " ".join(f"f{i}" for i in range(400)) +
                "\nPrincipal Risks\n" + " ".join(f"r{i}" for i in range(400)))
        split = text.index("Principal Risks")
        doc = RawDocument(
            ref=DocumentRef(symbol="TF", source="edgar", form_type="497K",
                            url="u", accession="a"),
            text=text,
            sections=[Section("fees", "Fees and Expenses", 0, split),
                      Section("risks", "Principal Risks", split, len(text))])
        records = rag.chunks_from_document(doc)
        assert {r.section for r in records} == {"fees", "risks"}
        for r in records:
            if r.section == "fees":
                assert "r0 " not in r.text and not r.text.startswith("r")
            else:
                assert "f0 " not in r.text

    def test_document_without_sections_still_chunks(self):
        doc = RawDocument(
            ref=DocumentRef(symbol="TF", source="edgar", form_type="497K", url="u"),
            text=" ".join(f"w{i}" for i in range(300)), sections=[])
        records = rag.chunks_from_document(doc)
        assert records and all(r.section == "other" for r in records)

    def test_chunk_count_is_capped(self, monkeypatch):
        monkeypatch.setattr(rag, "_limits", lambda: (3, 4_000_000))
        doc = RawDocument(
            ref=DocumentRef(symbol="TF", source="edgar", form_type="497K", url="u"),
            text=" ".join(f"w{i}" for i in range(5000)), sections=[])
        assert len(rag.chunks_from_document(doc)) == 3


class TestRetrieval:

    def test_finds_the_on_topic_section(self, rag_env):
        assert rag.build_index("TF", [FEE, RISK1, STRAT]) is True
        hits = rag.retrieve("TF", "what are the fees and costs?")
        assert hits[0].record.section == "fees"

    def test_off_topic_question_returns_nothing(self, rag_env):
        """No hit above the floor must yield [] — so the caller can say the
        filings don't cover it rather than answering from a stray paragraph."""
        rag.build_index("TF", [FEE, RISK1, STRAT])
        assert rag.retrieve("TF", "who is the custodian bank?") == []

    def test_min_score_floor_is_applied(self, rag_env):
        rag.build_index("TF", [FEE, RISK1, STRAT])
        assert rag.retrieve("TF", "risk", min_score=0.99)
        assert rag.retrieve("TF", "risk", min_score=1.01) == []

    def test_one_section_cannot_monopolise_results(self, rag_env):
        """Three equally-scoring risk chunks must not crowd out everything."""
        rag.build_index("TF", [RISK1, RISK2, RISK3, FEE])
        hits = rag.retrieve("TF", "risk of losing money", max_per_section=2)
        assert sum(1 for h in hits if h.record.section == "risks") == 2

    def test_scores_are_returned_and_ordered(self, rag_env):
        rag.build_index("TF", [FEE, RISK1, STRAT])
        hits = rag.retrieve("TF", "expense fee cost")
        assert hits[0].score > 0
        assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)

    def test_provenance_round_trips_through_disk(self, rag_env):
        rag.build_index("TF", [FEE, RISK1])
        rag._cache.clear()                       # force a disk load
        hits = rag.retrieve("TF", "fees")
        assert hits[0].record.form_type == "497K"
        assert hits[0].record.filed_date == "2025-04-28"
        assert hits[0].record.url == "https://sec.gov/x.htm"
        assert "Fees and Expenses" in hits[0].record.citation_label()

    def test_missing_index_returns_empty(self, rag_env):
        assert rag.retrieve("NOSUCH", "anything") == []

    def test_blank_question_returns_empty(self, rag_env):
        rag.build_index("TF", [FEE])
        assert rag.retrieve("TF", "   ") == []

    def test_query_fund_docs_wrapper_returns_text(self, rag_env):
        rag.build_index("TF", [FEE, RISK1])
        ctx = rag.query_fund_docs("TF", "fees")
        assert "expense ratio" in ctx


class TestSchemaVersioning:

    def test_current_index_is_recognised(self, rag_env):
        rag.build_index("TF", [FEE])
        assert rag.fund_index_exists("TF") is True

    def test_v1_flat_string_index_is_treated_as_absent(self, rag_env):
        """v1 wrote a flat list of strings; reading it as records would crash."""
        base = rag_env / "OLD"
        base.mkdir()
        (base / "index.faiss").write_bytes(b"x")
        (base / "chunks.json").write_text(json.dumps(["flat v1 chunk"]))
        assert rag.fund_index_exists("OLD") is False
        assert rag.retrieve("OLD", "fees") == []

    def test_stale_schema_version_is_rebuilt(self, rag_env):
        rag.build_index("TF", [FEE])
        base = rag_env / "TF"
        meta = json.loads((base / "meta.json").read_text())
        meta["schema_version"] = rag.SCHEMA_VERSION - 1
        (base / "meta.json").write_text(json.dumps(meta))
        rag._cache.clear()
        assert rag.fund_index_exists("TF") is False

    def test_meta_records_the_source_documents(self, rag_env):
        rag.build_index("TF", [FEE], doc_keys=["acc-1"])
        meta = json.loads((rag_env / "TF" / "meta.json").read_text())
        assert meta["doc_keys"] == ["acc-1"]
        assert meta["chunk_count"] == 1


class TestIngest:

    def test_unsupported_market_is_not_an_error(self, rag_env):
        assert rag.ingest_fund_docs("HDFCTOP100", market="in") is False

    def test_returns_false_when_fetcher_refuses_document(self, rag_env, monkeypatch):
        """A fetcher that cannot prove the filing belongs to this fund returns
        None; ingest must report failure rather than index the wrong fund."""
        class Refusing:
            source = "edgar"
            def find_documents(self, sym):
                return [DocumentRef(symbol=sym, source="edgar", form_type="485BPOS",
                                    url="u", accession="a")]
            def fetch(self, ref):
                return None

        monkeypatch.setattr("app.docs.get_fetcher", lambda s, m="us": Refusing())
        assert rag.ingest_fund_docs("TF") is False

    def test_indexes_the_first_usable_document(self, rag_env, monkeypatch):
        text = "Principal Risks\nYou could lose money in a market downturn."
        class Working:
            source = "edgar"
            def find_documents(self, sym):
                return [DocumentRef(symbol=sym, source="edgar", form_type="497K",
                                    url="https://sec.gov/a.htm", accession="acc-9")]
            def fetch(self, ref):
                return RawDocument(ref=ref, text=text,
                                   sections=[Section("risks", "Principal Risks", 0, len(text))])

        monkeypatch.setattr("app.docs.get_fetcher", lambda s, m="us": Working())
        assert rag.ingest_fund_docs("TF") is True
        hits = rag.retrieve("TF", "risk of losing money")
        assert hits and hits[0].record.accession == "acc-9"
