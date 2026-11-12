"""Self-test, stdlib only. Run: .venv/bin/python tests/selftest.py"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Jangan sentuh data asli: uji jalan di direktori sementara.
_TMP = Path(tempfile.mkdtemp(prefix="gai-selftest-"))
os.environ["GAI_DATA_DIR"] = str(_TMP)

from app import db, secrets, totp  # noqa: E402
from app.config import _default_data_dir, settings  # noqa: E402
from app.models import AccountCreate  # noqa: E402
from app.runner import browser  # noqa: E402

# RFC 6238 Appendix B: secret "12345678901234567890" (ASCII), SHA-1, 8 digits.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_totp_rfc_vectors() -> None:
    cases = {59: "287082", 1111111109: "081804", 1234567890: "005924", 2000000000: "279037"}
    for at, expected in cases.items():
        assert totp.code(RFC_SECRET, at=at) == expected, (at, totp.code(RFC_SECRET, at=at))


def test_totp_shapes() -> None:
    assert totp.code("") == ""
    assert totp.code("not-base32!!") == ""
    assert totp.code("jbsw y3dp ehpk 3pxp") != ""
    assert 1 <= totp.seconds_left() <= 30
    assert totp.wait_hint(at=0) == 0


def test_email_normalisation() -> None:
    a = AccountCreate(email="demo.user@example.invalid", password="x")
    assert a.email == "demo.user@example.invalid"
    for bad in ("nope", "a@b", "@b.com", "a b@example.invalid"):
        try:
            AccountCreate(email=bad, password="x")
        except Exception:
            continue
        raise AssertionError(f"accepted bad email {bad!r}")


def test_secrets_roundtrip() -> None:
    ref = "selftest:roundtrip"
    secrets.delete(ref)
    assert secrets.get(ref) == ""
    secrets.put(ref, "p@ss-with-symbols-9=X")
    assert secrets.get(ref) == "p@ss-with-symbols-9=X"
    secrets.put(ref, "overwritten")
    assert secrets.get(ref) == "overwritten"
    secrets.delete(ref)
    assert secrets.get(ref) == ""


def test_db_migrate() -> None:
    async def run() -> None:
        await db.migrate()
        await db.migrate()  # idempotent
        row = await db.fetch_one("SELECT COUNT(*) AS n FROM accounts")
        assert row is not None and row["n"] == 0
        cols = {r["name"] for r in await db.fetch_all("PRAGMA table_info(accounts)")}
        assert {"email", "secret_ref", "totp_ref", "step", "expiry_date"} <= cols

    asyncio.run(run())


def test_secret_backend_reported() -> None:
    assert secrets.backend() in {"security", "secret-tool", "file"}
    assert secrets.describe()


def test_file_backend_roundtrip_and_mode() -> None:
    """The fallback backend must work and must never be group/world readable."""
    saved = secrets._backend
    try:
        secrets._backend = "file"
        ref = "selftest:file"
        secrets.put(ref, "nilai-rahasia")
        assert secrets.get(ref) == "nilai-rahasia"
        assert secrets.exists(ref)
        path = settings.data_dir / "vault.json"
        assert path.exists(), path
        if sys.platform != "win32":
            # Windows has no POSIX modes; the profile ACL is the protection.
            assert path.stat().st_mode & 0o077 == 0, oct(path.stat().st_mode)
        secrets.delete(ref)
        assert secrets.get(ref) == ""
    finally:
        secrets._backend = saved


def test_data_dir_follows_platform() -> None:
    path = _default_data_dir()
    assert path.name == "GaiConsole"
    if sys.platform == "darwin":
        assert "Library/Application Support" in path.as_posix()
    elif sys.platform == "win32":
        if not os.environ.get("LOCALAPPDATA"):
            assert "AppData/Local" in path.as_posix(), path
    elif not os.environ.get("XDG_DATA_HOME"):
        assert path.as_posix().endswith(".local/share/GaiConsole"), path


def test_headless_resolution() -> None:
    saved_mode, saved_display = settings.headless, os.environ.get("DISPLAY")
    try:
        settings.headless = "1"
        assert browser.resolve_headless(False) is True
        settings.headless = "0"
        assert browser.resolve_headless(None) is False
        settings.headless = "auto"
        if sys.platform.startswith("linux"):
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            # headed was asked for, no display exists: headless is the only option
            assert browser.resolve_headless(False) is True
    finally:
        settings.headless = saved_mode
        if saved_display is not None:
            os.environ["DISPLAY"] = saved_display


def test_discover_today_resolves_zone() -> None:
    """discover memuat ZoneInfo("America/Los_Angeles") saat diimpor.

    Windows tidak punya database zona waktu IANA bawaan — tanpa paket
    `tzdata` (dipin di pyproject + lock) impor ini meledak di sana dan
    setiap run akun langsung mati.
    """
    import tzdata

    from app.sites.google_one import discover

    # Di Linux discover.today() tetap jalan dari /usr/share/zoneinfo walau
    # paketnya hilang; pin keberadaan paketnya supaya venv tanpa tzdata
    # gagal di sini, bukan nanti di Windows.
    assert tzdata.__file__, "paket tzdata tidak terpasang di venv ini"
    assert discover.today().isoformat()


def _patched_win32(env: dict[str, str]):
    """Patch sys.platform + env Windows untuk satu blok, pulihkan sesudahnya."""
    saved_platform = sys.platform
    saved_env = {k: os.environ.get(k) for k in env}
    sys.platform = "win32"
    os.environ.update(env)
    return saved_platform, saved_env


def _restore_win32(saved: tuple[str, dict[str, str | None]]) -> None:
    platform, env = saved
    sys.platform = platform
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_win32_data_dir_simulation() -> None:
    """Di host mana pun: platform win32 + LOCALAPPDATA kustom -> data dir ikut."""
    saved = _patched_win32({"LOCALAPPDATA": r"D:\Profil\Local"})
    try:
        path = _default_data_dir()
        assert path.name == "GaiConsole"
        assert str(path).replace("\\", "/").endswith("Profil/Local/GaiConsole"), path
    finally:
        _restore_win32(saved)


def test_win32_browser_resolution_simulation() -> None:
    """Di host mana pun: chrome.exe di pohon instalasi Windows palsu terpilih."""
    root = Path(tempfile.mkdtemp(prefix="gai-win32-"))
    chrome_exe = root / "Program Files" / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome_exe.parent.mkdir(parents=True)
    chrome_exe.write_bytes(b"")
    chrome_exe.chmod(0o755)
    saved = _patched_win32(
        {
            "PROGRAMFILES": str(root / "Program Files"),
            "PROGRAMFILES(X86)": str(root / "Program Files (x86)"),
            "LOCALAPPDATA": str(root / "Local"),
        }
    )
    # shutil.which membaca sys.platform dan masuk jalur win32 yang butuh
    # modul khusus Windows; di host non-Windows stub saja pencarian PATH-nya
    # (yang diuji di sini penemuan dari pohon instalasi, bukan PATH).
    saved_which = browser.shutil.which
    browser.shutil.which = lambda _name: None
    try:
        choice = browser.resolve_browser()
        assert choice == {"executable_path": str(chrome_exe)}, choice
        assert browser.has_display() is True
    finally:
        browser.shutil.which = saved_which
        _restore_win32(saved)


def test_win_browser_paths_shape() -> None:
    """Windows discovery is pure path building — shape-check it on any host."""
    chrome, chromium = browser._win_browser_paths()
    assert chrome and chromium
    assert all(p.endswith("chrome.exe") for p in chrome + chromium)
    assert all("Chrome" in p for p in chrome)
    assert all("Chromium" in p for p in chromium)


def test_browser_resolution_shape() -> None:
    choice = browser.resolve_browser()
    assert isinstance(choice, dict)
    assert set(choice) <= {"channel", "executable_path"}
    assert all(isinstance(v, str) and v for v in choice.values())
    assert isinstance(browser.has_display(), bool)
    assert "headless" in browser.describe() or "headed" in browser.describe()


def test_guard_judge_today_scoping() -> None:
    """Money guard dinilai dari tagihan *hari ini*, bukan nol di mana saja."""
    from app.sites.google_one import guard

    # (a) trial sah: hari ini $0.00, harga masa depan disebut -> lolos
    v = guard.judge("Start free trial · Due today $0.00 · Then $19.99/month starting Dec 8, 2026")
    assert v.safe, v
    # (b) tagihan hari ini bukan nol, walau ada $0.00 (pajak) -> gagal
    v = guard.judge("Subscribe · $199.99 due today + $0.00 tax")
    assert not v.safe, v
    # (c) teks kosong / tak terbaca -> gagal (fail closed)
    assert not guard.judge("").safe
    assert not guard.judge("   ").safe
    assert not guard.judge("dialog tanpa nominal apa pun").safe
    # (d) tagihan hari ini bukan nol tanpa nol sama sekali -> gagal
    assert not guard.judge("Due today $49.99").safe
    # nominal bukan nol tanpa label hari ini & tanpa konteks masa depan -> gagal
    assert not guard.judge("Total $199.99 · tax $0.00").safe
    # hanya harga masa depan + nol -> lolos
    assert guard.judge("Due $0.00 · renews at $19.99/month after the trial").safe
    # negasi bukan tagihan: "won't be charged today" tidak menambatkan nominal
    v = guard.judge("You won't be charged today · $0.00 due at trial end · then $19.99/month")
    assert v.safe, v
    # dialog "Upcoming charges" layer 2-4: tidak ada tambatan hari ini dan
    # *semua* nominal bukan-nol terikat tanggal mulai masa depan -> lolos
    v = guard.judge(
        "Google AI Pro (2 TB) · Upcoming charges · Starting May 8, 2027 $13.99/month"
        " · Starting May 8, 2028 $19.99/month · Subscribe"
    )
    assert v.safe, v
    # dialog berbayar polos (Tier B tanpa penanda mulai): satuan berkala
    # /month & /year serta "renews" bukan penundaan -> gagal
    assert not guard.judge(
        "Google AI Pro (2 TB) · $19.99/month · Billed monthly · Subscribe"
    ).safe
    assert not guard.judge(
        "Subscribe to Google AI Pro · $199.99/year · Auto-renews on Nov 8, 2027"
    ).safe
    # "Today $X": today telanjang menambatkan nominal sesudahnya
    assert not guard.judge("Today $19.99").safe
    v = guard.judge("12-month free trial · Today $0.00 · $19.99/month after trial")
    assert v.safe, v

    # Synthetic combined upcoming-charges dialog regression: rincian komponen tercetak
    # SETELAH total masa depannya — "$31.98" = "$19.99" + "$11.99" — dan
    # tidak ada tagihan hari ini -> lolos lewat aturan rincian komponen.
    l2 = (
        "Google AI Pro (5 TB) + YouTube Premium Individual Add On "
        "Upcoming charges Starting Dec 8, 2026 $11.99/month includes tax of $0.00 "
        "YouTube Premium Individual Add On $11.99 Starting May 8, 2028 $31.98/month "
        "includes tax of $0.00 Google AI Pro (5 TB) $19.99 YouTube Premium "
        "Individual Add On $11.99 Subscribing on Play"
    )
    v = guard.judge(l2)
    assert v.safe, v
    v = guard.judge(l2.replace("May 8, 2028", "Nov 8, 2028"))
    assert v.safe, v
    # total diubah -> jumlah rincian tidak lagi cocok -> gagal
    assert not guard.judge(l2.replace("$31.98", "$30.98")).safe
    # nominal ekstra tak terjelaskan setelah rincian -> gagal
    assert not guard.judge(l2 + " · Activation $5.00").safe
    # aturan rincian tidak menolong dialog berbayar polos yang ditambah
    # rincian karangan: tanpa total masa depan terikat, tetap gagal
    assert not guard.judge(
        "Google AI Pro $19.99 · YouTube Premium $11.99 · Total $31.98 · Billed monthly"
    ).safe


def test_same_origin_guard() -> None:
    """POST lintas origin ditolak; tanpa Origin (curl/API) lolos berbekal token."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.security import require_same_origin

    def req(headers: dict[str, str]):
        return SimpleNamespace(headers=headers)

    async def run() -> None:
        host = {"host": "127.0.0.1:8765"}
        # tanpa Origin/Referer -> lolos
        await require_same_origin(req(host))
        # Origin / Referer se-host dengan header Host -> lolos
        await require_same_origin(req({**host, "origin": "http://127.0.0.1:8765"}))
        await require_same_origin(req({**host, "referer": "http://127.0.0.1:8765/"}))
        # host/port lain, atau Origin "null" (sandboxed iframe) -> 403
        for bad in ("http://127.0.0.1:9999", "https://evil.example", "null"):
            try:
                await require_same_origin(req({**host, "origin": bad}))
            except HTTPException as exc:
                assert exc.status_code == 403, exc.status_code
                continue
            raise AssertionError(f"origin {bad!r} lolos")

    asyncio.run(run())


