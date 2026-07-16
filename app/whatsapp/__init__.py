"""WhatsApp assistant (Phase 1): text-native access to AlphaFunds.

Users link their phone to their account with a one-time code, then ask
natural-language questions and get grounded answers back — routed through the
same app.chat.answer_question brain the website chat uses.
"""
from app.whatsapp.webhook import router

__all__ = ["router"]
