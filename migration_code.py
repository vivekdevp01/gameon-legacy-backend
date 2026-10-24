"""Signed migration codes (Python twin of GameOn's utils/legacyMigrationCode.util.ts).

Format:  GOL1.<base64url(JSON payload)>.<base64url(HMAC-SHA256 over "GOL1.<body>")>

Keep phone normalisation and phone_hash IDENTICAL to the TypeScript side, or the
phone-match check in GameOn will never succeed.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from typing import Optional

CODE_PREFIX = "GOL1"
DEFAULT_TTL_SECONDS = 15 * 60


def _secret() -> Optional[bytes]:
    s = os.environ.get("MIGRATION_SIGNING_SECRET", "")
    if len(s) < 24:
        return None
    return s.encode("utf-8")


def signing_configured() -> bool:
    return _secret() is not None


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def normalize_phone(raw) -> Optional[str]:
    d = re.sub(r"\D", "", str(raw if raw is not None else ""))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if not re.fullmatch(r"[6-9]\d{9}", d):
        return None
    if re.fullmatch(r"(\d)\1{9}", d):
        return None
    return d


def phone_hash(normalized_phone: str) -> str:
    return hmac.new(_secret(), f"phone:{normalized_phone}".encode(), hashlib.sha256).hexdigest()


def mask_phone(raw) -> str:
    """98XXXXX210 style. Junk / short numbers show a dash."""
    n = normalize_phone(raw)
    if not n:
        return "—"
    return n[:2] + "X" * 5 + n[-3:]


def sign_code(payload: dict) -> str:
    secret = _secret()
    if secret is None:
        raise RuntimeError("MIGRATION_SIGNING_SECRET is not configured")
    body = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = _b64e(hmac.new(secret, f"{CODE_PREFIX}.{body}".encode(), hashlib.sha256).digest())
    return f"{CODE_PREFIX}.{body}.{sig}"


def new_jti() -> str:
    return uuid.uuid4().hex


def build_payload(*, source: str, record_key: str, old_rfid: str, name: str, phone,
                  main: int, bonus: int, tickets: int, last_activity: Optional[str],
                  flags: dict, input_source: str, issued_by: str,
                  ttl_seconds: int = DEFAULT_TTL_SECONDS, now: Optional[int] = None) -> dict:
    now = int(now if now is not None else time.time())
    norm = normalize_phone(phone)
    flags = dict(flags)
    flags["junkPhone"] = norm is None
    return {
        "v": 1,
        "source": source,
        "recordKey": record_key[:100],
        "oldRfid": old_rfid,
        "name": (name or "").strip()[:100],
        "phoneHash": phone_hash(norm) if norm else None,
        "phoneLast3": norm[-3:] if norm else None,
        "main": max(0, int(main or 0)),
        "bonus": max(0, int(bonus or 0)),
        "tickets": max(0, int(tickets or 0)),
        "lastActivity": last_activity,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": new_jti(),
        "flags": flags,
        "inputSource": input_source,
        "issuedBy": issued_by,
    }