def test_latest_log_rejects_bad_ids() -> None:
    """acc_id mentah dari URL tidak boleh lolos jadi pola glob."""
    from app.runner import queue

    assert queue.latest_log("../etc/passwd") is None
    assert queue.latest_log("acc_xyz") is None
    assert queue.latest_log("*") is None
    # pola sah tapi belum ada log untuknya -> None juga
    assert queue.latest_log("acc_0123456789ab") is None


def test_vault_card_rotation_and_reimport() -> None:
    """kill_card memutar akun ke kartu aktif; re-impor menyegarkan akun."""
    from app import vault

    async def run() -> None:
        await db.migrate()
        ok, note = await vault.add_card("4242424242424242", "12", "30", "123")
        assert ok, note
        ok, note = await vault.add_card("5555555555554444", "12", "30", "123")
        assert ok, note
        ok, note = await vault.add_account("uji@example.com", "pw-lama", "")
        assert ok, note
        acc = await db.fetch_one("SELECT * FROM accounts WHERE email = 'uji@example.com'")
        assert acc is not None and acc["card_id"], acc
        dead = acc["card_id"]
        others = [c["id"] for c in await db.fetch_all("SELECT id FROM cards") if c["id"] != dead]
        assert len(others) == 1

        await vault.kill_card(dead, dead=True)
        acc = await db.fetch_one("SELECT * FROM accounts WHERE email = 'uji@example.com'")
        assert acc["card_id"] == others[0], acc["card_id"]

        # hidupkan lagi: backfill tidak mencuri akun yang sudah punya kartu
        await vault.kill_card(dead, dead=False)
        acc = await db.fetch_one("SELECT * FROM accounts WHERE email = 'uji@example.com'")
        assert acc["card_id"] == others[0], acc["card_id"]

        # re-impor: seed yang dulu kosong kini tertulis, failed -> pending,
        # penugasan kartu tetap valid
        await db.execute("UPDATE accounts SET status = 'failed' WHERE id = ?", (acc["id"],))
        ok, note = await vault.add_account("uji@example.com", "pw-baru", "JBSWY3DPEHPK3PXP")
        assert not ok and note.endswith("diperbarui"), note
        acc = await db.fetch_one("SELECT * FROM accounts WHERE email = 'uji@example.com'")
        assert acc["status"] == "pending", acc["status"]
        assert acc["totp_ref"] == f"{acc['id']}:totp", acc["totp_ref"]
        assert acc["card_id"] == others[0], acc["card_id"]
        assert vault.secret_get(acc["totp_ref"])

    asyncio.run(run())


