"""Probe halaman metode pembayaran akun: daftar frame, kartu, tombol Remove.

Email akun diambil dari argumen; kredensial dan last4 kartu diselesaikan
lewat vault seperti subscribe_flow.py — tidak ada identitas akun di kode.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from app import db, egress, vault  # noqa: E402
from app.config import settings  # noqa: E402
from app.runner import browser, nav  # noqa: E402
from app.sites.google_one import checkout, login  # noqa: E402


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: .venv/bin/python tasks/probe_pay.py <email>")
        return 2
    email = sys.argv[1]
    settings.ensure_dirs()
    await db.migrate()
    creds = await vault.account_creds(email)
    if not creds:
        print(f"akun tidak ada di database: {email}", flush=True)
        return 2
    acc_id, password, secret = creds
    row = await db.fetch_one("SELECT card_id FROM accounts WHERE id = ?", (acc_id,))
    last4 = ""
    if row and row["card_id"]:
        card = await db.fetch_one("SELECT last4 FROM cards WHERE id = ?", (row["card_id"],))
        last4 = card["last4"] if card else ""
    async with async_playwright() as p:
        ctx = await browser.launch(p, settings.profiles_dir / f"profile_{acc_id}", headless=False)
        await ctx.add_init_script(await checkout.init_script())
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await nav.goto(page, egress.ECHO)
        print("egress:", await egress.observe(page))
        print("signed:", await login.ensure_signed_in(page, email, password, secret))
        await nav.goto(page, "https://play.google.com/store/paymentmethods")
        await asyncio.sleep(8)
        print("BODY:", (await page.evaluate("() => document.body.innerText"))[:400].replace("\n", " | "))
        await page.locator('text="Edit payment methods"').first.click(timeout=10000)
        await asyncio.sleep(9)
        print("URL2:", page.url)
        print("FRAMES2:", [f.url[:110] for f in page.frames][:8])
        print("BODY2:", (await page.evaluate("() => document.body.innerText"))[:400].replace("\n", " | "))
        await asyncio.sleep(2)
        print("URL3:", page.url)
        for i, f in enumerate(page.frames):
            try:
                t = (await f.evaluate("() => document.body.innerText")).replace("\n", " | ")
                print(f"FRAME{i} {f.url[:70]} :: {t[:400]}")
            except Exception as exc:
                print(f"FRAME{i} {f.url[:70]} ERR {type(exc).__name__}")
        pay = [f for f in page.frames if "payment_methods" in f.url]
        if pay:
            await pay[0].locator('text="Remove"').first.click(timeout=8000)
            await asyncio.sleep(2.5)
            for i, f in enumerate(page.frames):
                try:
                    t = (await f.evaluate("() => document.body.innerText")).replace("\n", " | ")
                    if (last4 and last4 in t) or "Remove" in t or "removed" in t.lower():
                        print(f"AFTER{i} :: {t[:500]}")
                except Exception:
                    continue
            print("BUTTONS:", await pay[0].evaluate(
                "() => [...document.querySelectorAll('button,[role=button]')].map(b => (b.innerText||'').trim()).filter(Boolean).slice(0,10)"))
        await page.screenshot(path=str(settings.artifacts_dir / "payment_methods.png"))
        await asyncio.sleep(2)
        await ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
