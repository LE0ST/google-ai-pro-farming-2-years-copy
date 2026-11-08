"""Checkout helpers: install the hook, arm a layer, capture the subscription id.

Never clicks Buy — the flow in tasks/subscribe_flow.py owns the dialog."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from playwright.async_api import Page

from . import tokens

PLANS = "https://one.google.com/plans?hl=en"

_HOOK_PATH = Path(__file__).with_name("hook.js")
HOOK_JS = _HOOK_PATH.read_text(encoding="utf-8")


async def init_script() -> str:
    """Injected before any page script, on every navigation and frame."""
    return "window.__INIT_TEST = null;\n" + HOOK_JS


async def arm(page: Page, layer: int, sub_id: str | None = None) -> dict:
    """Install the hook if needed and push the layer config into it.

    The hook only binds on one.google.com, so while Google is still redirecting the
    install silently does nothing and `__setDirectTest` stays undefined."""
    cfg = tokens.config(layer, sub_id)

    ready = "() => !!window.__HOOK_INSTALLED && typeof window.__setDirectTest === 'function'"
    for _ in range(20):
        if await page.evaluate(ready):
            break
        await page.evaluate(HOOK_JS)
        await asyncio.sleep(0.6)

    for _ in range(3):
        try:
            await page.evaluate("(c) => window.__setDirectTest(c)", cfg)
        except Exception:
            await page.evaluate(HOOK_JS)
            await asyncio.sleep(0.6)
            await page.evaluate("(c) => window.__setDirectTest(c)", cfg)
        await asyncio.sleep(0.3)
    return cfg


async def capture_sub_id(page: Page) -> str | None:
    """Subscription id of an already-active plan, needed to stack rather than
    replace. The interceptor's capture is checked first, then the id is scraped
    out of the served HTML the way the original does (24 lowercase chars next to
    an AO- marker, or inside an init-data block mentioning the subscription
    namespace). Returning None is safe: the hook then keeps the page's own id."""
    seen = await page.evaluate("() => window.__capturedSubId || null")
    if seen:
        return seen
    try:
        html = await page.content()
    except Exception:
        return None
    for pattern in (r'"([a-z]{24})\.AO-', r"([a-z]{24})\.AO-J1O"):
        hit = re.search(pattern, html)
        if hit:
            return hit.group(1)
    for block in re.findall(r"AF_initDataCallback\(\{.*?\}\);", html, re.S):
        if "subscriptions.red" in block or "g1." in block:
            cand = re.findall(r'"([a-z]{24})"', block)
            if cand:
                return cand[0]
    return None
