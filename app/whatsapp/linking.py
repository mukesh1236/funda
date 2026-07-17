"""Phone <-> account linking. The website (authenticated) mints a 6-digit
code; the user sends it from WhatsApp; the inbound webhook verifies + binds.
Because only the logged-in owner ever sees the code, the phone is proven, not
self-claimed."""
import re
from typing import Optional

from app.store import RecommendationStore

_CODE_RE = re.compile(r"^\s*(\d{6})\s*$")


def generate_link_code(store: RecommendationStore, user_id: int) -> str:
    """Mint a fresh 6-digit code for a user (10-min TTL, single-use)."""
    return store.create_whatsapp_link_code(user_id)


def extract_code(text: str) -> Optional[str]:
    """Return the 6-digit code if the whole message is just a code, else None.
    Kept strict so a normal question containing digits isn't misread as a code."""
    m = _CODE_RE.match(text or "")
    return m.group(1) if m else None


def try_link(store: RecommendationStore, text: str, phone_e164: str) -> Optional[int]:
    """If `text` is a valid, unused, unexpired code, bind the phone and return
    the user_id. Otherwise None."""
    code = extract_code(text)
    if not code:
        return None
    user_id = store.consume_whatsapp_link_code(code)
    if user_id is None:
        return None
    store.bind_whatsapp(user_id, phone_e164)
    return user_id
