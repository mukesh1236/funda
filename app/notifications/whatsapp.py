"""WhatsApp notifier — stub. Wire up a provider when ready.

Two common options:
  1. Twilio WhatsApp sandbox (fastest for personal use):
     https://www.twilio.com/docs/whatsapp/sandbox
     pip install twilio; set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN /
     TWILIO_WHATSAPP_FROM and settings.whatsapp_to.
  2. Meta WhatsApp Cloud API (official, needs Business account + template
     approval): https://developers.facebook.com/docs/whatsapp/cloud-api

To enable: implement send() below and set NOTIFIER=whatsapp in .env.
"""
import logging

from app.notifications.base import Notifier

logger = logging.getLogger(__name__)


class WhatsAppNotifier(Notifier):
    def __init__(self, to: str = ""):
        self.to = to

    def send(self, digest: dict) -> None:
        raise NotImplementedError(
            "WhatsApp delivery is not wired up yet. Implement "
            "app/notifications/whatsapp.py (Twilio sandbox or Meta Cloud API) "
            "and set NOTIFIER=whatsapp. See the module docstring for steps."
        )

    @staticmethod
    def format_message(digest: dict) -> str:
        """Plain-text body ready for whichever provider gets wired up."""
        head = (
            f"📈 Analyst Digest {digest.get('date', '')}\n"
            f"New recs: {digest.get('new_recommendations', 0)} | "
            f"Targets hit: {digest.get('targets_hit', 0)}\n\n"
        )
        rows = [
            f"{s['symbol']}: {s['consensus_score']:+d} "
            f"(B{s['buy_count']}/H{s['hold_count']}/S{s['sell_count']})"
            for s in digest.get("top_stocks", [])
        ]
        return head + "\n".join(rows)
