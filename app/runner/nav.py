"""Navigation with retries. A VPN or proxy drops sockets mid-flow; a single
goto turns that into a dead run."""

from __future__ import annotations

import asyncio

from playwright.async_api import Page


async def goto(
    page: Page,
    url: str,
    *,
    tries: int = 3,
    timeout: int = 40000,
    wait_until: str = "domcontentloaded",
) -> bool:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            return True
        except Exception as exc:  # noqa: BLE001 - transient network classes
            last = exc
            if attempt < tries - 1:
                await asyncio.sleep(2.0 * (attempt + 1))
    print(f"   nav gagal {tries}x: {type(last).__name__}: {str(last)[:90]}")
    return False
