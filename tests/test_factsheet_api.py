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

    def test_answer_with_invented_number_is_not_shipped(self, client):
        """Regression: the ask endpoint told the model 'never calculate a
        number' but never checked its answer, unlike the fact-sheet summary
        path which enforces this in code. A hallucinated fee/return figure
        must not reach the user just because it came from the free-text path
        instead of the structured summary path."""
        with patch("app.fund_rag.retrieve", return_value=[self._hit()]), \
             patch("app.llm.generate_narrative",
                   return_value="Over 20 years you would pay $6,625 in fees."), \
             patch("app.factsheet.build_facts",
                   return_value={"expense_ratio_pct": 0.55, "annual_cost_usd": 55.0}), \
             patch("app.factsheet.budget_ok", return_value=True):
            r = client.post("/api/funds/ACMGX/factsheet/ask",
                            json={"question": "what will this cost me over 20 years?"})
        body = r.json()
        assert body["source"] == "unverifiable"
        assert "$6,625" not in body["answer"]

    def test_answer_with_only_grounded_numbers_is_shipped(self, client):
        with patch("app.fund_rag.retrieve", return_value=[self._hit()]), \
             patch("app.llm.generate_narrative",
                   return_value="The expense ratio is 0.55%, about $55 a year. [1]"), \
             patch("app.factsheet.build_facts",
                   return_value={"expense_ratio_pct": 0.55, "annual_cost_usd": 55.0}), \
             patch("app.factsheet.budget_ok", return_value=True):
            r = client.post("/api/funds/ACMGX/factsheet/ask",
                            json={"question": "what does this cost?"})
        body = r.json()
        assert body["source"] == "llm"
        assert "$55" in body["answer"]

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


