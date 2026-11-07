"""Checkout: open the offers dialog, arm a layer, read the total. Never clicks Buy."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from playwright.async_api import Page

from . import guard, tokens

PLANS = "https://one.google.com/plans?hl=en"

_HOOK_PATH = Path(__file__).with_name("hook.js")
HOOK_JS = _HOOK_PATH.read_text(encoding="utf-8")

# Text of the button that opens the offers dialog. English UI; the last match
# on the page is the primary action, matching the ordering Google renders.
BUTTON_HINTS = [
    "Get started",
    "Upgrade",
    "Switch to this plan",
    "Get offer",
    "Start free trial",
    "Try it free",
]

_LAST_ACTION = """(hints) => {
  const els = Array.from(document.querySelectorAll('button, a, [role="button"]'));
  const hits = els.filter((e) => {
    const t = (e.innerText || '').trim();
    const r = e.getBoundingClientRect();
    return r.width > 15 && r.height > 15 && r.left >= 200 && hints.some((h) => t.includes(h));
  });
  if (!hits.length) return 0;
  const last = hits[hits.length - 1];
  last.setAttribute('data-gai-target', '1');
  return hits.length;
}"""


async def init_script() -> str:
    """Injected before any page script, on every navigation and frame."""
    return "window.__INIT_TEST = null;\n" + HOOK_JS


async def open(page: Page) -> None:
    await page.goto(PLANS, wait_until="domcontentloaded")
    await asyncio.sleep(3.0)


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


async def click_action(page: Page) -> int:
    found = await page.evaluate(_LAST_ACTION, BUTTON_HINTS)
    if not found:
        return 0
    await page.evaluate("() => document.querySelector('[data-gai-target=\"1\"]')?.click()")
    return found


async def read_modal(page: Page, timeout_s: float = 45.0) -> tuple[str, str]:
    """Read the checkout dialog: the eacquire frame first, then any other child
    frame, then the main frame. The dialog lives in the eacquire iframe and is
    full-viewport, so picking an arbitrary frame lands on the app launcher."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    best: tuple[str, str] = ("", "")
    while asyncio.get_running_loop().time() < deadline:
        targets = [f for f in page.frames if "eacquire" in f.url]
        targets += [f for f in page.frames if f not in targets and f != page.main_frame]
        targets.append(page.main_frame)
        for frame in targets:
            try:
                text = await frame.inner_text("body", timeout=1500)
            except Exception:
                continue
            flat = " ".join(text.split())
            if not flat:
                continue
            best = (flat, frame.url)
            if guard.MONEY.search(flat):
                return best
        await asyncio.sleep(1.0)
    return best


def verdict_of(text: str) -> guard.Verdict:
    return guard.judge(text)
