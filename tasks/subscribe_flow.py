"""Subscribe automation: 3 layers then cancel. Assembled from what ran for real.

    .venv/bin/python tasks/subscribe_flow.py <email> [--layers 1,2,3] [--dry]

Sequence, exactly as proven on a live account:

  egress US gate -> login (password + TOTP + captcha hand-over) ->
  layer 1: /plans, "Get started", guard for "12-month free trial" + $0.00 ->
  Subscribe -> settle 45s -> layer 2: /settings, "Upgrade", guard for today+18M ->
  Subscribe -> settle -> layer 3: same, guard for today+24M -> Subscribe -> settle ->
  cancel: Manage, Cancel subscription, assert the end date, confirm, verify "Canceled".

Two habits carried over because they are what made it work:
  * the dialog is read ONLY from the eacquire frame, and only once that frame has
    rendered something recognisable — reading it early returns script noise and a
    guard would then reject a perfectly good dialog;
  * every commit is preceded by a date check, and a mismatch aborts before clicking.

`--dry` stops before each Subscribe. Needs a card already on the account.
"""

from __future__ import annotations

import asyncio
import calendar
import re
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import Page, async_playwright  # noqa: E402

from app import db, egress, secrets, vault  # noqa: E402
from app.config import settings  # noqa: E402
from app.runner import browser, nav  # noqa: E402
from app.sites.google_one import checkout, discover, guard, login  # noqa: E402

WANT = "US"
PLAY = "https://play.google.com/store/account/subscriptions?hl=en"
SETTINGS = "https://one.google.com/settings?expand=upgrade&hl=en"
SETTLE_S = 45

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
DATE_RE = re.compile(r"\b([A-Z][a-z]{2})[a-z]*\s+(\d{1,2}),\s+(\d{4})\b")
DIALOG_MARKERS = ("upcoming charges", "free trial", "today $0", "add payment method")


def say(tag: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {tag:6} {msg}", flush=True)


def add_months(base: date, months: int) -> date:
    year = base.year + (base.month - 1 + months) // 12
    month = (base.month - 1 + months) % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))