def test_discovered_zero_months_rewinds_stale_resume_cursor() -> None:
    """A stale step must not skip layer 1 when discovery finds no subscription."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from tasks import subscribe_flow

    state = {"step": 2}
    opened: list[int] = []

    async def execute(sql: str, params: tuple = ()) -> None:
        if "UPDATE accounts SET step = ?, status = 'running'" in sql:
            state["step"] = params[0]

    async def open_dialog(_page: object, layer: int) -> str:
        opened.append(layer)
        return ""  # Synthetic failure before any checkout action.

    async def run() -> bool:
        from app.sites.google_one import discover
        observed = discover.SubscriptionObservation(
            discover.SubscriptionStatus.NO_SUBSCRIPTION,
            months_remaining=0,
            reason="mocked empty state",
        )
        reconciliation = discover.reconcile_progress(observed, 2)
        with (
            patch.object(discover, "diagnose_page", new=AsyncMock(return_value=(observed, reconciliation))),
            patch.object(subscribe_flow, "open_dialog", new=open_dialog),
            patch.object(subscribe_flow.db, "execute", new=AsyncMock(side_effect=execute)),
        ):
            page = SimpleNamespace(screenshot=AsyncMock())
            return await subscribe_flow.run_layers(
                page,
                "demo@example.invalid",
                "acc_0123456789ab",
                [2, 3],
                dry=True,
                resume_from_state=True,
            )

    finished = asyncio.run(run())
    assert not finished
    assert opened == [1], f"stale step selected layers {opened!r} instead of layer 1"
    assert state["step"] == 2, f"failed layer attempt changed persisted step: {state!r}"


def test_resume_layers_follow_discovered_stage_boundaries() -> None:
    """Discovery rewinds stale cursors and retains only not-yet-complete layers."""
    from tasks.subscribe_flow import reconcile_resume_layers

    assert reconcile_resume_layers(1) == [1]  # initial offer only
    assert reconcile_resume_layers(2) == []   # active-plan changes are unsupported
    assert reconcile_resume_layers(3) == []
    assert reconcile_resume_layers(4) == []


def test_subscription_observation_reconciliation_boundaries() -> None:
    """Only explicit visible evidence can reconcile the local cursor."""
    from datetime import date
    from app.sites.google_one import discover

    today = date(2026, 1, 15)
    none = discover.observe_subscription_text("You don't have any subscriptions", today_date=today)
    assert none.status == discover.SubscriptionStatus.NO_SUBSCRIPTION
    assert discover.reconcile_progress(none, 1, today_date=today).status == discover.ReconciliationStatus.MATCH

    six_months = discover.observe_subscription_text("Active · Renews on Jul 15, 2026 · $19.99/month",
                                                    today_date=today)
    assert six_months.status == discover.SubscriptionStatus.ACTIVE_KNOWN
    assert six_months.months_remaining == 6
    assert six_months.price == "$19.99"
    six_months_reconciliation = discover.reconcile_progress(six_months, 1, today_date=today)
    assert six_months_reconciliation.status == discover.ReconciliationStatus.MISMATCH
    assert six_months_reconciliation.detected_layer is None

    twelve_months = discover.observe_subscription_text("Active · Renews on Jan 15, 2027 · $19.99/month",
                                                       today_date=today)
    assert twelve_months.months_remaining == 12
    assert discover.reconcile_progress(twelve_months, 2, today_date=today).status == discover.ReconciliationStatus.MATCH
    mismatch = discover.reconcile_progress(twelve_months, 1, today_date=today)
    assert mismatch.status == discover.ReconciliationStatus.MISMATCH
    assert mismatch.detected_layer == 2

    unknown_date = discover.observe_subscription_text("Subscription status: Active · $19.99/month",
                                                      today_date=today)
    assert unknown_date.status == discover.SubscriptionStatus.ACTIVE_UNKNOWN
    assert discover.reconcile_progress(unknown_date, 2, today_date=today).status == discover.ReconciliationStatus.INDETERMINATE

    inconsistent = discover.observe_subscription_text(
        "No subscriptions · Active · Renews on Jan 15, 2027", today_date=today
    )
    assert inconsistent.status == discover.SubscriptionStatus.INCONSISTENT
    assert discover.reconcile_progress(inconsistent, 2, today_date=today).status == discover.ReconciliationStatus.INDETERMINATE

    ambiguous_dates = discover.observe_subscription_text(
        "Active · Renews on Jan 15, 2027 · Ends on Jan 15, 2028", today_date=today
    )
    assert ambiguous_dates.status == discover.SubscriptionStatus.INCONSISTENT

    canceled = discover.observe_subscription_text("Canceled · Ends on Jan 15, 2027", today_date=today)
    assert canceled.status == discover.SubscriptionStatus.INCONSISTENT

    failed = discover.observe_subscription_text("", today_date=today)
    assert failed.status == discover.SubscriptionStatus.DETECTION_FAILED
    assert discover.reconcile_progress(failed, 1, today_date=today).status == discover.ReconciliationStatus.INDETERMINATE


def test_read_only_diagnosis_uses_visible_page_only() -> None:
    """The diagnostic navigates and reads text but never clicks or submits controls."""
    from app.sites.google_one import discover

    class FakePage:
        url = "about:blank"

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def goto(self, url: str, **kwargs: object) -> None:
            self.calls.append("goto")
            assert url == discover.SUBSCRIPTIONS_URL
            self.url = url

        async def inner_text(self, selector: str, **kwargs: object) -> str:
            self.calls.append("inner_text")
            assert selector == "body"
            return "You don't have any subscriptions"

    page = FakePage()
    observation, reconciliation = asyncio.run(discover.diagnose_page(page, 1))
    assert observation.status == discover.SubscriptionStatus.NO_SUBSCRIPTION
    assert reconciliation.status == discover.ReconciliationStatus.MATCH
    assert page.calls == ["goto", "inner_text"], page.calls


def test_diagnosis_navigation_failure_is_explicit_and_capturable() -> None:
    """Timeouts remain detection failures and optional evidence is caller-directed."""
    from app.sites.google_one import discover

    class TimeoutPage:
        url = "https://play.google.com/store/account/subscriptions?private=value"

        def __init__(self) -> None:
            self.screenshot_path: str | None = None

        async def goto(self, _url: str, **_kwargs: object) -> None:
            raise TimeoutError("navigation timeout")

        def locator(self, selector: str) -> str:
            assert selector == "body"
            return selector

        async def screenshot(self, *, path: str, full_page: bool, mask: list, timeout: int) -> None:
            assert mask == ["body"]
            self.screenshot_path = path

    page = TimeoutPage()
    observation, reconciliation = asyncio.run(
        discover.diagnose_page(page, 1, screenshot_path="diagnostic-timeout.png")
    )
    assert observation.status == discover.SubscriptionStatus.DETECTION_FAILED
    assert "TimeoutError" in observation.reason
    assert "private=value" not in observation.reason
    assert reconciliation.status == discover.ReconciliationStatus.INDETERMINATE
    assert page.screenshot_path == "diagnostic-timeout.png"


def test_subscription_detector_localized_and_ambiguous_states() -> None:
    from app.sites.google_one import discover

    for text in ("No active subscriptions", "You have no subscriptions", "You have no active subscriptions",
                 "No tienes suscripciones activas", "No tienes ninguna suscripción", "No hay suscripciones"):
        assert discover.observe_subscription_text(text).status == discover.SubscriptionStatus.NO_SUBSCRIPTION, text
    assert discover.observe_subscription_text("Discover subscriptions").status == discover.SubscriptionStatus.DETECTION_FAILED
    assert discover.observe_subscription_text("Google Play · Subscriptions · Discover subscriptions").status == discover.SubscriptionStatus.DETECTION_FAILED
    assert discover.observe_subscription_text("Discover subscriptions · No subscriptions").status == discover.SubscriptionStatus.NO_SUBSCRIPTION
    assert discover.observe_subscription_text("Discover subscriptions · Active subscription").status == discover.SubscriptionStatus.ACTIVE_UNKNOWN
    assert discover.observe_subscription_text("No subscriptions · Active subscription").status == discover.SubscriptionStatus.INCONSISTENT


def test_diagnosis_retries_delayed_render_and_preserves_cursor() -> None:
    from unittest.mock import AsyncMock, patch
    from app.sites.google_one import discover
    from app import db

    class DelayedPage:
        url = "https://play.google.com/store/account/subscriptions"

        def __init__(self) -> None:
            self.reads = 0

        async def goto(self, *args: object, **kwargs: object) -> None:
            pass

        async def inner_text(self, *args: object, **kwargs: object) -> str:
            self.reads += 1
            return ("", "Loading", "No tienes suscripciones activas")[min(self.reads - 1, 2)]

    page = DelayedPage()
    with patch.object(db, "execute", new=AsyncMock()) as execute:
        observation, reconciliation = asyncio.run(
            discover.diagnose_page(page, 2, timeout_ms=200, poll_interval=0.01)
        )
        execute.assert_not_awaited()
    assert page.reads == 3
    assert observation.status == discover.SubscriptionStatus.NO_SUBSCRIPTION
    assert reconciliation.status == discover.ReconciliationStatus.MISMATCH


def test_diagnosis_unauthenticated_loading_unknown_and_navigation_failure() -> None:
    from unittest.mock import AsyncMock, patch
    from app.sites.google_one import discover
    from app import db

    class FakePage:
        def __init__(self, text: str, *, url: str = "https://play.google.com/store/account/subscriptions",
                     fail: bool = False) -> None:
            self.url, self.text, self.fail = url, text, fail

        async def goto(self, *args: object, **kwargs: object) -> None:
            if self.fail:
                raise OSError("network failure")

        async def inner_text(self, *args: object, **kwargs: object) -> str:
            return self.text

    cases = ((FakePage("Use your Google Account", url="https://accounts.google.com/signin"), "login_required"),
             (FakePage("Loading"), "loading"), (FakePage("Discover subscriptions"), "unrecognized_content"),
             (FakePage("", fail=True), "navigation"))
    with patch.object(db, "execute", new=AsyncMock()) as execute:
        for page, reason in cases:
            observation, reconciliation = asyncio.run(
                discover.diagnose_page(page, 2, timeout_ms=35, poll_interval=0.01)
            )
            assert observation.status == discover.SubscriptionStatus.DETECTION_FAILED
            assert reason in observation.reason, observation.reason
            assert reconciliation.status == discover.ReconciliationStatus.INDETERMINATE
        execute.assert_not_awaited()


def test_diagnosis_active_and_hanging_navigation() -> None:
    from app.sites.google_one import discover

    class Page:
        url = "https://play.google.com/store/account/subscriptions"

        def __init__(self, hanging: bool) -> None:
            self.hanging = hanging

        async def goto(self, *args: object, **kwargs: object) -> None:
            if self.hanging:
                await asyncio.sleep(1)

        async def inner_text(self, *args: object, **kwargs: object) -> str:
            return "Subscription active · Renews on Jan 15, 2028"

    observation, _ = asyncio.run(discover.diagnose_page(Page(False), 2, timeout_ms=100))
    assert observation.status == discover.SubscriptionStatus.ACTIVE_KNOWN
    timed_out, reconciliation = asyncio.run(discover.diagnose_page(Page(True), 2, timeout_ms=30))
    assert timed_out.status == discover.SubscriptionStatus.DETECTION_FAILED
    assert "timeout" in timed_out.reason
    assert reconciliation.status == discover.ReconciliationStatus.INDETERMINATE


def test_failed_commit_does_not_advance_persisted_step() -> None:
    """An uncertain mocked submission preserves the cursor and blocks automatic retry."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from tasks import subscribe_flow

    state = {"step": 0}
    expected = subscribe_flow.add_months(subscribe_flow.today(), 12)
    displayed = expected.strftime("%b %d, %Y")

    async def execute(sql: str, params: tuple = ()) -> None:
        if "UPDATE accounts SET step = ?, status = 'running'" in sql:
            state["step"] = params[0]

    async def open_dialog(_page: object, _layer: int) -> str:
        return f"Due today $0.00 · Renews on {displayed}"

    async def run() -> bool:
        from app.sites.google_one import discover
        observed = discover.SubscriptionObservation(
            discover.SubscriptionStatus.NO_SUBSCRIPTION,
            months_remaining=0,
            reason="mocked empty state",
        )
        reconciliation = discover.reconcile_progress(observed, 1)
        with (
            patch.object(discover, "diagnose_page", new=AsyncMock(return_value=(observed, reconciliation))),
            patch.object(subscribe_flow, "open_dialog", new=open_dialog),
            patch.object(subscribe_flow, "commit", new=AsyncMock(return_value=False)),
            patch.object(subscribe_flow.guard, "judge", return_value=SimpleNamespace(safe=True, reason="mock")),
            patch.object(subscribe_flow.db, "execute", new=AsyncMock(side_effect=execute)),
        ):
            page = SimpleNamespace(screenshot=AsyncMock())
            return await subscribe_flow.run_layers(page, "demo@example.invalid", "acc_0123456789ac", [1], dry=False)

    assert asyncio.run(run()) is subscribe_flow.RunOutcome.NEEDS_VERIFICATION
    assert state["step"] == 0, state


