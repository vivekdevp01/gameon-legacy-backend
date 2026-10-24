# """Staff authentication for the legacy lookup app.

# Two roles only:
#   owner   - everything (browse, import, edit, delete, purge, users, audit).
#   cashier - ONLY the "tap old card -> signed migration code" screen.

# Design notes
#   * No new dependencies: passwords use hashlib.scrypt, sessions are HMAC-signed
#     tokens (same idea as a JWT, but we control the format).
#   * Secrets come from the environment only (JWT_SECRET). Nothing secret lives
#     in the repo. The app refuses to start without JWT_SECRET.
#   * Every request re-loads the user, so deactivating a user or changing their
#     password kills their sessions immediately (token_version).
#   * Passwords, tokens and migration codes are NEVER logged.
# """
# import base64
# import hashlib
# import hmac
# import json
# import logging
# import os
# import secrets
# import time
# import uuid
# from collections import defaultdict, deque
# from datetime import datetime, timezone, timedelta
# from typing import Optional

# from fastapi import Depends, HTTPException, Request

# from database import app_users, audit_log

# logger = logging.getLogger(__name__)

# ROLES = ("owner", "cashier")
# TOKEN_TTL_SECONDS = int(os.environ.get("SESSION_HOURS", "10")) * 3600
# MAX_FAILED_LOGINS = int(os.environ.get("MAX_FAILED_LOGINS", "5"))
# LOCK_MINUTES = int(os.environ.get("LOCK_MINUTES", "15"))
# MIN_PASSWORD_LEN = 8
# TRUST_PROXY = os.environ.get("TRUST_PROXY", "1") == "1"


# def _jwt_secret() -> bytes:
#     secret = os.environ.get("JWT_SECRET", "")
#     if len(secret) < 24:
#         raise RuntimeError(
#             "JWT_SECRET is missing or too short (need 24+ characters). Set it in the "
#             "environment (never in the repo). Generate one with: "
#             "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
#         )
#     return secret.encode("utf-8")


# def assert_auth_configured() -> None:
#     """Called at startup so a missing secret fails loudly, not silently open."""
#     _jwt_secret()


# # ---------------------------------------------------------------- passwords
# def hash_password(password: str) -> str:
#     salt = secrets.token_bytes(16)
#     dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
#     return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


# def verify_password(password: str, stored: str) -> bool:
#     try:
#         scheme, salt_b64, hash_b64 = stored.split("$")
#         if scheme != "scrypt":
#             return False
#         salt = base64.b64decode(salt_b64)
#         expected = base64.b64decode(hash_b64)
#         dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=len(expected))
#         return hmac.compare_digest(dk, expected)
#     except Exception:
#         return False


# # A dummy hash so "unknown user" costs the same time as "wrong password".
# _DUMMY_HASH = hash_password("dummy-password-for-timing")


# def validate_new_password(password: str) -> None:
#     if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
#         raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LEN} characters")
#     if len(password) > 200:
#         raise HTTPException(status_code=400, detail="Password is too long")


# # ---------------------------------------------------------------- tokens
# def _b64e(b: bytes) -> str:
#     return base64.urlsafe_b64encode(b).decode().rstrip("=")


# def _b64d(s: str) -> bytes:
#     return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# def make_token(user: dict) -> str:
#     payload = {
#         "sub": user["id"],
#         "role": user["role"],
#         "tv": user.get("token_version", 0),
#         "exp": int(time.time()) + TOKEN_TTL_SECONDS,
#     }
#     body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
#     sig = _b64e(hmac.new(_jwt_secret(), body.encode(), hashlib.sha256).digest())
#     return f"{body}.{sig}"


# def parse_token(token: str) -> Optional[dict]:
#     try:
#         body, sig = token.split(".")
#         expected = _b64e(hmac.new(_jwt_secret(), body.encode(), hashlib.sha256).digest())
#         if not hmac.compare_digest(sig, expected):
#             return None
#         payload = json.loads(_b64d(body))
#         if int(payload.get("exp", 0)) < int(time.time()):
#             return None
#         return payload
#     except Exception:
#         return None


