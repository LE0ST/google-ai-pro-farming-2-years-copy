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
    a = AccountCreate(email="Some.One@Gmail.COM", password="x")
    assert a.email == "some.one@gmail.com"
    for bad in ("nope", "a@b", "@b.com", "a b@c.com"):
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
