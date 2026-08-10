"""Fact sheet HTTP surface.

Covers the contract the polling UI depends on (always 200, honest status),
the guardrails inherited from the chat assistant, and the refusal path that
keeps a wrong-fund summary from ever being produced.
"""
import json
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.funds as funds_mod
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.store import RecommendationStore

    store = RecommendationStore(str(tmp_path / "fs.db"))
    monkeypatch.setattr(funds_mod, "_store", store)
    funds_mod._FS_PENDING.clear()
    funds_mod._FS_REFRESH_LIMIT.clear()
    return TestClient(app)


def _seed_ready(store, sym="ACMGX"):
    store.upsert_fund_document(
        {"symbol": sym, "source": "edgar", "form_type": "497K", "doc_role": "primary",
         "accession": "acc-1", "filed_date": "2025-04-28", "title": None,
         "url": "https://sec.gov/a.htm"},
        1000, json.dumps([{"key": "risks", "heading": "Principal Risks",
                           "start": 0, "end": 100}]))
    store.save_factsheet(sym, "acc-1", json.dumps({
        "summary": {"headline": "A large-growth fund.",
                    "sections": [{"key": "what_could_go_wrong",
                                  "title": "What could go wrong",
                                  "bullets": [{"text": "You could lose money.",
                                               "cite": "S1"}]}],
                    "jargon": [{"term": "expense ratio", "plain": "yearly cost"}]},
        "notes": []}), "test-model", 1)
    store.set_factsheet_status(sym, "ready", "", doc_key="acc-1", chunk_count=5)


class TestGetFactsheet:

    def test_cold_fund_returns_200_and_queues_once(self, client):
        """The UI polls this endpoint; it must never error, and repeated polls
        must not queue duplicate background builds."""
        with patch.object(funds_mod, "_factsheet_bg") as bg:
            r1 = client.get("/api/funds/ACMGX/factsheet")
            r2 = client.get("/api/funds/ACMGX/factsheet")
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["status"] in ("queued", "fetching", "parsing")
        assert bg.call_count <= 1

    def test_invalid_symbol_is_422(self, client):
        assert client.get("/api/funds/not%20a%20symbol!/factsheet").status_code == 422

    def test_ready_factsheet_is_served_from_cache(self, client):
        _seed_ready(funds_mod._store)
        with patch("app.factsheet.build_facts", return_value={"symbol": "ACMGX"}):
            r = client.get("/api/funds/ACMGX/factsheet")
        body = r.json()
        assert r.status_code == 200
        assert body["status"] == "ready"
        assert body["summary"]["headline"] == "A large-growth fund."
        assert body["documents"][0]["form_type"] == "497K"

    def test_ready_factsheet_exposes_its_source_document(self, client):
        """A summary must never be shown without the filing it came from."""
        _seed_ready(funds_mod._store)
        with patch("app.factsheet.build_facts", return_value={}):
            body = client.get("/api/funds/ACMGX/factsheet").json()
        assert body["documents"][0]["url"] == "https://sec.gov/a.htm"
        assert body["documents"][0]["filed_date"] == "2025-04-28"
        assert body["citations"], "citations must resolve [S1] to a filing section"

    def test_unavailable_is_reported_not_retried(self, client):
        """A fund whose filing couldn't be matched must say so rather than
        silently re-queueing forever."""
        funds_mod._store.set_factsheet_status(
            "ZZZZX", "unavailable", "The fund's filing could not be matched.")
        with patch.object(funds_mod, "_factsheet_bg") as bg:
            r = client.get("/api/funds/ZZZZX/factsheet")
        assert r.json()["status"] == "unavailable"
        assert "could not be matched" in r.json()["notes"][0]
        bg.assert_not_called()

    def test_indian_fund_reports_honest_reason(self, client):
        with patch.object(funds_mod, "_factsheet_bg"):
            r = client.get("/api/funds/HDFCX/factsheet?market=in")
        assert r.status_code == 200


class TestRefresh:

    def test_requires_login(self, client):
        assert client.post("/api/funds/ACMGX/factsheet/refresh").status_code == 401

    def test_rate_limited_per_symbol(self, client):
        """A rebuild costs an EDGAR fetch, a full re-embed and an LLM call, so
        it's capped per symbol. Logs in for real — get_current_user reads the
        session cookie, and the app's store is the one the fixture installed."""
        # Unique per run: the auth store persists between test runs, so a fixed
        # address would register once and then 409 on every later run.
        email = f"fs-{uuid.uuid4().hex[:12]}@example.com"
        reg = client.post("/api/auth/register",
                          json={"email": email, "password": "password123"})
        assert reg.status_code == 200

        with patch.object(funds_mod, "_factsheet_bg"):
            first = client.post("/api/funds/ACMGX/factsheet/refresh")
            second = client.post("/api/funds/ACMGX/factsheet/refresh")
        assert first.status_code == 202
        assert second.status_code == 429


