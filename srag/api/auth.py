# srag/api/auth.py
from __future__ import annotations
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException, Request


def make_session_token(api_key: str, secret: str, ttl_days: int = 30) -> str:
    expiry = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
    # Sign the *digest* of the key, not the key itself, so a stolen session
    # cookie never leaks the permanent API credential — it only yields a
    # time-limited session.
    payload = f"{_key_digest(api_key)}:{expiry}"
    return f"{payload}.{_sign(payload, secret)}"


def verify_session_token(token: str, api_key: str, secret: str) -> bool:
    try:
        payload, sig = token.rsplit(".", 1)
    except ValueError:
        return False
    if not secrets.compare_digest(sig, _sign(payload, secret)):
        return False
    try:
        stored_digest, expiry = payload.split(":", 1)
    except ValueError:
        return False
    if not secrets.compare_digest(stored_digest, _key_digest(api_key)):
        return False
    try:
        exp = datetime.fromisoformat(expiry)
    except ValueError:
        return False
    return exp > datetime.now(timezone.utc)


def _key_digest(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def require_auth(request: Request, x_api_key: str = Header(default="")) -> None:
    from srag.config import load_config

    cfg = load_config()
    expected = cfg.api_key or ""
    if x_api_key:
        if expected and secrets.compare_digest(x_api_key, expected):
            return
        _reject()
    token = request.cookies.get("srag_session")
    if token and expected and verify_session_token(token, expected, cfg.session_secret):
        return
    _reject()


def _reject():
    raise HTTPException(
        status_code=401,
        detail="Invalid API key or session",
        headers={"WWW-Authenticate": "Bearer"},
    )