def test_commit_confirmation_requires_independent_visible_state() -> None:
    """A closed dialog is not itself proof that Google accepted the subscription."""
    from datetime import date
    from unittest.mock import AsyncMock, patch
    from app.sites.google_one import discover
    from tasks import subscribe_flow

    confirmed = discover.SubscriptionObservation(
        discover.SubscriptionStatus.ACTIVE_KNOWN,
        renewal_date=date(2027, 1, 15),
        months_remaining=12,
        reason="mocked visible active state",
    )
    confirmed_reconciliation = discover.reconcile_progress(confirmed, 2, today_date=date(2026, 1, 15))
    at_24_months = discover.SubscriptionObservation(
        discover.SubscriptionStatus.ACTIVE_KNOWN,
        renewal_date=date(2028, 1, 15),
        months_remaining=24,
        reason="mocked 24-month state",
    )
    at_24_reconciliation = discover.reconcile_progress(at_24_months, 4, today_date=date(2026, 1, 15))
    at_30_months = discover.SubscriptionObservation(
        discover.SubscriptionStatus.ACTIVE_KNOWN,
        renewal_date=date(2028, 7, 15),
        months_remaining=30,
        reason="mocked 30-month state",
    )
    at_30_reconciliation = discover.reconcile_progress(at_30_months, 4, today_date=date(2026, 1, 15))
    page = object()

    async def run() -> tuple[bool, bool, bool, bool]:
        diagnosis = AsyncMock(side_effect=[
            (confirmed, confirmed_reconciliation),
            (confirmed, confirmed_reconciliation),
            (at_24_months, at_24_reconciliation),
            (at_30_months, at_30_reconciliation),
        ])
        with patch.object(discover, "diagnose_page", new=diagnosis):
            success = await subscribe_flow.verify_committed_layer(page, 1)
            no_evidence = await subscribe_flow.verify_committed_layer(page, 2)
            layer4_without_change = await subscribe_flow.verify_committed_layer(page, 4)
            layer4_confirmed = await subscribe_flow.verify_committed_layer(page, 4)
            return success, no_evidence, layer4_without_change, layer4_confirmed

    assert asyncio.run(run()) == (True, False, False, False)


