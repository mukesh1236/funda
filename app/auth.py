"""Authentication helpers: password hashing, signed session cookies, and the
FastAPI dependency that turns a request's cookie into the logged-in user.

Sessions are stateless: the cookie holds the user id signed with SESSION_SECRET
(itsdangerous). No server-side session table — verifying the signature + expiry
is enough. The cookie is httpOnly + SameSite=Lax so the vanilla-JS frontend
never touches the token (the browser sends it automatically, same-origin).
"""
import logging
import secrets
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

COOKIE_NAME = "session"
_SALT = "fundai-session-v1"


# ── passwords ────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """bcrypt hash of a plaintext password, as a utf-8 string for storage."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """True if the plaintext matches the stored hash. Never raises."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── session tokens ───────────────────────────────────────────────────────────
def _serializer(settings: Optional[Settings] = None) -> URLSafeTimedSerializer:
    settings = settings or get_settings()
    secret = settings.session_secret or _ephemeral_secret()
    return URLSafeTimedSerializer(secret, salt=_SALT)


_EPHEMERAL: Optional[str] = None


def _ephemeral_secret() -> str:
    """Random per-process key used when SESSION_SECRET is unset. Logins survive
    until the server restarts — fine for local dev, not for production."""
    global _EPHEMERAL
    if _EPHEMERAL is None:
        _EPHEMERAL = secrets.token_hex(32)
        logger.warning(
            "SESSION_SECRET is not set — using a random per-process key. "
            "Logins will be invalidated on restart. Set SESSION_SECRET in .env "
            "for production."
        )
    return _EPHEMERAL


def make_token(user_id: int, settings: Optional[Settings] = None) -> str:
    return _serializer(settings).dumps(user_id)


def read_token(token: str, settings: Optional[Settings] = None) -> Optional[int]:
    """Return the user id from a valid, unexpired token, else None."""
    settings = settings or get_settings()
    max_age = settings.session_max_age_days * 24 * 3600
    try:
        return int(_serializer(settings).loads(token, max_age=max_age))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None


# ── cookies ──────────────────────────────────────────────────────────────────
def set_session_cookie(response: Response, user_id: int,
                       settings: Optional[Settings] = None) -> None:
    settings = settings or get_settings()
    response.set_cookie(
        COOKIE_NAME,
        make_token(user_id, settings),
        max_age=settings.session_max_age_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ── FastAPI dependency ───────────────────────────────────────────────────────
def get_current_user(request: Request) -> dict:
    """Resolve the logged-in user from the session cookie, or raise 401.

    Imports `store` lazily to avoid a circular import with app.main.
    """
    token = request.cookies.get(COOKIE_NAME)
    uid = read_token(token) if token else None
    if uid is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    from app.main import store
    user = store.get_user_by_id(uid)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