# # ---------------------------------------------------------------- rate limits
# class RateLimiter:
#     """Small in-memory sliding window. Fine for a single instance; if you run
#     several instances, each enforces its own window (still useful)."""

#     def __init__(self):
#         self._hits = defaultdict(deque)

#     def hit(self, key: str, limit: int, window_seconds: int) -> bool:
#         """Record a hit. Returns False when the limit is exceeded."""
#         now = time.monotonic()
#         q = self._hits[key]
#         while q and now - q[0] > window_seconds:
#             q.popleft()
#         if len(q) >= limit:
#             return False
#         q.append(now)
#         return True

#     def count(self, key: str, window_seconds: int) -> int:
#         now = time.monotonic()
#         q = self._hits[key]
#         while q and now - q[0] > window_seconds:
#             q.popleft()
#         return len(q)

#     def reset(self):
#         self._hits.clear()


# limiter = RateLimiter()


# def client_ip(request: Request) -> str:
#     if TRUST_PROXY:
#         xff = request.headers.get("x-forwarded-for")
#         if xff:
#             return xff.split(",")[0].strip()[:64]
#     return (request.client.host if request.client else "unknown")[:64]


# def enforce_rate(key: str, limit: int, window_seconds: int, message: str = "Too many requests. Please wait a moment.") -> None:
#     if not limiter.hit(key, limit, window_seconds):
#         raise HTTPException(status_code=429, detail=message)


# # ---------------------------------------------------------------- audit
# async def audit(event: str, request: Optional[Request] = None, user: Optional[dict] = None, **detail) -> None:
#     """Never raises - an audit failure must not block the action."""
#     try:
#         await audit_log.insert_one({
#             "id": str(uuid.uuid4()),
#             "ts": datetime.now(timezone.utc).isoformat(),
#             "event": event,
#             "user": (user or {}).get("username"),
#             "role": (user or {}).get("role"),
#             "ip": client_ip(request) if request is not None else None,
#             "detail": detail or None,
#         })
#     except Exception as e:  # pragma: no cover
#         logger.warning("audit write failed: %s", type(e).__name__)


# # ---------------------------------------------------------------- users
# def public_user(u: dict) -> dict:
#     return {
#         "id": u["id"], "username": u["username"], "name": u.get("name"),
#         "role": u["role"], "active": u.get("active", True),
#         "created_at": u.get("created_at"), "last_login_at": u.get("last_login_at"),
#     }


# def _norm_username(username: str) -> str:
#     return (username or "").strip().lower()


# async def create_user(username: str, password: str, role: str, name: Optional[str] = None) -> dict:
#     username = _norm_username(username)
#     if not username or len(username) > 60 or not all(c.isalnum() or c in "._-@+" for c in username):
#         raise HTTPException(status_code=400, detail="Username may use letters, numbers and . _ - @ + only")
#     if role not in ROLES:
#         raise HTTPException(status_code=400, detail="Role must be owner or cashier")
#     validate_new_password(password)
#     if await app_users.find_one({"username": username}):
#         raise HTTPException(status_code=409, detail="That username already exists")
#     doc = {
#         "id": str(uuid.uuid4()), "username": username, "name": (name or "").strip()[:100] or None,
#         "role": role, "password_hash": hash_password(password), "active": True,
#         "token_version": 0, "failed_attempts": 0, "locked_until": None,
#         "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
#     }
#     await app_users.insert_one(doc)
#     return doc