def test_verified_terminal_term_does_not_retry_another_layer() -> None:
    """A 30-month visible term is terminal and must not trigger another attempt."""
    from datetime import date
    from unittest.mock import AsyncMock, patch
    from app.sites.google_one import discover
    from tasks import subscribe_flow

    today = date(2026, 1, 15)
    renewal = date(2028, 7, 15)
    observed = discover.SubscriptionObservation(
        discover.SubscriptionStatus.ACTIVE_KNOWN,
        renewal_date=renewal,
        months_remaining=30,
        reason="mocked terminal state",
    )
    reconciliation = discover.reconcile_progress(observed, 4, today_date=today)

    async def run() -> tuple[bool, AsyncMock, AsyncMock]:
        with (
            patch.object(discover, "diagnose_page", new=AsyncMock(return_value=(observed, reconciliation))),
            patch.object(subscribe_flow, "open_dialog", new=AsyncMock()) as open_dialog,
            patch.object(subscribe_flow.db, "execute", new=AsyncMock()) as execute,
        ):
            result = await subscribe_flow.run_layers(object(), "demo@example.invalid", "acc_0123456789ab", [1, 2, 3], dry=False)
            return result, open_dialog, execute

    result, open_dialog, execute = asyncio.run(run())
    assert result is subscribe_flow.RunOutcome.NEEDS_VERIFICATION
    open_dialog.assert_not_awaited()
    execute.assert_not_awaited()


