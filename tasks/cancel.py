"""Step 5: cancel auto-renew, then read the resulting end date.

Play's current UI is a single confirm dialog (no retention screen, no reason
radio), so the flow is: Manage -> Cancel subscription -> assert the dialog names
an end date -> confirm -> verify the page reports the cancellation.

Reversible: "Resubscribe" restores auto-renew; access runs to the term end.
"""

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

PLAY = "https://play.google.com/store/account/subscriptions?hl=en"


def say(tag: str, msg: str) -> None:
    print(f"[{tag:5}] {msg}", flush=True)


async def body(page) -> str:
    try:
        return " ".join((await page.inner_text("body", timeout=4000)).split())
    except Exception:
        return ""


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
        print("usage: .venv/bin/python tasks/cancel.py <email>")
        return 2
    acc_id = await _acc_id_for(sys.argv[1])
    async with async_playwright() as p:
        ctx = await browser.launch(p, settings.profiles_dir / f"profile_{acc_id}", headless=False)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(PLAY, wait_until="domcontentloaded")
        await asyncio.sleep(4)

        await page.locator('button:has-text("Manage")').first.click(timeout=10000)
        await asyncio.sleep(4)
        say("KLIK", "Manage")

        await page.locator('button:has-text("Cancel subscription")').first.click(timeout=10000)
        await asyncio.sleep(3)

        dialog = await body(page)
        say("DIALOG", dialog[-400:])
        m = re.search(r"canceled at the end of your billing period on ([A-Z][a-z]{2} \d{1,2}, \d{4})", dialog)
        if not m:
            say("GAGAL", "dialog tidak menyebut tanggal berakhir — tidak dikonfirmasi")
            await page.screenshot(path=str(settings.artifacts_dir / "cancel_nodate.png"))
            await asyncio.sleep(4)
            await ctx.close()
            return 1
        say("TANGGAL", m.group(1))

        # tombol konfirmasi di dalam dialog (yang terakhir, bukan pemicu)
        await page.locator('button:has-text("Cancel subscription")').last.click(timeout=10000)
        await asyncio.sleep(6)

        after = await body(page)
        say("SESUDAH", after[:400])
        dates = re.findall(r"(?:Ends on|Access until|Ends)[^.]{0,40}", after, re.IGNORECASE)
        say("PENANDA", str(dates) or "tidak ada")
        await page.screenshot(path=str(settings.artifacts_dir / "cancel_result.png"))
        say("SHOT", str(settings.artifacts_dir / "cancel_result.png"))
        await asyncio.sleep(4)
        await ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