# async def bootstrap_owner() -> None:
#     """Create the first owner from env ONCE. It never overwrites an existing
#     user, so changing the env later does not silently reset a password (use
#     manage_users.py for that)."""
#     username = _norm_username(os.environ.get("OWNER_USERNAME", ""))
#     password = os.environ.get("OWNER_PASSWORD", "")
#     if not username or not password:
#         if await app_users.count_documents({"role": "owner"}) == 0:
#             logger.warning("No owner exists and OWNER_USERNAME/OWNER_PASSWORD are not set - nobody can log in. "
#                            "Set them, or run: python manage_users.py create-owner")
#         return
#     if await app_users.find_one({"username": username}):
#         return
#     if await app_users.count_documents({"role": "owner"}) > 0:
#         return  # an owner already exists; do not add surprise owners from env
#     await create_user(username, password, "owner", "Owner")
#     logger.info("Bootstrap owner '%s' created", username)


# async def login(request: Request, username: str, password: str) -> dict:
#     ip = client_ip(request)
#     enforce_rate(f"login-ip:{ip}", 20, 60, "Too many login attempts from this device. Wait a minute.")
#     username = _norm_username(username)
#     user = await app_users.find_one({"username": username}) if username else None

#     now = datetime.now(timezone.utc)
#     if user and user.get("locked_until"):
#         try:
#             until = datetime.fromisoformat(user["locked_until"])
#         except Exception:
#             until = None
#         if until and until > now:
#             await audit("login_blocked_locked", request, user)
#             raise HTTPException(status_code=423, detail="Account is temporarily locked after too many wrong passwords. Try again later or ask the owner.")

#     ok = verify_password(password or "", user["password_hash"] if user else _DUMMY_HASH)
#     if not user or not ok or not user.get("active", True):
#         if user:
#             attempts = int(user.get("failed_attempts", 0)) + 1
#             upd = {"failed_attempts": attempts}
#             if attempts >= MAX_FAILED_LOGINS:
#                 upd["locked_until"] = (now + timedelta(minutes=LOCK_MINUTES)).isoformat()
#                 upd["failed_attempts"] = 0
#             await app_users.update_one({"id": user["id"]}, {"$set": upd})
#         await audit("login_failed", request, user, attempted_username=username[:60])
#         raise HTTPException(status_code=401, detail="Invalid username or password")

#     await app_users.update_one(
#         {"id": user["id"]},
#         {"$set": {"failed_attempts": 0, "locked_until": None, "last_login_at": now.isoformat()}},
#     )
#     await audit("login_ok", request, user)
#     return {"token": make_token(user), "user": public_user(user), "expires_in": TOKEN_TTL_SECONDS}


# # ---------------------------------------------------------------- dependencies
# async def current_user(request: Request) -> dict:
#     header = request.headers.get("authorization", "")
#     if not header.lower().startswith("bearer "):
#         raise HTTPException(status_code=401, detail="Login required", headers={"WWW-Authenticate": "Bearer"})
#     payload = parse_token(header[7:].strip())
#     if not payload:
#         raise HTTPException(status_code=401, detail="Session expired - please log in again", headers={"WWW-Authenticate": "Bearer"})
#     user = await app_users.find_one({"id": payload.get("sub")})
#     if (
#         not user
#         or not user.get("active", True)
#         or user.get("token_version", 0) != payload.get("tv")
#         or user.get("role") != payload.get("role")
#     ):
#         raise HTTPException(status_code=401, detail="Session expired - please log in again", headers={"WWW-Authenticate": "Bearer"})
#     request.state.user = user
#     return user


# async def require_staff(user: dict = Depends(current_user)) -> dict:
#     """Any logged-in staff (owner or cashier)."""
#     return user


# async def require_owner(user: dict = Depends(current_user)) -> dict:
#     if user.get("role") != "owner":
#         raise HTTPException(status_code=403, detail="This action needs the owner login")
#     return user

