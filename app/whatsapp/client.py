"""Outbound WhatsApp adapter. Twilio REST now (simple form-POST + basic auth,
no SDK dependency); Meta Cloud API can replace this behind the same
send_text() method later. Never raises on transient send failures — logs
instead, so a delivery hiccup never breaks the webhook response."""
import logging
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def _normalize_to(phone: str) -> str:
    """Twilio wants the WhatsApp channel prefix on both ends."""
    phone = phone.strip()
    return phone if phone.startswith("whatsapp:") else f"whatsapp:{phone}"


class WhatsAppClient:
    """Thin provider adapter. configured() lets callers no-op cleanly when
    creds aren't set (dev / tests)."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        s = self.settings
        return bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_whatsapp_from)

    def send_text(self, to_phone: str, body: str) -> bool:
        """Send a plain-text WhatsApp message. Returns True on apparent
        success, False on any failure (logged, never raised). Long bodies are
        trimmed to WhatsApp's ~1600-char limit."""
        if not self.configured():
            logger.info("WhatsApp send skipped — Twilio creds not configured.")
            return False
        s = self.settings
        url = f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json"
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    url,
                    auth=(s.twilio_account_sid, s.twilio_auth_token),
                    data={
                        "From": _normalize_to(s.twilio_whatsapp_from),
                        "To": _normalize_to(to_phone),
                        "Body": body[:1600],
                    },
                )
            if resp.status_code >= 400:
                logger.warning("Twilio send failed (%s): %s", resp.status_code, resp.text[:300])
                return False
            return True
        except Exception as e:
            logger.warning("Twilio send error: %s", e)
            return False