class TestReviewRegressions:

    def test_malformed_cached_blob_does_not_500(self, client):
        """The UI polls this every 12s; a legacy or truncated row must trigger
        a rebuild, not an error response."""
        st = funds_mod._store
        st.save_factsheet("BADX", "acc-1", '{"summary": {"headline": 123, "sections": "nope"}}',
                          "m", 1)
        st.set_factsheet_status("BADX", "ready", "", doc_key="acc-1")
        with patch.object(funds_mod, "_factsheet_bg"):
            r = client.get("/api/funds/BADX/factsheet")
        assert r.status_code == 200
        assert r.json()["status"] != "ready"

    def test_stale_schema_version_is_not_served(self, client):
        """Bumping the summary schema must retire old rows, or a stale shape
        is served as 'ready' forever and never regenerates."""
        from app.factsheet import SUMMARY_SCHEMA_VERSION

        st = funds_mod._store
        st.save_factsheet("OLDX", "acc-1", json.dumps({"summary": {"headline": "old"}}),
                          "m", SUMMARY_SCHEMA_VERSION - 1)
        st.set_factsheet_status("OLDX", "ready", "", doc_key="acc-1")
        with patch.object(funds_mod, "_factsheet_bg"):
            r = client.get("/api/funds/OLDX/factsheet")
        assert r.json()["status"] != "ready"

    def test_unavailable_expires_so_a_transient_outage_recovers(self, client):
        """'unavailable' covers an EDGAR 503 as well as a real mismatch. Sticky
        forever would let one outage disable a fund for every user."""
        from datetime import datetime, timedelta, timezone

        st = funds_mod._store
        st.set_factsheet_status("TMPX", "unavailable", "EDGAR was down")
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")
        with st._connect() as conn:
            conn.execute("UPDATE fund_factsheet_status SET updated_at = ? WHERE symbol = ?",
                         (old, "TMPX"))
        with patch.object(funds_mod, "_factsheet_bg") as bg:
            r = client.get("/api/funds/TMPX/factsheet")
        assert r.json()["status"] != "unavailable"
        assert bg.call_count == 1, "a stale failure should be retried"

    def test_recent_unavailable_is_still_honoured(self, client):
        st = funds_mod._store
        st.set_factsheet_status("FRSHX", "unavailable", "Could not be matched.")
        with patch.object(funds_mod, "_factsheet_bg") as bg:
            r = client.get("/api/funds/FRSHX/factsheet")
        assert r.json()["status"] == "unavailable"
        bg.assert_not_called()

    def test_dropped_refresh_does_not_burn_the_hourly_quota(self, client):
        """A refresh rejected because a build is already running shouldn't cost
        the user their one refresh per hour."""
        email = f"fs-{uuid.uuid4().hex[:12]}@example.com"
        client.post("/api/auth/register", json={"email": email, "password": "password123"})

        funds_mod._FS_PENDING.add("BUSYX")     # pretend a build is in flight
        try:
            with patch.object(funds_mod, "_factsheet_bg"):
                dropped = client.post("/api/funds/BUSYX/factsheet/refresh")
            assert dropped.status_code == 202
        finally:
            funds_mod._FS_PENDING.discard("BUSYX")

        with patch.object(funds_mod, "_factsheet_bg"):
            real = client.post("/api/funds/BUSYX/factsheet/refresh")
        assert real.status_code == 202, "the quota should not have been spent"

    def test_citation_numbering_matches_select_excerpts_when_a_section_is_empty(self, client):
        """Defensive-correctness test, not a reproduction of a live bug.

        select_excerpts() skips a section whose body is empty after
        stripping, and previously _citations_for() recomputed citation
        numbers independently with no such skip — so the two could in
        principle disagree on which ordinal maps to which section.

        In practice this codebase's own parser (app/docs/parse.py) always
        constructs a Section's span starting at its OWN heading line, so
        doc.text[start:end] always contains that heading text and can never
        strip to empty — confirmed directly against find_sections() before
        writing this test. So the scenario below cannot currently be produced
        by app/docs/edgar.py against a real filing.

        The fix (persisting an `empty` flag at storage time, using the same
        check select_excerpts uses, and having _citations_for skip flagged
        sections) is kept anyway: it removes a "recompute the same derived
        fact in two places" pattern that's a correctness risk on its own
        terms, and would matter the moment ANY document source constructs a
        Section whose span doesn't include its heading — plausible for a
        future non-EDGAR source (e.g. a PDF-based extractor) that isn't bound
        by this parser's convention. This test exercises that fix directly by
        constructing such a Section by hand.
        """
        import app.funds as funds_mod
        from app.docs.base import DocumentRef, RawDocument, Section
        from app.factsheet import select_excerpts

        # Deliberately exclude "Principal Investment Strategies" itself from
        # its own section's span, leaving only whitespace — a construction
        # today's parser never produces (see docstring above), used here to
        # exercise the empty-body path the fix guards.
        text = ("Investment Objective\nSeeks growth.\n"
                "Principal Investment Strategies\n   \n"
                "Principal Risks\nYou could lose money.\n"
                "Fees and Expenses\nThe ratio is 0.55%.\n")
        o = text.index("Investment Objective")
        s_heading = text.index("Principal Investment Strategies")
        s_body = text.index("\n", s_heading) + 1   # after the heading line
        r = text.index("Principal Risks")
        f = text.index("Fees and Expenses")
        doc = RawDocument(
            ref=DocumentRef(symbol="EMPTX", source="edgar", form_type="497K",
                            url="https://sec.gov/e.htm", accession="acc-e",
                            filed_date="2025-04-28"),
            text=text,
            sections=[Section("objective", "Investment Objective", o, s_heading),
                      Section("strategy", "Principal Investment Strategies", s_body, r),
                      Section("risks", "Principal Risks", r, f),
                      Section("fees", "Fees and Expenses", f, len(text))])

        excerpt_keys = [e["key"] for e in select_excerpts(doc)]
        assert "strategy" not in excerpt_keys, "the empty section must be skipped"
        assert excerpt_keys.index("risks") == 1, "risks is excerpt #2 (0-indexed 1)"

        # Persist exactly as _factsheet_bg does, including the empty flag.
        sections_json = json.dumps([
            {**sec.to_dict(), "empty": not doc.text[sec.start:sec.end].strip()}
            for sec in doc.sections])
        funds_mod._store.upsert_fund_document(
            {"symbol": "EMPTX", "source": "edgar", "form_type": "497K",
             "doc_role": "primary", "accession": "acc-e", "filed_date": "2025-04-28",
             "title": None, "url": "https://sec.gov/e.htm"},
            len(text), sections_json)

        citations = funds_mod._citations_for("EMPTX")
        cited_keys = [c.section for c in citations]
        assert cited_keys == excerpt_keys, (
            "citations shown to the user must number sections in exactly the "
            "same order select_excerpts used when the summary was generated")
        risks_citation = next(c for c in citations if c.section == "risks")
        assert risks_citation.n == 2, "risks must be citation #2, not #3"