"""Staff authentication for the legacy lookup app.

Two roles only:
  owner   - everything (browse, import, edit, delete, purge, users, audit).
  cashier - ONLY the "tap old card -> signed migration code" screen.

Design notes
  * No new dependencies: passwords use hashlib.scrypt, sessions are HMAC-signed
    tokens (same idea as a JWT, but we control the format).
  * Secrets come from the environment only (JWT_SECRET). Nothing secret lives
    in the repo. The app refuses to start without JWT_SECRET.
  * Every request re-loads the user, so deactivating a user or changing their
    password kills their sessions immediately (token_version).
  * Passwords, tokens and migration codes are NEVER logged.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request

from database import app_users, audit_log

logger = logging.getLogger(__name__)

ROLES = ("owner", "manager", "cashier")
TOKEN_TTL_SECONDS = int(os.environ.get("SESSION_HOURS", "10")) * 3600
MAX_FAILED_LOGINS = int(os.environ.get("MAX_FAILED_LOGINS", "5"))
LOCK_MINUTES = int(os.environ.get("LOCK_MINUTES", "15"))
MIN_PASSWORD_LEN = 8
TRUST_PROXY = os.environ.get("TRUST_PROXY", "1") == "1"


def _jwt_secret() -> bytes:
    secret = os.environ.get("JWT_SECRET", "")
    if len(secret) < 24:
        raise RuntimeError(
            "JWT_SECRET is missing or too short (need 24+ characters). Set it in the "
            "environment (never in the repo). Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return secret.encode("utf-8")


def assert_auth_configured() -> None:
    """Called at startup so a missing secret fails loudly, not silently open."""
    _jwt_secret()


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# A dummy hash so "unknown user" costs the same time as "wrong password".
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def validate_new_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password) > 200:
        raise HTTPException(status_code=400, detail="Password is too long")


# ---------------------------------------------------------------- tokens
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user: dict) -> str:
    payload = {
        "sub": user["id"],
        "role": user["role"],
        "tv": user.get("token_version", 0),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_jwt_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_token(token: str) -> Optional[dict]:
    try:
        body, sig = token.split(".")
        expected = _b64e(hmac.new(_jwt_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------- rate limits
class RateLimiter:
    """Small in-memory sliding window. Fine for a single instance; if you run
    several instances, each enforces its own window (still useful)."""

    def __init__(self):
        self._hits = defaultdict(deque)

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record a hit. Returns False when the limit is exceeded."""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def count(self, key: str, window_seconds: int) -> int:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        return len(q)

    def reset(self):
        self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def enforce_rate(key: str, limit: int, window_seconds: int, message: str = "Too many requests. Please wait a moment.") -> None:
    if not limiter.hit(key, limit, window_seconds):
        raise HTTPException(status_code=429, detail=message)


# ---------------------------------------------------------------- audit
async def audit(event: str, request: Optional[Request] = None, user: Optional[dict] = None, **detail) -> None:
    """Never raises - an audit failure must not block the action."""
    try:
        await audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "user": (user or {}).get("username"),
            "role": (user or {}).get("role"),
            "ip": client_ip(request) if request is not None else None,
            "detail": detail or None,
        })
    except Exception as e:  # pragma: no cover
        logger.warning("audit write failed: %s", type(e).__name__)


# ---------------------------------------------------------------- users
def public_user(u: dict) -> dict:
    return {
        "id": u["id"], "username": u["username"], "name": u.get("name"),
        "role": u["role"], "active": u.get("active", True),
        "created_at": u.get("created_at"), "last_login_at": u.get("last_login_at"),
    }


def _norm_username(username: str) -> str:
    return (username or "").strip().lower()


async def create_user(username: str, password: str, role: str, name: Optional[str] = None) -> dict:
    username = _norm_username(username)
    if not username or len(username) > 60 or not all(c.isalnum() or c in "._-@+" for c in username):
        raise HTTPException(status_code=400, detail="Username may use letters, numbers and . _ - @ + only")
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="Role must be owner, manager or cashier")
    validate_new_password(password)
    if await app_users.find_one({"username": username}):
        raise HTTPException(status_code=409, detail="That username already exists")
    doc = {
        "id": str(uuid.uuid4()), "username": username, "name": (name or "").strip()[:100] or None,
        "role": role, "password_hash": hash_password(password), "active": True,
        "token_version": 0, "failed_attempts": 0, "locked_until": None,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    }
    await app_users.insert_one(doc)
    return doc


