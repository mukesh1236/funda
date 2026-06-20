"""Auth: password hashing, session tokens, and the API login/cookie flow."""
import time

import pytest
from fastapi.testclient import TestClient

from app import auth


# ── password hashing ───────────────────────────────────────────────────────────

def test_hash_verify_roundtrip():
    h = auth.hash_password("hunter2pw")
    assert h != "hunter2pw"            # not stored in plaintext
    assert auth.verify_password("hunter2pw", h)
    assert not auth.verify_password("wrong", h)


def test_verify_password_bad_hash_returns_false():
    assert auth.verify_password("anything", "not-a-real-bcrypt-hash") is False


# ── session tokens ─────────────────────────────────────────────────────────────

def test_token_make_read_roundtrip():
    tok = auth.make_token(42)
    assert auth.read_token(tok) == 42


def test_token_tampering_rejected():
    tok = auth.make_token(42)
    assert auth.read_token(tok + "x") is None
    assert auth.read_token("garbage") is None


def test_token_expiry(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "session_max_age_days", 0)  # everything is expired
    tok = auth.make_token(7, s)
    time.sleep(1.1)
    assert auth.read_token(tok, s) is None


# ── API cookie flow ────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient backed by a throwaway DB, with a fixed session secret."""
    monkeypatch.setenv("SESSION_SECRET", "test-secret-key-fixed")
    monkeypatch.setenv("RECOMMENDATIONS_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    # Reload config + main so the new env vars take effect on a fresh store.
    import importlib
    import app.config as config
    config.get_settings.cache_clear()
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_register_me_watchlist_flow(client, monkeypatch):
    import app.main as main
    from app.models import WatchlistResult
    # Avoid network: the watchlist build (price history) is stubbed out.
    monkeypatch.setattr(main, "build_watchlist",
                        lambda store, uid, **kw: WatchlistResult(items=[]))

    # Unauthenticated watchlist access is blocked.
    assert client.get("/api/watchlist").status_code == 401

    r = client.post("/api/auth/register",
                    json={"email": "z@example.com", "password": "password123",
                          "display_name": "Zed"})
    assert r.status_code == 200
    assert r.json()["email"] == "z@example.com"
    assert "session" in r.cookies

    # The cookie now authenticates /me and the watchlist.
    assert client.get("/api/auth/me").json()["display_name"] == "Zed"
    assert client.get("/api/watchlist").status_code == 200


def test_login_wrong_password(client):
    client.post("/api/auth/register",
                json={"email": "y@example.com", "password": "password123"})
    client.post("/api/auth/logout")
    bad = client.post("/api/auth/login",
                      json={"email": "y@example.com", "password": "nope-wrong"})
    assert bad.status_code == 401


def test_duplicate_register_conflicts(client):
    client.post("/api/auth/register",
                json={"email": "dupe@example.com", "password": "password123"})
    again = client.post("/api/auth/register",
                        json={"email": "dupe@example.com", "password": "password123"})
    assert again.status_code == 409


def test_short_password_rejected(client):
    r = client.post("/api/auth/register",
                    json={"email": "short@example.com", "password": "abc"})
    assert r.status_code == 422


def test_watchlist_add_unknown_ticker_404(client, monkeypatch):
    # A symbol with no name, no price, and no analyst coverage is rejected.
    import app.sources.prices as prices
    import app.sources.profiles as profiles
    monkeypatch.setattr(prices, "get_current_price", lambda sym: None)
    monkeypatch.setattr(profiles, "fetch_profile", lambda sym: None)
    client.post("/api/auth/register",
                json={"email": "w@example.com", "password": "password123"})
    r = client.post("/api/watchlist", json={"symbol": "ZZZZQQ"})
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()
