"""Login as a state machine: look at the screen, do the one thing it asks, repeat.

Google inserts screens we cannot predict (reCAPTCHA "Verify it's you", recovery
prompts, account chooser). A fixed email->password sequence breaks on any of
them, so each iteration classifies the current screen and acts on that.

The reCAPTCHA checkbox is clicked automatically. When Google escalates to an
image grid, the human is asked to finish it in the visible window — that is why
runs use a headed browser.
"""

from __future__ import annotations

import asyncio

from playwright.async_api import Page

from ... import totp
from ...runner import nav
from . import fields

PLANS_PLAIN = "https://one.google.com/plans?hl=en"
SIGNIN = (
    "https://accounts.google.com/ServiceLogin"
    "?hl=en&continue=https%3A%2F%2Fone.google.com%2Fplans%3Fhl%3Den"
)

# Google's identifier field is a `type="text"` input named `identifier` — it has
# not been `type="email"` for some time, so that selector alone made the email
# step miss entirely. Keep the id first: it is the stable handle.
EMAIL_SEL = ["#identifierId", 'input[name="identifier"]', 'input[type="email"]']
PWD_SEL = ['input[name="Passwd"]', "#password", 'input[type="password"]']
TOTP_SEL = [
    'input[name="totpPin"]',
    "#totpPin",
    'input[name="Pin"]',
    'input[type="tel"][aria-label*="code" i]',
    'input[type="tel"]',
]
NEXT_SEL = ['#identifierNext', '#passwordNext', '#totpNext', 'button:has-text("Next")']
SKIP_SEL = ['button:has-text("Not now")', 'button:has-text("Skip")', 'button:has-text("Confirm")']

SIGNED_OUT = ("Sign in", "Sign in with Google", "Use your Google Account")
CAPTCHA_HINTS = ("not a robot", "Confirm you’re not a robot", "recaptcha", "unusual traffic")
DEVICE_HINTS = (
    "check your phone", "tap yes", "tap the number", "google prompt",
    "check your", "on your device", "verify it’s you", "verify it is you",
)
ANCHOR = 'iframe[title*="reCAPTCHA"]'


async def _visible(page: Page, selectors: list[str]) -> str | None:
    for sel in selectors:
        try:
            if await page.locator(sel).first.is_visible():
                return sel
        except Exception:
            continue
    return None


async def _fill(page: Page, sel: str, value: str, *, tries: int = 3) -> bool:
    return await fields.fill(page, sel, value, tries=tries)


async def _click(page: Page, selectors: list[str], *, timeout: int = 5000,
                 need_enabled: bool = True) -> bool:
    return await fields.click(page, selectors, timeout=timeout, need_enabled=need_enabled)


async def _body(page: Page, limit: int = 1500) -> str:
    try:
        return await page.inner_text("body", timeout=3000)
    except Exception:
        return ""


async def pass_captcha(page: Page, budget_s: float = 150.0) -> bool:
    """Click the checkbox; if Google escalates, hand over to the human."""
    solved = False
    for _ in range(3):
        try:
            frame = page.frame_locator(ANCHOR).locator("#recaptcha-anchor")
            await frame.click(timeout=6000)
            solved = True
            await asyncio.sleep(1.0)
            # A one-click pass is common: the anchor flips to checked and Google
            # moves on without ever showing a grid. Notice that here instead of
            # paying the full human-first budget below.
            try:
                checked = await frame.locator("#recaptcha-anchor[aria-checked='true']").count()
            except Exception:
                checked = 0
            if checked:
                print("   captcha lolos (otomatis)")
                await asyncio.sleep(1.0)
                await _click(page, NEXT_SEL, timeout=3000)
                return True
            break
        except Exception:
            await asyncio.sleep(2.0)

    if not solved:
        print("   checkbox captcha tidak bisa diklik otomatis — selesaikan di jendela Chrome")
    else:
        print("   checkbox captcha diklik, menunggu")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    announced = False
    while loop.time() < deadline:
        done = False
        try:
            done = bool(
                await page.frame_locator(ANCHOR)
                .locator("#recaptcha-anchor[aria-checked='true']")
                .count()
            )
        except Exception:
            done = False

        if not done:
            # Chrome drops the anchor frame once solved; treat "frame gone and
            # Google already moved on (or is asking for a credential)" as passed.
            anchor_gone = await page.locator(ANCHOR).count() == 0
            if anchor_gone and "recaptcha" not in page.url:
                done = True
            elif anchor_gone and await _visible(page, PWD_SEL + TOTP_SEL + EMAIL_SEL):
                done = True

        if done:
            print("   captcha lolos")
            await asyncio.sleep(1.0)
            await _click(page, NEXT_SEL, timeout=3000)
            return True

        if not announced and loop.time() > deadline - budget_s + 15:
            print("   >>> kalau muncul kotak gambar, pilih di jendela Chrome (maks 150s) <<<")
            announced = True
        await asyncio.sleep(2.0)

    print("   captcha tidak selesai dalam batas waktu")
    return False


