"""Pluggable notifier factory — selects a channel by config."""
from app.config import Settings
from app.notifications.base import Notifier
from app.notifications.console import ConsoleNotifier
from app.notifications.whatsapp import WhatsAppNotifier


def get_notifier(settings: Settings) -> Notifier:
    choice = (settings.notifier or "console").lower()
    if choice == "whatsapp":
        return WhatsAppNotifier(to=settings.whatsapp_to)
    return ConsoleNotifier()


__all__ = ["Notifier", "ConsoleNotifier", "WhatsAppNotifier", "get_notifier"]
