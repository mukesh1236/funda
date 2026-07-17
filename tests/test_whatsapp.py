"""WhatsApp assistant tests — Phase 1 (store linking, inbound routing,
Twilio signature verification, opt-out) and Phase 2 (personalized morning
brief). All provider I/O is avoided (webhook replies via TwiML / mocked
sends; no outbound network calls made)."""
import base64
import hashlib
import hmac
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import jobs
from app.models import ConsensusOut
from app.store import RecommendationStore
from app.whatsapp import linking


def _store(tmp_path):
    return RecommendationStore(str(tmp_path / "wa.db"))


# ── store: linking codes ──────────────────────────────────────────────────────
def test_link_code_is_single_use_and_binds(tmp_path):
    s = _store(tmp_path)
    uid = s.create_user("a@t.com", "h", "Al")
    code = s.create_whatsapp_link_code(uid)
    assert code.isdigit() and len(code) == 6

    assert s.consume_whatsapp_link_code(code) == uid
    assert s.consume_whatsapp_link_code(code) is None   # single-use

    s.bind_whatsapp(uid, "+14155550001")
    u = s.get_user_by_whatsapp("+14155550001")
    assert u and u["email"] == "a@t.com"


def test_expired_link_code_rejected(tmp_path):
    s = _store(tmp_path)
    uid = s.create_user("b@t.com", "h", "Bo")
    code = s.create_whatsapp_link_code(uid, ttl_minutes=-1)   # already expired
    assert s.consume_whatsapp_link_code(code) is None


def test_opt_out_hides_user(tmp_path):
    s = _store(tmp_path)
    uid = s.create_user("c@t.com", "h", "Ci")
    s.bind_whatsapp(uid, "+14155550002")
    assert s.get_user_by_whatsapp("+14155550002") is not None
    assert s.whatsapp_opt_out("+14155550002") is True
    assert s.get_user_by_whatsapp("+14155550002") is None   # opted out → invisible


def test_list_whatsapp_opted_in_excludes_opted_out(tmp_path):
    s = _store(tmp_path)
    u1 = s.create_user("d1@t.com", "h", "D1")
    u2 = s.create_user("d2@t.com", "h", "D2")
    s.bind_whatsapp(u1, "+14155550003")
    s.bind_whatsapp(u2, "+14155550004")
    s.whatsapp_opt_out("+14155550004")

    links = s.list_whatsapp_opted_in()
    assert {l["user_id"] for l in links} == {u1}
    assert {l["phone_e164"] for l in links} == {"+14155550003"}


def test_extract_code_is_strict():
    assert linking.extract_code("123456") == "123456"
    assert linking.extract_code("  123456 ") == "123456"
    assert linking.extract_code("my code is 123456") is None   # not a bare code
    assert linking.extract_code("how's NVDA?") is None


# ── webhook routing (via TestClient; no Twilio auth token → sig check skipped) ──
def _client():
    from app.main import app
    return TestClient(app)


def test_webhook_unlinked_phone_gets_link_instructions():
    c = _client()
    r = c.post("/api/whatsapp/webhook",
               data={"From": "whatsapp:+14155550100", "Body": "top buys"})
    assert r.status_code == 200
    assert "Connect" in r.text or "connect" in r.text
    assert "code" in r.text.lower()


def test_webhook_full_link_then_ask_then_stop():
    import uuid
    c = _client()
    email = f"flow2-{uuid.uuid4().hex[:8]}@t.com"   # unique per run: this hits the
    # real dev DB (webhook.py owns its own store instance), so a fixed email
    # would 409 as "already registered" on a second run of the suite.
    c.post("/api/auth/register", json={"email": email, "password": "longpass123"})
    code = c.post("/api/whatsapp/link-code").json()["code"]
    phone = "whatsapp:+14155550101"

    linked = c.post("/api/whatsapp/webhook", data={"From": phone, "Body": code})
    assert "Connected" in linked.text

    answered = c.post("/api/whatsapp/webhook", data={"From": phone, "Body": "top buys today"})
    assert answered.status_code == 200
    assert "not investment advice" in answered.text   # disclaimer always appended

    stopped = c.post("/api/whatsapp/webhook", data={"From": phone, "Body": "STOP"})
    assert "unsubscribed" in stopped.text


