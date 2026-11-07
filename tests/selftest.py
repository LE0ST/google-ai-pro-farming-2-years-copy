"""Self-test, stdlib only. Run: .venv/bin/python tests/selftest.py"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, secrets, totp  # noqa: E402
from app.models import AccountCreate  # noqa: E402

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
