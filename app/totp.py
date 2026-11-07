"""RFC 6238 TOTP using the stdlib only — no pyotp dependency."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import struct
import time

STEP = 30
DIGITS = 6


def normalize(secret: str) -> str:
    raw = re.sub(r"[^A-Za-z2-7]", "", secret or "").upper()
    return raw + "=" * (-len(raw) % 8)


def code(secret: str, at: int | None = None) -> str:
    """Six-digit code for `secret`. Empty string when the secret is unusable."""
    norm = normalize(secret)
    if not norm:
        return ""
    try:
        key = base64.b32decode(norm, casefold=True)
    except Exception:
        return ""
    counter = int(time.time() if at is None else at) // STEP
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**DIGITS)
    return f"{value:0{DIGITS}d}"


def seconds_left(at: int | None = None) -> int:
    return STEP - int(time.time() if at is None else at) % STEP


def wait_hint(threshold: int = 3, at: int | None = None) -> int:
    """Seconds to sleep so a code is not submitted as it expires (0 = go now)."""
    remaining = seconds_left(at)
    return remaining + 1 if remaining <= threshold else 0
