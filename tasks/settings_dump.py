"""Full text of the membership page, scrolled, filtered for dates and billing lines."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from app import db, vault  # noqa: E402
from app.config import settings  # noqa: E402
from app.runner import browser  # noqa: E402

URL = "https://one.google.com/settings?expand=upgrade&hl=en"

WANT = re.compile(
    r"next payment|renews|renewal|ends|until|expires|\$|20\d\d|payment|trial|cancel",
    re.IGNORECASE,
)


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
        print("usage: .venv/bin/python tasks/settings_dump.py <email>")
        return 2
    acc_id = await _acc_id_for(sys.argv[1])
    async with async_playwright() as p:
        ctx = await browser.launch(p, settings.profiles_dir / f"profile_{acc_id}", headless=False)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        for _ in range(10):
            await page.mouse.wheel(0, 700)
            await asyncio.sleep(0.5)

        txt = await page.inner_text("body")
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        print("=== baris relevan ===")
        for ln in lines:
            if WANT.search(ln):
                print(" *", ln[:160])

        print("\n=== 40 baris awal ===")
        for ln in lines[:40]:
            print("  ", ln[:140])

        await page.screenshot(path=str(settings.artifacts_dir / "settings_full.png"), full_page=True)
        print("\nSHOT:", settings.artifacts_dir / "settings_full.png")
        await asyncio.sleep(2)
        await ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
