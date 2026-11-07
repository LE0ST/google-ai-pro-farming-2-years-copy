"""Egress resolution and verification.

`None` means "inherit the system route" — that is the VPN case, and the default.
A run is only allowed to continue once the observed country matches what was
asked for; a silent region mismatch invalidates the whole checkout.
"""

from __future__ import annotations

import json
import re

from playwright.async_api import Page

ECHO = "https://ipwho.is/"


def parse(raw: str) -> dict[str, str] | None:
    """host:port | user:pass@host:port | scheme://user:pass@host:port"""
    text = (raw or "").strip()
    if not text:
        return None
    scheme = "http"
    if "://" in text:
        scheme, text = text.split("://", 1)
    auth = None
    if "@" in text:
        auth, text = text.rsplit("@", 1)
    host, _, port = text.partition(":")
    if not host or not port:
        return None
    out = {"server": f"{scheme}://{host}:{port}"}
    if auth:
        user, _, pwd = auth.partition(":")
        out |= {"username": user, "password": pwd}
    return out


async def observe(page: Page) -> dict[str, str]:
    body = await page.evaluate(
        "async (u) => (await fetch(u, {cache:'no-store'})).text()", ECHO
    )
    data = json.loads(body)
    return {
        "ip": data.get("ip", ""),
        "country": (data.get("country_code") or "").upper(),
        "org": ((data.get("connection") or {}).get("org") or "")[:60],
    }


def matches(want: str, seen: str) -> bool:
    return re.fullmatch(want.upper(), seen.upper()) is not None