def today() -> date:
    """Google bills in the browser's timezone, which is pinned to LA. Using the
    machine clock (WIB) is a day ahead around midnight."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).date()


def near(got: list[date], expected: date, slack: int = 5) -> bool:
    # Slack 5 hari: layer berjarak ±180 hari, jadi tetap tak ambigu, tapi
    # selisih akhir pekan / zona waktu pada tanggal proyeksi tidak menggagalkan.
    return any(abs((d - expected).days) <= slack for d in got)


def dates_in(text: str) -> list[date]:
    out = []
    for mon, day, year in DATE_RE.findall(text):
        month = MONTHS.get(mon.lower())
        if month:
            out.append(date(int(year), month, int(day)))
    return out


async def eacq_frames(page: Page) -> list:
    return [f for f in page.frames if "eacquire" in f.url]


async def frame_text(page: Page) -> str:
    for f in await eacq_frames(page):
        try:
            return " ".join((await f.inner_text("body", timeout=3000)).split())
        except Exception:
            continue
    return ""


async def body(page: Page) -> str:
    try:
        return " ".join((await page.inner_text("body", timeout=4000)).split())
    except Exception:
        return ""


async def await_dialog(page: Page, seconds: int = 40) -> str:
    """Wait for the eacquire frame to exist and read like a checkout dialog."""
    best = ""
    for _ in range(seconds):
        await asyncio.sleep(1.0)
        text = await frame_text(page)
        if not text:
            continue
        # Local patch: through this VM's relay the unrendered eacquire frame
        # can report its own inline script source (tens of KB) as body text,
        # which false-matches the markers below. A rendered dialog is small;
        # keep waiting instead of accepting raw source.
        if len(text) > 20000:
            continue
        if any(m in text.lower() for m in DIALOG_MARKERS):
            return text
        best = text
    return best


async def click_visible(page: Page, selector: str) -> bool:
    """Click the first *visible* match. The plans page pre-mounts hidden copies of
    the same button, and `.first` lands on a 0x0 node that never becomes visible."""
    loc = page.locator(selector)
    for i in range(await loc.count()):
        el = loc.nth(i)
        try:
            if await el.is_visible():
                await el.click(timeout=8000)
                return True
        except Exception:
            continue
    return False


async def click_pro_discount(page: Page) -> bool:
    """Local patch: this account's plans page labels the AI Pro CTA
    "Get discount" (Annual view) instead of "Get started", and the generic
    "Upgrade" selector only hits the sidebar nav. Click the discount button
    that belongs to the Google AI Pro card specifically."""
    js = """() => {
      // Visible "Get discount" buttons run left->right: Storage, AI Plus,
      // AI Pro. Ultra has no discount button, so the last visible one is
      // the AI Pro card's. (Card headings are not ancestors of the button,
      // so ancestry matching fails; geometry is the reliable key here.)
      const btns = Array.from(document.querySelectorAll('button, a, [role="button"]'))
        .filter((e) => {
          const r = e.getBoundingClientRect();
          return (e.innerText || '').trim() === 'Get discount' && r.width > 15 && r.height > 15;
        })
        .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
      if (!btns.length) return false;
      btns[btns.length - 1].click();
      return true;
    }"""
    try:
        return bool(await page.evaluate(js))
    except Exception:
        return False


async def open_dialog(page: Page, layer: int) -> str:
    """Navigate, arm, click the action. Retries once if nothing opens."""
    url = checkout.PLANS if layer == 1 else SETTINGS
    # An account that already holds a plan is redirected to the settings page even
    # for layer 1, where the action is "Upgrade" — so both labels are candidates.
    sels = ['button:has-text("Get started")', 'button:has-text("Upgrade")'] if layer == 1 \
        else ['button:has-text("Upgrade")']

    for attempt in range(2):
        await nav.goto(page, url)
        for _ in range(15):
            await asyncio.sleep(1.0)
            if await page.evaluate("() => location.hostname.includes('one.google.com')"):
                break
        await asyncio.sleep(2.5)
        await page.evaluate(checkout.HOOK_JS)
        await checkout.arm(page, layer, await checkout.capture_sub_id(page))

        clicked = False
        if layer == 1:
            clicked = await click_pro_discount(page)
        if not clicked:
            for sel in sels:
                if await click_visible(page, sel):
                    clicked = True
                    break
        if not clicked:
            say("DIALOG", f"tombol aksi tidak ada yang terlihat (percobaan {attempt + 1})")
            continue
        text = await await_dialog(page)
        if text:
            return text
        say("DIALOG", f"dialog tidak terbuka (percobaan {attempt + 1})")
    return ""


async def click_in_frame(page: Page, selectors: list[str]) -> bool:
    for f in await eacq_frames(page):
        for sel in selectors:
            try:
                el = f.locator(sel).last
                if await el.count():
                    await el.click(timeout=6000)
                    return True
            except Exception:
                continue
    return False


async def find_button(page: Page, selectors: list[str], budget_s: float = 60.0, frames=None):
    """Return the first visible *and enabled* match, waiting for it to become live.

    The Subscribe button exists before the quote settles and swallows clicks while
    disabled, which reads as "cannot be pressed". `frames` narrows the search to the
    dialog, otherwise the page's own Subscribe behind the modal gets picked.
    """
    for _ in range(int(budget_s / 0.5)):
        for f in (frames if frames is not None else page.frames):
            for sel in selectors:
                try:
                    loc = f.locator(sel)
                    for i in range(min(await loc.count(), 3)):
                        el = loc.nth(i)
                        try:
                            await el.scroll_into_view_if_needed(timeout=1500)
                        except Exception:
                            pass
                        if await el.is_visible() and await el.is_enabled():
                            return el
                except Exception:
                    continue
        await asyncio.sleep(0.5)
    return None


async def commit(page: Page) -> bool:
    sels = ['button:text-is("Subscribe")', 'button:has-text("Subscribe")']
    for attempt in range(2):
        frames = await eacq_frames(page) or list(page.frames)
        for f in frames:                          # dialog panjang: gulir ke dasar dulu
            try:
                await f.evaluate("() => { for (const e of document.querySelectorAll('*'))"
                                 " { if (e.scrollHeight > e.clientHeight + 40) e.scrollTop = e.scrollHeight; }"
                                 " window.scrollTo(0, document.body.scrollHeight); }")
            except Exception:
                pass
        await asyncio.sleep(0.6)
        btn = await find_button(page, sels, 25.0 if attempt else 60.0, frames)
        if btn is None:
            say("LAYER", "tombol Subscribe tidak pernah aktif")
            return False
        try:
            await btn.click(timeout=8000)
        except Exception as exc:
            say("LAYER", f"klik Subscribe gagal: {type(exc).__name__}")
            continue
        for _ in range(20):
            await asyncio.sleep(1.5)
            if not await eacq_frames(page):
                return True
            if "something went wrong" in (await frame_text(page)).lower():
                say("LAYER", "Google menolak: Something went wrong")
                return False
            await click_in_frame(page, ['button:has-text("Not now")', 'button:has-text("Got it")',
                                        'button:has-text("Done")', 'button:has-text("Continue")'])
        if attempt == 0:
            say("LAYER", "dialog tidak bergerak — coba tekan sekali lagi")
    say("LAYER", "dialog masih terbuka setelah dua klik — lanjut verifikasi")
    return True


async def click_any_frame(page: Page, selectors: list[str]) -> bool:
    """Click inside whichever frame holds the control. Card entry lives in the
    instrumentmanager frame, not the eacquire one."""
    for f in page.frames:
        for sel in selectors:
            try:
                loc = f.locator(sel)
                for i in range(await loc.count()):
                    el = loc.nth(i)
                    if await el.is_visible():
                        await el.click(timeout=6000)
                        return True
            except Exception:
                continue
    return False


async def ensure_card(page: Page, acc_id: str) -> bool:
    """Add the account's assigned card to its Google payment profile.

    The vault only records *which* card to use; nothing is on Google's side until
    this runs. Field order in the card form is number, expiry, CVC, holder, ZIP.
    """
    row = await db.fetch_one("SELECT card_id FROM accounts WHERE id = ?", (acc_id,))
    card = await db.fetch_one("SELECT * FROM cards WHERE id = ?", (row["card_id"],)) if row else None
    if not card:
        say("KARTU", "akun tidak punya kartu di vault")
        return False
    if card["status"] != "active":
        # Kartu mati tidak boleh ditempel walau penugasannya belum sempat
        # diputar kill_card — menempelkannya hanya membuang satu run.
        say("KARTU", f"kartu {card['brand']}-{card['last4']} berstatus {card['status']}, "
                     "bukan active — berhenti")
        return False

    number = vault.secret_get(card["number_ref"])
    cvc = vault.secret_get(card["cvc_ref"])
    if not number or not cvc:
        say("KARTU", "nomor/CVC tidak terbaca dari vault")
        return False
    exp = f"{card['exp_month']}{card['exp_year']}"
    say("KARTU", f"tempel {card['brand']}-{card['last4']} ke akun")

    if not await click_any_frame(page, ['button:has-text("Add card")', 'text="Add card"',
                                        '[role="button"]:has-text("Add card")']):
        say("KARTU", "tombol Add card tidak ditemukan")
        return False

    for _ in range(25):
        await asyncio.sleep(1.0)
        frame = None
        for f in page.frames:
            try:
                if await f.locator("input:visible").count() >= 3:
                    frame = f
                    break
            except Exception:
                continue
        if frame is None:
            continue
        say("FRAME", f"form kartu di {frame.url[:70]}")
        inputs = frame.locator("input:visible")
        values = [number, exp, cvc]
        saved = False
        for attempt in range(2):
            if attempt:
                say("KARTU", "form belum tuntas — isi ulang pelan")
                await asyncio.sleep(2.0)
                frame = None
                for f in page.frames:
                    try:
                        if await f.locator("input:visible").count() >= 3:
                            frame = f
                            break
                    except Exception:
                        continue
                if frame is None:
                    break
                inputs = frame.locator("input:visible")
            await asyncio.sleep(0.4)

            for idx, value in enumerate(values):
                field = inputs.nth(idx)
                await field.click()
                await field.fill("")
                # Nomor kartu diformat ulang oleh Google sambil diketik, jadi lebih
                # pelan dan diberi jeda supaya field berikutnya tidak bergeser.
                await field.press_sequentially(value, delay=70 if idx == 0 else 45)
                await asyncio.sleep(0.9 if idx == 0 else 0.45)
            if await inputs.count() >= 5:
                try:
                    if not (await inputs.nth(3).input_value()).strip():
                        await inputs.nth(3).fill("CARD HOLDER")
                    await inputs.nth(4).fill("97201")
                except Exception:
                    pass
            await asyncio.sleep(0.8)

            save = 'button:has-text("Save card")'
            for _ in range(12):                   # tunggu tombol simpan aktif
                try:
                    loc = frame.locator(save).first
                    if not await loc.count():
                        break
                    if not await loc.is_disabled():
                        break
                except Exception:
                    break
                await asyncio.sleep(0.4)

            say("KARTU", "data terisi, menyimpan")
            if not await click_any_frame(page, ['button:has-text("Save card")',
                                                'button:has-text("Save")', 'text=/^Save$/']):
                say("KARTU", "tombol simpan tidak ditemukan")
                continue

            for _ in range(25):
                await asyncio.sleep(1.5)
                text = await frame_text(page)
                if "add payment method" not in text.lower() and (
                    "today" in text.lower() or "free trial" in text.lower()
                ):
                    say("KARTU", "kartu tersimpan, dialog kembali ke checkout")
                    return True
            saved = True
        say("KARTU", "dialog tidak kembali setelah menyimpan" if saved
            else "kartu gagal terisi")
        return False
    say("KARTU", "form kartu tidak muncul")
    return False


async def run_layers(page: Page, email: str, acc_id: str, layers: list[int], dry: bool) -> bool:
    """Drive the requested layers, verifying by date before every commit.

    Nothing here trusts the caller's layer list blindly: each iteration re-reads
    the dialog and, if the account is already past that layer, skips it instead of
    submitting a duplicate.
    """
    end = await current_end_date(page)
    have = discover.months_from(discover.today(), end) if end else 0
    stage = discover.stage_for(have)
    say("DISC", f"term sekarang {end or 'tanpa langganan'} → {stage.label} ({have} bulan)")
    capped = False
    if stage.done:
        # Already at the cap. Try the spare token once: if Google takes it the term
        # moves, if it answers "Something went wrong" the cap is real and we finish.
        say("LAYER", "sudah 24 bulan — mencoba layer 4 sekali")
        layers = [4]
        capped = True
    else:
        if have:
            await resubscribe(page)
        layers = [n for n in layers if n >= stage.start_layer]
        if not layers:
            say("LAYER", "tidak ada layer tersisa")
            return True
        if layers[0] > 1:
            say("LAYER", f"mulai dari layer {layers[0]} (sudah {have} bulan)")

    for index, layer in enumerate(layers):
        await db.execute("UPDATE accounts SET step = ?, status = 'running' WHERE email = ?",
                         (layer, email))
        say("LAYER", f"{layer} — buka dialog")
        text = await open_dialog(page, layer)
        shot = settings.artifacts_dir / f"flow_L{layer}_{int(time.time())}.png"
        await page.screenshot(path=str(shot))
        say("SHOT", str(shot))

        if not text:
            if capped and layer == 4:
                say("LAYER", "layer 4 ditolak — batas 24 bulan nyata")
                return True
            say("LAYER", "dialog kosong — berhenti")
            return False
        if "add payment method" in text.lower():
            if dry:
                say("DRY", "kartu belum terpasang di akun — isi manual bila perlu")
            elif not await ensure_card(page, acc_id):
                say("LAYER", "pengisian kartu gagal — berhenti sebelum commit")
                return False
            else:
                text = await await_dialog(page, 40)
                if not text:
                    say("LAYER", "dialog tidak kembali setelah kartu disimpan")
                    return False

        got = dates_in(text)

        # Guard gabungan di semua layer: harga hari ini harus nol (guard.judge,
        # terikat label "due today") DAN tanggal berakhir harus cocok. Dulu
        # layer 1 hanya mengecek string "$0.00" dan layer 2-4 hanya tanggal —
        # dialog yang menagih nyata bisa lolos dari salah satu sisi.
        verdict = guard.judge(text)
        if layer == 4:
            expected = add_months(discover.today(), 24)
            date_ok = near(got, expected) or near(got, add_months(discover.today(), 30))
        else:
            expected = add_months(discover.today(), {1: 12, 2: 18, 3: 24}[layer])
            date_ok = near(got, expected)
        ok = verdict.safe and date_ok
        say("GUARD", f"layer {layer}: harga {'ok' if verdict.safe else 'GAGAL'} "
                     f"({verdict.reason}) · tanggal harap ±{expected} · terbaca {got or '—'}"
                     f" → {'lolos' if ok else 'GAGAL'}")
        if not ok:
            print("        " + text[:280])
            if capped and layer == 4:
                say("LAYER", "layer 4 ditolak — batas 24 bulan nyata, lanjut cancel")
                return True
            return False

        if dry:
            say("DRY", f"layer {layer} siap, tidak ditekan")
            continue

        if not await commit(page):
            say("LAYER", "tombol Subscribe tidak bisa ditekan — berhenti")
            return False
        say("LAYER", f"{layer} terkirim · settlement {SETTLE_S}s")
        await asyncio.sleep(SETTLE_S)
        await db.execute("UPDATE accounts SET step = ? WHERE email = ?", (layer + 1, email))

        if index < len(layers) - 1:
            nxt = layers[index + 1]
            verify_text = await open_dialog(page, nxt)
            expected = add_months(discover.today(), 18 if nxt == 2 else 24)
            vgot = dates_in(verify_text)
            good = near(vgot, expected)
            say("VERIF", f"setelah layer {layer}: harap ±{expected} · terbaca {vgot or '—'} → "
                         f"{'cocok' if good else 'TIDAK cocok'}")
            if not good:
                return False
    return True


async def resubscribe(page: Page) -> None:
    """A cancelled plan cannot take a plan change; Play offers "Resubscribe"."""
    txt = await body(page)
    if "Canceled" not in txt or "Resubscribe" not in txt:
        return
    say("AKTIF", "langganan berstatus Canceled — resubscribe dulu")
    try:
        await page.locator('button:has-text("Resubscribe")').first.click(timeout=8000)
        await asyncio.sleep(4)
        for sel in ('button:has-text("Subscribe")', 'button:has-text("Confirm")',
                    'button:has-text("Resubscribe")'):
            try:
                await page.locator(sel).last.click(timeout=4000)
                break
            except Exception:
                continue
        await asyncio.sleep(5)
    except Exception as exc:
        say("AKTIF", f"resubscribe gagal: {type(exc).__name__}")
        return
    after = await body(page)
    say("AKTIF", "aktif kembali" if "Canceled" not in after else "masih Canceled")


async def current_end_date(page: Page) -> "date | None":
    """True term end, read from the cancel dialog without committing.

    The offers dialog projects the term *after* the armed layer, so it cannot say
    where the account is now. The cancel dialog states it outright.
    """
    await nav.goto(page, PLAY)
    await asyncio.sleep(4)
    txt = await body(page)
    if "Canceled" in txt and "will end on" in txt:
        hit = re.search(r"will end on ([A-Z][a-z]{2} \d{1,2}, \d{4})", txt)
        return parse_date(hit.group(1)) if hit else None
    if "Active" not in txt:
        return None
    try:
        await page.locator('button:has-text("Manage")').first.click(timeout=8000)
        await asyncio.sleep(3)
        await page.locator('button:has-text("Cancel subscription")').first.click(timeout=8000)
        await asyncio.sleep(3)
    except Exception:
        return None
    dialog = await body(page)
    hit = re.search(r"canceled at the end of your billing period on ([A-Z][a-z]{2} \d{1,2}, \d{4})", dialog)
    try:
        await page.locator('button:has-text("Keep subscription")').first.click(timeout=4000)
    except Exception:
        pass
    return parse_date(hit.group(1)) if hit else None


def parse_date(text: str) -> "date | None":
    m = re.match(r"([A-Z][a-z]{2})[a-z]* (\d{1,2}), (\d{4})", text.strip())
    if not m:
        return None
    month = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
             "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}.get(m.group(1).lower())
    if not month:
        return None
    return date(int(m.group(3)), month, int(m.group(2)))


async def any_text(page: Page, needle: str) -> bool:
    """payments.google.com renders the card list inside a frame, so the main body
    alone never shows it."""
    for f in page.frames:
        try:
            if needle in " ".join((await f.inner_text("body", timeout=3000)).split()):
                return True
        except Exception:
            continue
    return False


async def click_text(page: Page, pattern: str) -> bool:
    for f in page.frames:
        try:
            loc = f.locator(pattern).first
            if await loc.count():
                await loc.click(timeout=6000)
                return True
        except Exception:
            continue
    return False


async def click_near(page: Page, needle: str, pattern: str, also: str | None = None) -> bool:
    """Click inside the frame that owns `needle` (and `also`, if given), so a plain
    "Remove" hits the right card instead of a row that merely mentions it."""
    for f in page.frames:
        try:
            txt = await f.inner_text("body", timeout=2000)
            if needle not in txt or (also and also not in txt):
                continue
            loc = f.locator(pattern).first
            if await loc.count():
                await loc.click(timeout=2500)
                return True
        except Exception:
            continue
    return False


async def has_text(page: Page, pattern: str) -> bool:
    for f in page.frames:
        try:
            if await f.locator(pattern).count():
                return True
        except Exception:
            continue
    return False


async def detach_on_google(page: Page, last4: str) -> bool:
    """Best effort: remove the card from the account's payment profile.

    Play hands off to payments.google.com, which demands a re-authorisation that only
    offers "tap Yes on your phone" — no password path. So we wait for the human, then
    remove. Returns False rather than raising so the run still counts as done.
    """
    try:
        await nav.goto(page, "https://play.google.com/store/paymentmethods")
        asked = saved = tried_edit = False
        absent = 0
        # Halaman Play/payments mengikuti bahasa akun, bukan ?hl=en — akun
        # Indonesia menampilkan label Indonesia. Semua pencocokan teks di sini
        # dwibahasa; label tombol verifikasi pun dua varian ("Verify that
        # it's you" di Play, "Verify it's you" di payments center).
        for _ in range(750):                      # ±300s, tanpa tunggu tetap
            if await any_text(page, "sure that it's you") or \
                    await any_text(page, "memastikan bahwa ini memang anda"):
                # Re-auth hanya terkirim kalau tombolnya ditekan; kalau sesi sudah
                # terverifikasi tombol ini memang tidak muncul lagi. Ini dicek lebih
                # dulu karena selama verifikasi berjalan tidak ada yang bisa diklik.
                if not asked and (
                    await click_text(page, 'text="Verify that it\'s you"')
                    or await click_text(page, 'text="Verify it\'s you"')
                    or await click_text(page, 'text="Verifikasi diri Anda"')
                ):
                    asked = True
                    say("KARTU", "permintaan dikirim ke HP — setujui di sana (maks 300s)")
            elif await any_text(page, "will be removed") or \
                    await any_text(page, "akan dihapus"):
                if not saved and (
                    await click_text(page, 'text="Remove and save"')
                    or await click_text(page, 'text="Hapus dan simpan"')
                ):
                    saved = True
                    await asyncio.sleep(1.2)
                if not await any_text(page, last4):
                    return True
            elif await any_text(page, last4):
                absent = 0
                if not saved:
                    if await click_near(page, last4, 'text="Remove"', also="Remove") or \
                            await click_near(page, last4, 'text="Hapus"', also="Hapus"):
                        await asyncio.sleep(1.0)
            elif await any_text(page, "Add a payment") or \
                    await any_text(page, "Tambahkan metode pembayaran"):
                # Daftar metode bayar sudah tampil (wallet maupun halaman Play) tapi
                # kartunya tidak ada di situ — dengan nol kartu pun tautan edit hilang.
                absent += 1
                if absent >= 3:
                    say("KARTU", "kartu sudah tidak ada di akun")
                    return False
            elif not tried_edit and (
                await has_text(page, 'text="Edit payment methods"')
                or await has_text(page, 'text="Edit metode pembayaran"')
            ):
                tried_edit = True
                await click_text(page, 'text="Edit payment methods"')
                await click_text(page, 'text="Edit metode pembayaran"')
            await asyncio.sleep(0.4)
        say("KARTU", "verifikasi tidak selesai — kartu dilepas di vault saja")
        return False
    except Exception as exc:
        say("KARTU", f"pelepasan di Google gagal: {type(exc).__name__}")
        return False


async def release_card(page: Page, acc_id: str) -> None:
    """Drop the card from the account, but keep it in the vault for the next one.

    Google side is best effort (the payments page often renders blank under
    automation); the vault assignment is always cleared so the card is not left
    pinned to a finished account. The card row itself is never deleted.
    """
    row = await db.fetch_one(
        "SELECT a.card_id, c.last4 FROM accounts a JOIN cards c ON c.id = a.card_id WHERE a.id = ?",
        (acc_id,))
    card_id = row["card_id"] if row else ""
    if not card_id:
        return
    removed = await detach_on_google(page, row["last4"])
    say("KARTU", "dilepas dari akun" if removed else "pelepasan di Google tidak terbukti")
    # Penugasan vault tetap dibersihkan (desain), tapi kegagalan di sisi Google
    # harus terlihat di UI lewat step_message — bukan hanya di log run.
    await db.execute(
        "UPDATE accounts SET step_message = ? WHERE id = ?",
        ("" if removed else "kartu belum terlepas di Google", acc_id),
    )
    await db.execute("UPDATE accounts SET card_id = '' WHERE id = ?", (acc_id,))
    say("KARTU", "penugasan dilepas di vault — kartu tetap tersimpan")


async def cancel(page: Page) -> bool:
    await nav.goto(page, PLAY)
    await asyncio.sleep(4)
    state = await body(page)
    if "Canceled" in state and "will end on" in state:
        say("CANCEL", "sudah Canceled — dilewati")
        return True
    try:
        await page.locator('button:has-text("Manage")').first.click(timeout=10000)
    except Exception:
        say("CANCEL", "tombol Manage tidak ada")
        return False
    await asyncio.sleep(4)
    try:
        await page.locator('button:has-text("Cancel subscription")').first.click(timeout=10000)
    except Exception:
        say("CANCEL", "tombol Cancel subscription tidak ada")
        return False
    await asyncio.sleep(3)

    dialog = await body(page)
    m = re.search(r"canceled at the end of your billing period on ([A-Z][a-z]{2} \d{1,2}, \d{4})", dialog)
    if not m:
        say("CANCEL", "dialog tidak menyebut tanggal — tidak dikonfirmasi")
        return False
    say("CANCEL", f"berakhir {m.group(1)} · konfirmasi")
    await page.locator('button:has-text("Cancel subscription")').last.click(timeout=10000)
    await asyncio.sleep(6)

    after = await body(page)
    ok = "Canceled" in after and "will end on" in after
    say("CANCEL", "terverifikasi: Canceled + tanggal berakhir" if ok
        else "status 'Canceled' belum terbaca")
    return ok


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    email = sys.argv[1]
    layers = [1, 2, 3]
    dry = "--dry" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--layers" and i + 1 < len(sys.argv):
            layers = [int(x) for x in sys.argv[i + 1].split(",")]
        if a == "--from" and i + 1 < len(sys.argv):
            start = max(1, min(3, int(sys.argv[i + 1])))
            layers = [n for n in (1, 2, 3) if n >= start]

    settings.ensure_dirs()
    await db.migrate()

    creds = await vault.account_creds(email)
    if not creds:
        print(f"akun tidak ada di database: {email}", flush=True)
        return 2
    acc_id, password, secret = creds
    if not password:
        print(f"password kosong di vault untuk {email} ({acc_id}) — impor ulang akunnya",
              flush=True)
        return 2
    print(f"akun {email} · {acc_id} · password {len(password)} char"
          f" · totp {'ada' if secret else 'tidak ada'}", flush=True)

    async with async_playwright() as p:
        ctx = await browser.launch(p, settings.profiles_dir / f"profile_{acc_id}", headless=False)
        await ctx.add_init_script(await checkout.init_script())
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        await nav.goto(page, egress.ECHO)
        seen = await egress.observe(page)
        waited = 0
        while not egress.matches(WANT, seen["country"]) and waited < 120:
            say("EGRESS", f"{seen['country']} ≠ {WANT} · tunggu {waited}/120s")
            await asyncio.sleep(10)
            waited += 10
            await nav.goto(page, egress.ECHO)
            seen = await egress.observe(page)
        say("EGRESS", f"{seen['ip']} · {seen['country']} · {seen['org']}")
        if not egress.matches(WANT, seen["country"]):
            await ctx.close()
            return 1

        if not await login.ensure_signed_in(page, email, password, secret):
            say("LOGIN", "GAGAL — selesaikan manual di jendela Chrome")
            await asyncio.sleep(300)
            await ctx.close()
            return 1
        say("LOGIN", "siap")

        finished = await run_layers(page, email, acc_id, layers, dry)
        if not finished:
            say("STOP", "layer belum tuntas — cancel tidak dijalankan")
            await page.screenshot(path=str(settings.artifacts_dir / f"flow_stop_{int(time.time())}.png"))
            await asyncio.sleep(10)
            await ctx.close()
            return 1

        if dry:
            say("DRY", "berhenti sebelum commit apa pun")
            await asyncio.sleep(10)
            await ctx.close()
            return 0

        done = await cancel(page)
        if done:
            await release_card(page, acc_id)
        await db.execute("UPDATE accounts SET status = ? WHERE email = ?",
                         ("subscribed" if done else "failed", email))
        await page.screenshot(path=str(settings.artifacts_dir / f"flow_final_{int(time.time())}.png"))
        say("SELESAI", "cancel terverifikasi" if done else "cancel belum terverifikasi")
        await asyncio.sleep(10)
        await ctx.close()
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
