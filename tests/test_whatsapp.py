"""WhatsApp assistant (Phase 1) tests — store linking, inbound routing,
Twilio signature verification, and opt-out. All provider I/O is avoided
(webhook replies via TwiML; no outbound calls made)."""
import base64
import hashlib
import hmac
from unittest.mock import patch

from fastapi.testclient import TestClient

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
    c = _client()
    c.post("/api/auth/register", json={"email": "flow2@t.com", "password": "longpass123"})
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