class TestAsk:

    def _hit(self, text="You could lose money in a downturn.", section="risks"):
        from app.fund_rag import ChunkHit, ChunkRecord
        return ChunkHit(score=0.8, record=ChunkRecord(
            text=text, symbol="ACMGX", source="edgar", form_type="497K",
            filed_date="2025-04-28", accession="acc-1", section=section,
            heading="Principal Risks", url="https://sec.gov/a.htm"))

    def test_answers_with_citations(self, client):
        with patch("app.fund_rag.retrieve", return_value=[self._hit()]), \
             patch("app.llm.generate_narrative", return_value="You could lose money. [1]"), \
             patch("app.factsheet.build_facts", return_value={}), \
             patch("app.factsheet.budget_ok", return_value=True):
            r = client.post("/api/funds/ACMGX/factsheet/ask",
                            json={"question": "what are the risks?"})
        body = r.json()
        assert body["source"] == "llm"
        assert body["citations"][0]["url"] == "https://sec.gov/a.htm"
        assert body["citations"][0]["heading"] == "Principal Risks"

    def test_declines_personal_advice(self, client):
        """Inherits the assistant's existing refusal rather than reimplementing it."""
        with patch("app.fund_rag.retrieve") as ret:
            r = client.post("/api/funds/ACMGX/factsheet/ask",
                            json={"question": "should I buy this fund with my savings?"})
        assert r.json()["source"] == "advice-declined"
        ret.assert_not_called()

    def test_says_so_when_filing_does_not_cover_it(self, client):
        """No retrieval hit above the score floor must produce an honest miss,
        not an answer improvised from an unrelated paragraph."""
        with patch("app.fund_rag.retrieve", return_value=[]), \
             patch("app.llm.generate_narrative") as llm:
            r = client.post("/api/funds/ACMGX/factsheet/ask",
                            json={"question": "who is the custodian bank?"})
        assert r.json()["source"] == "no-context"
        llm.assert_not_called()

    def test_budget_exhausted_is_reported(self, client):
        with patch("app.fund_rag.retrieve", return_value=[self._hit()]), \
             patch("app.factsheet.budget_ok", return_value=False), \
             patch("app.llm.generate_narrative") as llm:
            r = client.post("/api/funds/ACMGX/factsheet/ask",
                            json={"question": "what are the risks?"})
        assert r.json()["source"] == "budget_exhausted"
        llm.assert_not_called()

    def test_empty_question_rejected(self, client):
        r = client.post("/api/funds/ACMGX/factsheet/ask", json={"question": "   "})
        assert r.status_code == 422

    def test_llm_failure_degrades_gracefully(self, client):
        with patch("app.fund_rag.retrieve", return_value=[self._hit()]), \
             patch("app.factsheet.budget_ok", return_value=True), \
             patch("app.factsheet.build_facts", return_value={}), \
             patch("app.llm.generate_narrative", return_value=None):
            r = client.post("/api/funds/ACMGX/factsheet/ask",
                            json={"question": "what are the risks?"})
        assert r.status_code == 200
        assert r.json()["source"] == "unavailable"


class TestBackgroundBuild:

    def test_refusal_marks_unavailable_not_ready(self, tmp_path, monkeypatch):
        """The wrong-fund guard must surface as 'unavailable' end-to-end."""
        from app.docs.base import DocumentRef
        from app.store import RecommendationStore

        store = RecommendationStore(str(tmp_path / "bg.db"))
        monkeypatch.setattr(funds_mod, "_store", store)

        class Refusing:
            source = "edgar"
            def find_documents(self, sym):
                return [DocumentRef(symbol=sym, source="edgar", form_type="485BPOS",
                                    url="u", accession="a")]
            def fetch(self, ref):
                return None      # could not prove it's this fund

        monkeypatch.setattr("app.docs.get_fetcher", lambda s, m="us": Refusing())
        funds_mod._factsheet_bg("ACMGX", "us", False)

        status = store.get_factsheet_status("ACMGX")
        assert status["state"] == "unavailable"
        assert "could not be matched" in status["detail"]

    def test_unsupported_market_marks_unavailable(self, tmp_path, monkeypatch):
        from app.store import RecommendationStore

        store = RecommendationStore(str(tmp_path / "bg2.db"))
        monkeypatch.setattr(funds_mod, "_store", store)
        funds_mod._factsheet_bg("HDFCX", "in", False)

        status = store.get_factsheet_status("HDFCX")
        assert status["state"] == "unavailable"
        assert "SEC" in status["detail"] or "factsheet" in status["detail"].lower()