async def is_signed_in(page: Page) -> bool:
    if "accounts.google.com" in page.url:
        return False
    head = (await _body(page))[:1200]
    return bool(head) and not any(marker in head for marker in SIGNED_OUT)


async def pass_screens(page: Page, email: str, password: str, secret: str,
                       max_steps: int = 18) -> bool:
    """Drive whatever credential/challenge screens are on screen, in place.

    Returns True once the URL leaves accounts.google.com. Used both by the initial
    login and by an in-page reauth (payments centre asks to "verify it's you"
    without leaving the flow)."""
    for step in range(max_steps):
        if "accounts.google.com" not in page.url:
            return True
        text = await _body(page, 2500)
        low = text.lower()

        if any(h.lower() in low for h in CAPTCHA_HINTS) or "challenge/recaptcha" in page.url:
            if not await pass_captcha(page):
                return False
            continue

        totp_field = await _visible(page, TOTP_SEL)
        if totp_field and any(
            k in text for k in ("2-Step", "verification code", "Enter the code", "Get a verification")
        ):
            wait = totp.wait_hint()
            if wait:
                print(f"   kode hampir kedaluwarsa, tunggu {wait}s")
                await asyncio.sleep(wait)
            code = totp.code(secret)
            if not code:
                print("   seed TOTP tidak terbaca")
                return False
            print(f"   kirim kode 2FA {code[:2]}••••")
            if await _fill(page, totp_field, code):
                await _click(page, NEXT_SEL)
            await asyncio.sleep(4.0)
            continue

        if (sel := await _visible(page, PWD_SEL)):
            print(f"   isi password (langkah {step + 1})")
            if not await _fill(page, sel, password):
                # Do not click Next on an empty field: that burns the step budget
                # on a silently-swallowed click and ends in a false "stuck".
                print("   password tidak masuk — ulangi langkah ini")
                await asyncio.sleep(2.0)
                continue
            await _click(page, NEXT_SEL)
            await asyncio.sleep(3.0)
            continue

        if (sel := await _visible(page, EMAIL_SEL)):
            print(f"   isi email (langkah {step + 1})")
            if not await _fill(page, sel, email):
                print("   email tidak masuk — ulangi langkah ini")
                await asyncio.sleep(2.0)
                continue
            await _click(page, NEXT_SEL)
            await asyncio.sleep(2.5)
            continue

        if any(k in text for k in ("Not now", "Skip", "Confirm your recovery", "Add recovery")):
            print("   lewati prompt pemulihan")
            await _click(page, SKIP_SEL, timeout=3000)
            await asyncio.sleep(2.0)
            continue

        # Device prompt: nothing to type, the human approves on their phone. Wait
        # rather than burn the step budget, or the run dies mid-login.
        if any(h in low for h in DEVICE_HINTS) and not await _visible(page, PWD_SEL + TOTP_SEL):
            print("   >>> device prompt: setujui di HP Anda (maks 5 menit, tidak dihitung langkah) <<<")
            for _ in range(60):
                if "accounts.google.com" not in page.url:
                    return True
                await asyncio.sleep(5)
                fresh = (await _body(page, 1200)).lower()
                if not any(h in fresh for h in DEVICE_HINTS):
                    break
            print("   lanjut setelah device prompt")
            continue

        if await _click(page, NEXT_SEL, timeout=2500):
            await asyncio.sleep(2.5)
            continue

        print(f"   menunggu ({step + 1}/{max_steps}) · url={page.url[:70]}")
        await asyncio.sleep(2.5)
    return "accounts.google.com" not in page.url


async def pass_reauth(page: Page, email: str, password: str, secret: str) -> bool:
    """Reauth triggered inside a flow (payments centre). Clicks the verify button
    first if one is on screen, then drives the credential screens."""
    for sel in ('button:has-text("Verify that")', 'button:has-text("Verify")'):
        try:
            el = page.locator(sel).first
            if await el.count() and await el.is_visible():
                await el.click(timeout=6000)
                print("   tombol verifikasi ditekan")
                break
        except Exception:
            continue
    await asyncio.sleep(3)
    return await pass_screens(page, email, password, secret, max_steps=12)


async def ensure_signed_in(page: Page, email: str, password: str, secret: str) -> bool:
    """Idempotent: returns immediately when the profile is already authenticated."""
    await nav.goto(page, PLANS_PLAIN)
    await asyncio.sleep(2.5)
    if await is_signed_in(page):
        print("   profil sudah tertaut")
        return True

    await nav.goto(page, SIGNIN)
    await asyncio.sleep(1.5)

    await pass_screens(page, email, password, secret)

    await nav.goto(page, PLANS_PLAIN)
    await asyncio.sleep(2.5)
    if await is_signed_in(page):
        return True

    print(f"   belum berhasil masuk · url={page.url[:90]}")
    print(f"   teks: {(await _body(page, 400)).replace(chr(10), ' | ')[:400]}")
    return False
