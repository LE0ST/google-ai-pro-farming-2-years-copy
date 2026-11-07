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


def test_browser_resolution_shape() -> None:
    choice = browser.resolve_browser()
    assert isinstance(choice, dict)
    assert set(choice) <= {"channel", "executable_path"}
    assert all(isinstance(v, str) and v for v in choice.values())
    assert isinstance(browser.has_display(), bool)
    assert "headless" in browser.describe() or "headed" in browser.describe()


def test_lock_covers_linux() -> None:
    """A darwin-only lock is what broke Linux installs; keep both platforms in it."""
    lock = Path(__file__).resolve().parent.parent / "requirements.lock"
    text = lock.read_text(encoding="utf-8")
    assert "--hash=sha256:" in text
    tags = re.findall(r"^# files: (.+)$", text, re.MULTILINE)
    assert len(tags) >= 20, f"{len(tags)} package tags"
    combined = " ".join(tags)
    assert "linux=" in combined, "lock pinned no linux wheels"
    assert "macos=" in combined, "lock pinned no macOS wheels"
    assert "win" not in combined, "windows artifacts should not be pinned"


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
