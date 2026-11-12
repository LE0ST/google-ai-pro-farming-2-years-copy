"""Open the cancel dialog and dump it fully: text, buttons, radios."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from app import db, vault  # noqa: E402
from app.config import settings  # noqa: E402
from app.runner import browser  # noqa: E402

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


async def _acc_id_for(email: str) -> str:
    """Selesaikan acc_id dari email lewat vault (id akun acak, bukan turunan email)."""
    settings.ensure_dirs()
    await db.migrate()
    creds = await vault.account_creds(email)
    if not creds:
        print(f"akun tidak ada di database: {email}", flush=True)
        raise SystemExit(2)
    return creds[0]


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: .venv/bin/python tasks/cancel_probe.py <email>")
        return 2
    acc_id = await _acc_id_for(sys.argv[1])
    async with async_playwright() as p:
        ctx = await browser.launch(p, settings.profiles_dir / f"profile_{acc_id}", headless=False)
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
