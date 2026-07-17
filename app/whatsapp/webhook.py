"""Inbound WhatsApp webhook + the authenticated link-code endpoint.

Routing for an inbound message:
  1. message is a 6-digit code   -> bind phone to account, confirm
  2. STOP / UNSUBSCRIBE          -> opt out (compliance)
  3. phone not linked            -> tell them how to link
  4. otherwise                   -> answer_question() and reply (grounded)

Replies use TwiML (synchronous, no extra API call). Twilio requests are
signature-verified when the auth token is configured.
"""
import base64
import hashlib
import hmac
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

from app.auth import get_current_user
from app.config import get_settings
from app.store import RecommendationStore
from app.whatsapp.client import WhatsAppClient
from app.whatsapp.linking import generate_link_code, try_link

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])

_settings = get_settings()
_store = RecommendationStore(_settings.recommendations_db_path)

_DISCLAIMER = "\n\n— AlphaFunds · analysis, not investment advice."
_STOP_WORDS = {"stop", "unsubscribe", "stop all", "cancel", "end", "quit"}

# Minimal in-memory inbound rate limit (per phone) — abuse guard.
_RATE: Dict[str, Deque[float]] = defaultdict(deque)
_RATE_LOCK = threading.Lock()
_RATE_MAX = 20        # messages
_RATE_WINDOW = 60.0   # seconds


def _rate_ok(phone: str) -> bool:
    now = time.time()
    with _RATE_LOCK:
        q = _RATE[phone]
        while q and now - q[0] > _RATE_WINDOW:
            q.popleft()
        if len(q) >= _RATE_MAX:
            return False
        q.append(now)
        return True


def _twiml(message: str) -> Response:
    body = f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Message>{escape(message)}</Message></Response>"
    return Response(content=body, media_type="application/xml")


def _twiml_empty() -> Response:
    return Response(content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response></Response>",
                    media_type="application/xml")


def _phone_from_twilio(raw: str) -> str:
    """'whatsapp:+14155551234' -> '+14155551234'."""
    return (raw or "").replace("whatsapp:", "").strip()


def _verify_twilio_signature(request: Request, url: str, params: Dict[str, str]) -> bool:
    """Twilio signs URL + sorted concatenated POST params with the auth token
    (HMAC-SHA1, base64). Skipped when no auth token is configured (dev/tests)."""
    token = _settings.twilio_auth_token
    if not token:
        return True   # not configured — allow (local dev / tests)
    signature = request.headers.get("X-Twilio-Signature", "")
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


# ── authenticated: mint a link code for the logged-in user ────────────────────
@router.post("/link-code")
def link_code(user: dict = Depends(get_current_user)):
    """Return a fresh 6-digit code plus the WhatsApp deep link + sandbox join
    phrase, so the website can render a one-tap 'Connect WhatsApp' flow."""
    code = generate_link_code(_store, user["id"])
    to = _phone_from_twilio(_settings.twilio_whatsapp_from) or ""
    join = _settings.whatsapp_sandbox_join
    # Pre-fill the message the user sends us: the join phrase (sandbox) + code.
    prefill = (f"{join}\n{code}" if join else code)
    wa_link = f"https://wa.me/{to.lstrip('+')}?text={prefill.replace(chr(10), '%0A').replace(' ', '%20')}" if to else None
    existing = _store.whatsapp_link_for_user(user["id"])
    return {
        "code": code,
        "expires_minutes": 10,
        "whatsapp_number": to or None,
        "sandbox_join": join or None,
        "wa_link": wa_link,
        "already_linked": bool(existing and existing.get("opted_in")),
    }


# ── public: Twilio inbound webhook ────────────────────────────────────────────
@router.post("/webhook")
async def webhook(request: Request, From: str = Form(""), Body: str = Form("")):
    # Build the URL Twilio signed (its configured public webhook URL). Using
    # app_base_url avoids proxy-rewritten scheme/host mismatches on Railway.
    url = _settings.app_base_url.rstrip("/") + "/api/whatsapp/webhook"
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    if not _verify_twilio_signature(request, url, params):
        logger.warning("Rejected WhatsApp webhook — bad Twilio signature.")
        raise HTTPException(403, detail="Invalid signature.")

    phone = _phone_from_twilio(From)
    text = (Body or "").strip()
    if not phone or not text:
        return _twiml_empty()

    if not _rate_ok(phone):
        return _twiml("You're sending messages too fast — give it a minute and try again.")

    # 1) Linking code
    linked_uid = try_link(_store, text, phone)
    if linked_uid is not None:
        user = _store.get_user_by_id(linked_uid)
        name = (user or {}).get("display_name") or "there"
        return _twiml(
            f"✅ Connected, {name}! You can now ask me anything about your stocks — "
            f"e.g. \"top buys today\", \"how's NVDA?\", or \"which analysts are most accurate?\". "
            f"Reply STOP anytime to unsubscribe.")

    # 2) Opt-out
    if text.lower() in _STOP_WORDS:
        _store.whatsapp_opt_out(phone)
        return _twiml("You've been unsubscribed. Send your 6-digit code again anytime to reconnect.")

    # 3) Not linked yet
    user = _store.get_user_by_whatsapp(phone)
    if user is None:
        return _twiml(
            "👋 Welcome to AlphaFunds. To connect, sign in at "
            f"{_settings.app_base_url} → Connect WhatsApp, and send me the 6-digit code it shows you.")

    # 4) Grounded answer via the same brain the website chat uses
    from app.chat import answer_question
    answer, error, source = answer_question(_store, _settings, text, market="us", symbol=None)
    try:
        _store.add_chat_answer(source)   # feeds the fallback-rate SLO
    except Exception as e:
        logger.debug("wa chat telemetry skipped: %s", e)
    if error:
        return _twiml("The assistant is temporarily unavailable — please try again shortly.")
    return _twiml((answer or "I couldn't find an answer for that.") + _DISCLAIMER)


def get_client() -> WhatsAppClient:
    """Outbound client (used by later phases / tests)."""
    return WhatsAppClient(_settings)