def test_link_code_endpoint_requires_auth():
    c = _client()
    # fresh client with no cookie
    from app.main import app
    with TestClient(app) as anon:
        r = anon.post("/api/whatsapp/link-code")
    assert r.status_code == 401


def test_bad_twilio_signature_rejected(tmp_path, monkeypatch):
    """With an auth token configured, a request without a valid signature is
    rejected (403)."""
    import app.whatsapp.webhook as wh
    monkeypatch.setattr(wh._settings, "twilio_auth_token", "secrettoken")
    c = _client()
    r = c.post("/api/whatsapp/webhook",
               data={"From": "whatsapp:+14155550102", "Body": "hi"},
               headers={"X-Twilio-Signature": "wrong"})
    assert r.status_code == 403


def test_valid_twilio_signature_accepted(tmp_path, monkeypatch):
    import app.whatsapp.webhook as wh
    token = "secrettoken"
    monkeypatch.setattr(wh._settings, "twilio_auth_token", token)
    url = wh._settings.app_base_url.rstrip("/") + "/api/whatsapp/webhook"
    params = {"From": "whatsapp:+14155550103", "Body": "hello"}
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    sig = base64.b64encode(
        hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()

    c = _client()
    r = c.post("/api/whatsapp/webhook", data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200   # signature verified → routed normally


# ── Phase 2: personalized morning brief ────────────────────────────────────────
def _consensus(symbol: str, score: int) -> ConsensusOut:
    return ConsensusOut(
        symbol=symbol, buy_count=max(score, 0), hold_count=1,
        sell_count=max(-score, 0), total_count=abs(score) + 1,
        consensus_score=score,
    )


def test_format_brief_uses_watchlist_when_present():
    by_symbol = {"NVDA": _consensus("NVDA", 5), "AAPL": _consensus("AAPL", 2)}
    body = jobs._format_brief(["NVDA"], ["SPY"], by_symbol, fallback_top=[])
    assert "NVDA" in body and "consensus +5" in body
    assert "SPY" in body
    assert "Today's top picks" not in body


def test_format_brief_falls_back_to_top_picks_when_nothing_pinned():
    fallback = [_consensus("MSFT", 4), _consensus("TSLA", 1)]
    body = jobs._format_brief([], [], {}, fallback_top=fallback)
    assert "Today's top picks" in body
    assert "MSFT" in body


def test_send_whatsapp_briefs_skips_when_not_configured(tmp_path, monkeypatch):
    s = _store(tmp_path)
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "twilio_account_sid", "")
    sent = jobs.send_whatsapp_briefs(s, settings)
    assert sent == 0


def test_send_whatsapp_briefs_sends_personalized_message_per_user(tmp_path, monkeypatch):
    s = _store(tmp_path)
    uid = s.create_user("brief@t.com", "h", "Bri")
    s.bind_whatsapp(uid, "+14155550200")
    s.add_watchlist(uid, "NVDA", "default", "2026-01-01", 100.0, "Nvidia")

    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "twilio_account_sid", "sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "tok")
    monkeypatch.setattr(settings, "twilio_whatsapp_from", "whatsapp:+14155238886")

    class _FakeFeed:
        def __init__(self, stocks):
            self.stocks = stocks

    def _fake_build_feed(store, days=1, market="us"):
        return _FakeFeed([_consensus("NVDA", 5)] if market == "us" else [])

    monkeypatch.setattr(jobs, "build_feed", _fake_build_feed)

    sent_messages = []
    monkeypatch.setattr(
        jobs.WhatsAppClient, "send_text",
        lambda self, to_phone, body: sent_messages.append((to_phone, body)) or True,
    )

    sent = jobs.send_whatsapp_briefs(s, settings)
    assert sent == 1
    assert len(sent_messages) == 1
    to_phone, body = sent_messages[0]
    assert to_phone == "+14155550200"
    assert "NVDA" in body