def test_indeterminate_reconciliation_never_modifies_cursor() -> None:
    """Any indeterminate classification or out-of-range local_step must stop before writing step."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from app.runner import diagnosis
    from app.sites.google_one import discover
    from tasks import subscribe_flow

    async def run() -> None:
        empty_obs = discover.SubscriptionObservation(
            discover.SubscriptionStatus.NO_SUBSCRIPTION,
            months_remaining=0,
            reason="contextual visible empty state",
        )
        # Out-of-range local_step=99 yields INDETERMINATE even though detected_layer is 1.
        out_of_range = discover.reconcile_progress(empty_obs, 99)
        assert out_of_range.status == discover.ReconciliationStatus.INDETERMINATE
        assert out_of_range.detected_layer == 1

        unknown_active = discover.SubscriptionObservation(
            discover.SubscriptionStatus.ACTIVE_UNKNOWN,
            reason="subscription indicated without a readable renewal date",
        )
        unknown_rec = discover.reconcile_progress(unknown_active, 0)

        for obs, rec, step in ((empty_obs, out_of_range, 99), (unknown_active, unknown_rec, 0)):
            with (
                patch.object(diagnosis, "diagnose_run_page", new=AsyncMock(return_value=(obs, rec))),
                patch.object(subscribe_flow, "open_dialog", new=AsyncMock()) as open_dialog,
                patch.object(subscribe_flow.db, "execute", new=AsyncMock()) as execute,
            ):
                page = SimpleNamespace(screenshot=AsyncMock())
                ok = await subscribe_flow.run_layers(
                    page, "demo@example.invalid", "acc_0123456789ab", [1, 2, 3],
                    dry=True, resume_from_state=True, local_step=step,
                )
                assert ok is subscribe_flow.RunOutcome.NEEDS_VERIFICATION
                open_dialog.assert_not_awaited()
                execute.assert_not_awaited()

    asyncio.run(run())


def test_historical_run_log_distinguished_from_new_worker_execution() -> None:
    """Historical Run logs without current observer hashes must not be confused with current runs."""
    from app.runner import diagnosis, queue

    acc_id = "acc_0123456789ab"
    settings.ensure_dirs()
    for old in settings.artifacts_dir.glob(f"run_{acc_id}_*.log"):
        old.unlink()

    old_log = settings.artifacts_dir / f"run_{acc_id}_1700000000.log"
    old_log.write_text(
        "[12:00:00] [DIAG] stage=subscription-status url=https://play.google.com/store/account/subscriptions "
        "expected=visible subscription state after page navigation; no controls clicked; "
        "result=detection_failed; renewal=unknown; price=unknown; "
        "reconciliation=indeterminate; reason=data_request_unverified\n",
        encoding="utf-8",
    )
    meta_old = queue.run_log_metadata(acc_id)
    assert meta_old["exists"] is True
    assert meta_old["is_historical"] is True
    assert meta_old["has_passive_observer"] is False
    assert meta_old["matches_loaded_detector"] is False
    assert meta_old["last_subscription_status"] == "detection_failed"
    assert meta_old["last_reason"] == "data_request_unverified"

    versions = diagnosis.detector_version()
    new_log = settings.artifacts_dir / f"run_{acc_id}_1700000100.log"
    new_log.write_text(
        f"[12:01:40] DISC   detector_sha256={versions['sha256']} observer_sha256={versions['observer_sha256']}\n"
        "[DIAG] stage=subscription-status url=https://play.google.com/store/account/subscriptions "
        "expected=visible subscription state after page navigation; no controls clicked; "
        "result=no_subscription; renewal=unknown; price=unknown; "
        "reconciliation=match; reason=contextual visible empty state\n"
        '[DISC_NET] {"mode":"passive","service_worker_seen":false,"service_worker_check":"no_registrations_observed"}\n',
        encoding="utf-8",
    )
    try:
        assert queue.latest_log(acc_id) == new_log
        meta_new = queue.run_log_metadata(acc_id)
        assert meta_new["run_id"] == f"run_{acc_id}_1700000100"
        assert meta_new["is_historical"] is True
        assert meta_new["has_passive_observer"] is True
        assert meta_new["matches_loaded_detector"] is True
        assert meta_new["last_subscription_status"] == "no_subscription"
        assert meta_new["last_reconciliation"] == "match"
        assert meta_new["last_reason"] == "contextual visible empty state"
    finally:
        old_log.unlink(missing_ok=True)
        new_log.unlink(missing_ok=True)


def test_composite_subscription_separates_ai_pro_and_youtube_billing() -> None:
    """Bundle/YouTube next payment must never be mistaken for Google AI Pro renewal date."""
    from datetime import date
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from app.runner import diagnosis
    from app.sites.google_one import discover
    from tasks import subscribe_flow

    today = date(2026, 11, 11)

    # 1) Synthetic regression: Google Play bundle with only Next payment: $11.99 on Feb 17, 2027.
    play_bundle_text = (
        "Google AI Pro (5 TB) + YouTube Premium Individual Add On\n"
        "Next payment: $11.99 on Feb 17, 2027"
    )
    bundle_only = discover.observe_subscription_text(play_bundle_text, today_date=today)
    assert bundle_only.status == discover.SubscriptionStatus.ACTIVE_UNKNOWN
    assert bundle_only.renewal_date is None
    assert bundle_only.primary_renewal_date is None
    assert bundle_only.ai_pro_promo_end_date is None
    assert bundle_only.months_remaining is None
    assert bundle_only.price is None
    assert bundle_only.primary_service == "Google AI Pro (5 TB)"
    assert bundle_only.addons == ("YouTube Premium Individual Add On",)
    assert bundle_only.bundle_next_payment_date == date(2027, 2, 17)
    assert bundle_only.bundle_next_payment_price == "$11.99"
    assert bundle_only.addon_renewal_date == date(2027, 2, 17)
    assert bundle_only.addon_price == "$11.99"
    rec_bundle = discover.reconcile_progress(bundle_only, 1, today_date=today)
    assert rec_bundle.status == discover.ReconciliationStatus.INDETERMINATE
    assert rec_bundle.detected_layer is None
    assert "1 months" not in rec_bundle.reason

    # 2) Bundle next payment without $11.99 price still does not infer AI Pro renewal date.
    bundle_no_price = discover.observe_subscription_text(
        "Google AI Pro (5 TB) + YouTube Premium Individual Add On · Next payment on Feb 17, 2027",
        today_date=today,
    )
    assert bundle_no_price.status == discover.SubscriptionStatus.ACTIVE_UNKNOWN
    assert bundle_no_price.renewal_date is None
    assert bundle_no_price.months_remaining is None
    assert bundle_no_price.bundle_next_payment_date == date(2027, 2, 17)

    # 3) Confirmed second-account evidence on Google One ("Plan actual"):
    #    - Google AI Pro (5 TB): USD 0/mes durante 12 meses; después USD 19.99/mes.
    #    - YouTube Premium Individual: USD 0/mes durante 1 mes; después USD 11.99/mes.
    google_one_plan_actual = (
        "Plan actual\n"
        "Google AI Pro (5 TB): USD 0/mes durante 12 meses; después USD 19.99/mes.\n"
        "YouTube Premium Individual: USD 0/mes durante 1 mes; después USD 11.99/mes."
    )
    one_obs = discover.observe_subscription_text(google_one_plan_actual, today_date=today)
    assert one_obs.status == discover.SubscriptionStatus.ACTIVE_DURATION
    assert one_obs.renewal_date is None
    assert one_obs.months_remaining is None
    assert one_obs.primary_service == "Google AI Pro (5 TB)"
    assert one_obs.addons == ("YouTube Premium Individual",)
    assert one_obs.ai_pro_promo_months == 12
    assert one_obs.primary_price == "USD 19.99"
    assert one_obs.addon_promo_months == 1
    assert one_obs.addon_price == "USD 11.99"

    combined_confirmed = discover.contrast_sources(bundle_only, one_obs)
    assert combined_confirmed.status == discover.SubscriptionStatus.ACTIVE_DURATION
    assert combined_confirmed.renewal_date is None
    assert combined_confirmed.months_remaining is None
    assert combined_confirmed.price == "USD 19.99"
    assert combined_confirmed.bundle_next_payment_date == date(2027, 2, 17)
    assert combined_confirmed.bundle_next_payment_price == "$11.99"
    assert combined_confirmed.ai_pro_promo_months == 12
    assert combined_confirmed.addon_promo_months == 1
    assert combined_confirmed.addon_renewal_date == date(2027, 2, 17)
    rec_confirmed = discover.reconcile_progress(combined_confirmed, 1, today_date=today)
    assert rec_confirmed.status == discover.ReconciliationStatus.INDETERMINATE
    assert rec_confirmed.local_step == 1
    assert rec_confirmed.detected_layer is None

    # 4) A next-payment date is not automatically a renewal date.
    ai_pro_only = discover.observe_subscription_text(
        "Google AI Pro (5 TB)\nNext payment: $19.99 on May 21, 2028",
        today_date=today,
    )
    assert ai_pro_only.status == discover.SubscriptionStatus.ACTIVE_UNKNOWN
    assert ai_pro_only.renewal_date is None
    assert ai_pro_only.primary_renewal_date is None
    assert ai_pro_only.details.primary_next_payment_date == date(2028, 5, 21)
    assert ai_pro_only.months_remaining is None
    assert ai_pro_only.price == "$19.99"
    assert ai_pro_only.addons == ()
    assert ai_pro_only.bundle_next_payment_date is None

    # 5) Promo end and an add-on payment do not establish the AI Pro renewal date.
    two_dates_text = (
        "Google AI Pro (5 TB) + YouTube Premium Individual Add On\n"
        "Google AI Pro (5 TB): USD 0/mes durante 12 meses (vence el May 21, 2028); después USD 19.99/mes\n"
        "YouTube Premium Individual Add On: Next payment $11.99 on Feb 17, 2027"
    )
    two_dates = discover.observe_subscription_text(two_dates_text, today_date=today)
    assert two_dates.status == discover.SubscriptionStatus.ACTIVE_DURATION
    assert two_dates.renewal_date is None
    assert two_dates.primary_renewal_date is None
    assert two_dates.ai_pro_promo_end_date == date(2028, 5, 21)
    assert two_dates.ai_pro_promo_months == 12
    assert two_dates.months_remaining is None
    assert two_dates.price == "USD 19.99"
    assert two_dates.addon_renewal_date == date(2027, 2, 17)
    assert two_dates.bundle_next_payment_date == date(2027, 2, 17)
    assert two_dates.bundle_next_payment_price == "$11.99"
    assert discover.reconcile_progress(two_dates, 2, today_date=today).status == discover.ReconciliationStatus.INDETERMINATE

    # 6) Canceled composite subscription.
    canceled_bundle = discover.observe_subscription_text(
        "Google AI Pro (5 TB) + YouTube Premium Individual Add On · Canceled · Ends on Feb 17, 2027",
        today_date=today,
    )
    assert canceled_bundle.status == discover.SubscriptionStatus.INCONSISTENT
    assert "canceled" in canceled_bundle.reason

    # 7) Contradictory AI Pro dates.
    contradictory_ai_pro = discover.observe_subscription_text(
        "Google AI Pro (5 TB): Renews on May 21, 2028 · Google AI Pro (5 TB): Renews on May 11, 2028",
        today_date=today,
    )
    assert contradictory_ai_pro.status == discover.SubscriptionStatus.INCONSISTENT

    # 8) Unknown AI Pro date never becomes 0 months, 1 month, or a completed layer, and preserves step=1.
    async def run_preserves_step() -> None:
        persisted = {"step": 1}
        with (
            patch.object(diagnosis, "diagnose_run_page", new=AsyncMock(return_value=(combined_confirmed, rec_confirmed))),
            patch.object(subscribe_flow, "open_dialog", new=AsyncMock()) as open_dialog,
            patch.object(subscribe_flow.db, "execute", new=AsyncMock()) as execute,
        ):
            page = SimpleNamespace(screenshot=AsyncMock())
            ok = await subscribe_flow.run_layers(
                page, "demo@example.invalid", "acc_0123456789ab", [1, 2, 3],
                dry=True, resume_from_state=True, local_step=persisted["step"],
            )
            assert ok is subscribe_flow.RunOutcome.ACTIVE_NEEDS_VERIFICATION
            assert persisted["step"] == 1
            open_dialog.assert_not_awaited()
            execute.assert_not_awaited()

    asyncio.run(run_preserves_step())


def test_lock_covers_platforms() -> None:
    """A darwin-only lock is what broke Linux installs; keep all platforms in it."""
    lock = Path(__file__).resolve().parent.parent / "requirements.lock"
    text = lock.read_text(encoding="utf-8")
    assert "--hash=sha256:" in text
    tags = re.findall(r"^# files: (.+)$", text, re.MULTILINE)
    assert len(tags) >= 20, f"{len(tags)} package tags"
    combined = " ".join(tags)
    assert "linux=" in combined, "lock pinned no linux wheels"
    assert "macos=" in combined, "lock pinned no macOS wheels"
    assert "windows=" in combined, "lock pinned no windows wheels"


def main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("---", "all passed" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
