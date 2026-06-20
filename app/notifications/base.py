"""Notifier interface. The digest is a plain dict so any channel can render it."""
from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def send(self, digest: dict) -> None:
        """Deliver the daily digest. Implementations must not raise on
        transient delivery errors that shouldn't fail the daily job — log
        instead."""
        raise NotImplementedError