async def bootstrap_owner() -> None:
    """Create the first owner from env ONCE. It never overwrites an existing
    user, so changing the env later does not silently reset a password (use
    manage_users.py for that)."""
    username = _norm_username(os.environ.get("OWNER_USERNAME", ""))
    password = os.environ.get("OWNER_PASSWORD", "")
    if not username or not password:
        if await app_users.count_documents({"role": "owner"}) == 0:
            logger.warning("No owner exists and OWNER_USERNAME/OWNER_PASSWORD are not set - nobody can log in. "
                           "Set them, or run: python manage_users.py create-owner")
        return
    if await app_users.find_one({"username": username}):
        return
    if await app_users.count_documents({"role": "owner"}) > 0:
        return  # an owner already exists; do not add surprise owners from env
    await create_user(username, password, "owner", "Owner")
    logger.info("Bootstrap owner '%s' created", username)


async def login(request: Request, username: str, password: str) -> dict:
    ip = client_ip(request)
    enforce_rate(f"login-ip:{ip}", 20, 60, "Too many login attempts from this device. Wait a minute.")
    username = _norm_username(username)
    user = await app_users.find_one({"username": username}) if username else None

    now = datetime.now(timezone.utc)
    if user and user.get("locked_until"):
        try:
            until = datetime.fromisoformat(user["locked_until"])
        except Exception:
            until = None
        if until and until > now:
            await audit("login_blocked_locked", request, user)
            raise HTTPException(status_code=423, detail="Account is temporarily locked after too many wrong passwords. Try again later or ask the owner.")

    ok = verify_password(password or "", user["password_hash"] if user else _DUMMY_HASH)
    if not user or not ok or not user.get("active", True):
        if user:
            attempts = int(user.get("failed_attempts", 0)) + 1
            upd = {"failed_attempts": attempts}
            if attempts >= MAX_FAILED_LOGINS:
                upd["locked_until"] = (now + timedelta(minutes=LOCK_MINUTES)).isoformat()
                upd["failed_attempts"] = 0
            await app_users.update_one({"id": user["id"]}, {"$set": upd})
        await audit("login_failed", request, user, attempted_username=username[:60])
        raise HTTPException(status_code=401, detail="Invalid username or password")

    await app_users.update_one(
        {"id": user["id"]},
        {"$set": {"failed_attempts": 0, "locked_until": None, "last_login_at": now.isoformat()}},
    )
    await audit("login_ok", request, user)
    return {"token": make_token(user), "user": public_user(user), "expires_in": TOKEN_TTL_SECONDS}


# ---------------------------------------------------------------- dependencies
async def current_user(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Login required", headers={"WWW-Authenticate": "Bearer"})
    payload = parse_token(header[7:].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="Session expired - please log in again", headers={"WWW-Authenticate": "Bearer"})
    user = await app_users.find_one({"id": payload.get("sub")})
    if (
        not user
        or not user.get("active", True)
        or user.get("token_version", 0) != payload.get("tv")
        or user.get("role") != payload.get("role")
    ):
        raise HTTPException(status_code=401, detail="Session expired - please log in again", headers={"WWW-Authenticate": "Bearer"})
    request.state.user = user
    return user


async def require_staff(user: dict = Depends(current_user)) -> dict:
    """Any logged-in staff (owner or cashier)."""
    return user


async def require_owner(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="This action needs the owner login")
    return user


async def require_manager(user: dict = Depends(current_user)) -> dict:
    """Store manager (or owner). Used for the no-card "find by phone" flow."""
    if user.get("role") not in ("owner", "manager"):
        raise HTTPException(status_code=403, detail="This action needs a Store Manager login")
    return user