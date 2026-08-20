# srag/api/auth.py
from __future__ import annotations
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Header, HTTPException, Request

# ── Session tokens (API-key bootstrap session, retained for compatibility) ──

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


# ── Password hashing (stdlib PBKDF2 — no new dependency) ─────────────

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, _PBKDF2_ITERATIONS,
    )
    return f"pbkdf2${_PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations_s, salt_b64, digest_b64 = stored.split("$", 4)
        if scheme != "pbkdf2":
            return False
        iterations = int(iterations_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return secrets.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


# ── User sessions ─────────────────────────────────────────────────────

def make_user_session_token(user_id: str, role: str, secret: str,
                            ttl_days: int = 30) -> str:
    expiry = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
    payload = f"user:{user_id}:{role}:{expiry}"
    return f"{payload}.{_sign(payload, secret)}"


def resolve_session(token: str, session_secret: str, api_key: str) -> Optional[dict]:
    """Decode a signed session cookie into a claims dict, or None. Accepts the
    user-session format (``user:<id>:<role>:<expiry>``) and the legacy API-key
    session format (``<sha256(key)>:<expiry>``) so existing cookies keep working
    after upgrade."""
    if not token or not session_secret:
        return None
    try:
        payload, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    if not secrets.compare_digest(sig, _sign(payload, session_secret)):
        return None

    now = datetime.now(timezone.utc)

    if payload.startswith("user:"):
        parts = payload.split(":", 3)  # ["user", user_id, role, expiry]
        if len(parts) != 4:
            return None
        _, user_id, role, expiry = parts
        try:
            if datetime.fromisoformat(expiry) <= now:
                return None
        except ValueError:
            return None
        return {"user_id": user_id, "role": role}

    # Legacy API-key session.
    try:
        stored_digest, expiry = payload.split(":", 1)
    except ValueError:
        return None
    if not api_key or not secrets.compare_digest(stored_digest, _key_digest(api_key)):
        return None
    try:
        if datetime.fromisoformat(expiry) <= now:
            return None
    except ValueError:
        return None
    return {"user_id": "bootstrap", "role": "admin"}


# ── Current-user resolution ───────────────────────────────────────────

def get_current_user(request: Request, x_api_key: str = Header(default="")) -> Optional[dict]:
    """Resolve the authenticated user, or None. Returns a dict with id,
    username, and role. The bootstrap API key maps to a built-in admin."""
    from srag.config import load_config
    from srag.store.db import get_user_by_id

    cfg = load_config()
    if x_api_key:
        # A presented-but-wrong header rejects immediately; never fall through
        # to a cookie (preserves the original denied-by-default header rule).
        if cfg.api_key and secrets.compare_digest(x_api_key, cfg.api_key):
            return {"id": "bootstrap", "username": "admin", "role": "admin"}
        return None

    claims = resolve_session(request.cookies.get("srag_session"), cfg.session_secret, cfg.api_key)
    if claims is None:
        return None
    if claims["user_id"] == "bootstrap":
        return {"id": "bootstrap", "username": "admin", "role": "admin"}

    user = get_user_by_id(cfg.db_path, claims["user_id"])
    if user is None or not user.is_active():
        return None
    return {"id": user.id, "username": user.username, "role": user.role}


def require_auth(request: Request, x_api_key: str = Header(default="")):
    user = get_current_user(request, x_api_key)
    if user is None:
        _reject()
    return user


def require_role(*roles: str):
    """Dependency factory: require the authenticated user to hold one of the
    given roles (in addition to being authenticated)."""
    def dependency(request: Request, x_api_key: str = Header(default="")):
        user = get_current_user(request, x_api_key)
        if user is None:
            _reject()
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user
    return dependency


def _reject():
    raise HTTPException(
        status_code=401,
        detail="Invalid API key or session",
        headers={"WWW-Authenticate": "Bearer"},
    )
