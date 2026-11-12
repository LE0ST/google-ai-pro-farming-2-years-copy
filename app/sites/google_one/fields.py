"""Typing and clicking against Google's JS-driven forms.

Both the login and the signup flow drive the same Material inputs, and both hit
the same failure: the field node is replaced when Google re-renders it, so
keystrokes sent across that swap land on a detached element. The value never
arrives, `aria-invalid` stays set, the Next control stays disabled, and the
click on it is silently swallowed — the run then spins on the same screen until
it runs out of steps and reports a false "stuck".

`fill` and `click` here verify the effect rather than the request: read the
value back, wait for the control to become enabled, and retry on a fresh node
handle when either did not hold.
"""

from __future__ import annotations

import asyncio

from playwright.async_api import Page


async def fill(page: Page, sel: str, value: str, *, tries: int = 3) -> bool:
    """Type `value` into the first match of `sel` and confirm it stuck.

    When the selector matches nothing the field is simply not on this screen —
    that is a miss, not a transient error, so bail at once instead of spending
    the click timeout three times over.
    """
    try:
        await page.locator(sel).first.wait_for(state="attached", timeout=6000)
    except Exception:
        print(f"   kolom tidak ada: {sel}")
        return False

    for attempt in range(tries):
        el = page.locator(sel).first
        try:
            await el.wait_for(state="visible", timeout=5000)
            await el.click(timeout=5000)
        except Exception as exc:  # noqa: BLE001 - element swap races
            print(f"   isi gagal (klik): {type(exc).__name__}")
            await asyncio.sleep(1.0)
            continue

        try:
            current = await el.input_value(timeout=3000)
        except Exception:
            current = ""

        if current == value:
            return True

        if current:  # clear only when there is something to clear
            try:
                await el.press("ControlOrMeta+a")
                await el.press("Delete")
            except Exception:
                try:
                    await el.evaluate(
                        "e => { e.value = ''; e.dispatchEvent(new Event('input',{bubbles:true})); }"
                    )
                except Exception:
                    pass

        try:
            await el.press_sequentially(value, delay=60)
        except Exception as exc:  # noqa: BLE001
            print(f"   isi gagal (ketik): {type(exc).__name__}")
            await asyncio.sleep(1.0)
            continue

        await asyncio.sleep(0.35)  # let the input handler commit the value
        try:
            landed = await el.input_value(timeout=3000)
        except Exception:
            landed = ""

        if landed == value:
            return True

        bad = ""
        try:
            bad = await el.get_attribute("aria-invalid", timeout=2000) or ""
        except Exception:
            pass
        print(f"   isi tidak masuk (percobaan {attempt + 1}/{tries}, "
              f"panjang={len(landed)}, aria-invalid={bad or '-'})")
        await asyncio.sleep(1.2)

    return False


async def click(
    page: Page,
    selectors: list[str],
    *,
    timeout: int = 5000,
    need_enabled: bool = True,
) -> bool:
    """Click the first selector that is present.

    `visible` is not enough: Google renders the Next control long before the
    form validates, and the browser swallows a click on the disabled node. When
    `need_enabled` is set, wait for the control to accept input first.
    """
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=timeout)
        except Exception:
            continue

        if need_enabled:
            try:
                await page.wait_for_function(
                    "s => { const e = document.querySelector(s);"
                    " return !e || (!e.disabled && e.getAttribute('aria-disabled') !== 'true'); }",
                    sel, timeout=timeout,
                )
            except Exception:
                pass  # not a gated control; try the click anyway

        for _ in range(2):
            try:
                await el.click(timeout=timeout, no_wait_after=True)
                return True
            except Exception:
                await asyncio.sleep(0.4)

    return False
