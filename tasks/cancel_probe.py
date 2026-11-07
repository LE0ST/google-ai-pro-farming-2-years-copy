"""Open the cancel dialog and dump it fully: text, buttons, radios."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from app.config import settings  # noqa: E402
from app.runner import browser  # noqa: E402

ACC = "acc_pyro.solana.labs"
PLAY = "https://play.google.com/store/account/subscriptions?hl=en"

DUMP = """() => ({
  text: (document.body.innerText || ''),
  buttons: Array.from(document.querySelectorAll('button, [role="button"], [role="radio"], input[type="radio"], a'))
    .map((e) => { const r = e.getBoundingClientRect();
      return {tag: e.tagName, role: e.getAttribute('role'), t: (e.innerText || '').trim().slice(0, 60),
              aria: e.getAttribute('aria-label'), vis: r.width > 0 && r.height > 0,
              x: Math.round(r.x), y: Math.round(r.y)}; })
    .filter((b) => b.vis),
})"""


async def main() -> int:
    async with async_playwright() as p:
        ctx = await browser.launch(p, settings.profiles_dir / f"profile_{ACC}", headless=False)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(PLAY, wait_until="domcontentloaded")
        await asyncio.sleep(4)
        try:
            await page.locator('button:has-text("Manage")').first.click(timeout=8000)
        except Exception as exc:
            print("Manage gagal:", type(exc).__name__)
        await asyncio.sleep(4)
        await page.locator('button:has-text("Cancel subscription")').first.click(timeout=10000)
        await asyncio.sleep(3)

        info = await page.evaluate(DUMP)
        print("=== TEKS UTUH ===")
        print(info["text"])
        print("\n=== TOMBOL/RADIO TERLIHAT ===")
        for b in info["buttons"]:
            print(json.dumps(b, ensure_ascii=False))

        await page.screenshot(path=str(settings.artifacts_dir / "cancel_dialog.png"))
        print("\nSHOT:", settings.artifacts_dir / "cancel_dialog.png")
        await asyncio.sleep(3)
        await ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
