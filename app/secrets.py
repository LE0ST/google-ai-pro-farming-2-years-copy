"""Secret storage: macOS Keychain, Linux Secret Service, file as last resort.

Windows has no OS keyring CLI to shell out to, so `auto` lands on the file
backend there — ``vault.json`` inside ``%LOCALAPPDATA%\\GaiConsole``. POSIX
modes do not exist on Windows; protection comes from the user profile's ACL
(only the owning user, plus admins, can read their own LocalAppData). Same
weaker-than-a-keyring caveat as any file-backend host, and ``describe()``
says so instead of claiming 0600.

One API, three backends — chosen once per process:

* ``security`` — Apple's own CLI, ships with macOS. The value goes in over
  stdin (it prompts twice for confirmation), so it never appears in a process
  argument list.
* ``secret-tool`` — libsecret's CLI on Linux. It talks to gnome-keyring or
  KWallet over D-Bus; the secret goes in over stdin, only attributes are on
  argv. Same guarantee as ``security``.
* ``file`` — a 0600 JSON file inside the 0700 data directory. Used when no
  keyring daemon is reachable: headless Ubuntu server, no session bus, a
  container, a bare SSH session. It is **weaker** than a keyring — any process
  running as the same user can read it — but it is the difference between the
  console running and the console refusing to start. ``describe()`` reports the
  live backend so the log and the UI never imply more protection than exists.

Values containing newlines are not supported — passwords, TOTP seeds and card
numbers never have any.

Selection: ``GAI_SECRET_BACKEND`` = ``auto`` (default) | ``security`` |
``secret-tool`` | ``file``. An explicit choice is honoured even when it will
fail, so the error surfaces instead of silently degrading.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import settings

SERVICE = "GaiConsole"
KEYRING_ATTR = ("service", SERVICE)

_backend: str | None = None
_backend_lock = threading.Lock()


# ── macOS: Apple `security` ─────────────────────────────────────────────────
def _security(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _security_put(ref: str, value: str) -> None:
    proc = _security(
        ["add-generic-password", "-U", "-s", SERVICE, "-a", ref, "-w"],
        stdin=f"{value}\n{value}\n",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"keychain write failed for {ref}: {proc.stderr.strip()}")


def _security_get(ref: str) -> str:
    proc = _security(["find-generic-password", "-s", SERVICE, "-a", ref, "-w"])
    if proc.returncode != 0:
        return ""
    return proc.stdout.rstrip("\n")


def _security_delete(ref: str) -> None:
    _security(["delete-generic-password", "-s", SERVICE, "-a", ref])


# ── Linux: libsecret `secret-tool` ──────────────────────────────────────────
def _secret_tool(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["secret-tool", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _secret_tool_put(ref: str, value: str) -> None:
    proc = _secret_tool(
        ["store", "--label", f"{SERVICE} {ref}", *KEYRING_ATTR, "ref", ref],
        stdin=value,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"secret-tool write failed for {ref}: {proc.stderr.strip()}")


def _secret_tool_get(ref: str) -> str:
    proc = _secret_tool(["lookup", *KEYRING_ATTR, "ref", ref])
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _secret_tool_delete(ref: str) -> None:
    _secret_tool(["clear", *KEYRING_ATTR, "ref", ref])


def _secret_tool_works() -> bool:
    """A binary on PATH is not a working keyring. Round-trip a throwaway value.

    On a server with no session bus, `secret-tool` exists and every call fails
    with "Cannot autolaunch D-Bus without X11 $DISPLAY" — worth knowing before
    the first real password is written.
    """
    ref = "__gai_probe__"
    try:
        _secret_tool_put(ref, "probe")
        ok = _secret_tool_get(ref) == "probe"
    except (OSError, RuntimeError):
        return False
    finally:
        _secret_tool_delete(ref)
    return ok


# ── file fallback ───────────────────────────────────────────────────────────
def _store_path() -> Path:
    return settings.data_dir / "vault.json"


def _store_load() -> dict[str, str]:
    try:
        raw = _store_path().read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _store_save(data: dict[str, str]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Nama tmp unik per proses: dua penulis tidak saling menimpa file temp.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        if sys.platform == "win32":
            # Di NTFS, pembaca yang sedang membuka vault bisa membuat os.replace
            # gagal sesaat dengan PermissionError — coba lagi beberapa kali.
            for attempt in range(10):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.05)
        else:
            os.replace(tmp, path)
    except BaseException:
        # Jangan tinggalkan tmp yatim saat tulis/replace gagal di tengah.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        path.chmod(0o600)
    except OSError:
        pass


_store_thread_lock = threading.Lock()


class _StoreLock:
    """Read-modify-write under a lock, so two writers cannot drop a key.

    A threading.Lock covers writers inside this process; the lock file adds
    inter-process exclusion — fcntl.flock on POSIX, msvcrt.locking on Windows
    (fcntl does not exist there, so the file lock used to be a no-op).
    """

    def __enter__(self) -> None:
        _store_thread_lock.acquire()
        try:
            path = _store_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path.with_suffix(".json.lock"), "a+")  # noqa: SIM115
            try:
                if sys.platform == "win32":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
        except BaseException:
            _store_thread_lock.release()
            raise

    def __exit__(self, *exc: object) -> None:
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        finally:
            self._fh.close()
            _store_thread_lock.release()


def _file_put(ref: str, value: str) -> None:
    with _StoreLock():
        data = _store_load()
        data[ref] = value
        _store_save(data)


def _file_get(ref: str) -> str:
    return _store_load().get(ref, "")


def _file_delete(ref: str) -> None:
    with _StoreLock():
        data = _store_load()
        data.pop(ref, None)
        _store_save(data)


# ── backend selection ───────────────────────────────────────────────────────
def _detect() -> str:
    want = (settings.secret_backend or "auto").strip().lower()
    if want in {"security", "secret-tool", "file"}:
        return want
    if sys.platform == "darwin" and shutil.which("security"):
        return "security"
    if shutil.which("secret-tool") and _secret_tool_works():
        return "secret-tool"
    return "file"


def backend() -> str:
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                _backend = _detect()
    return _backend


def describe() -> str:
    if sys.platform == "win32":
        file_label = f"file (ACL profil user), no keyring — {_store_path()}"
    else:
        file_label = f"file 0600, no keyring — {_store_path()}"
    return {
        "security": "macOS Keychain (security CLI)",
        "secret-tool": "Secret Service (libsecret secret-tool)",
        "file": file_label,
    }[backend()]


# ── public API ──────────────────────────────────────────────────────────────
def put(ref: str, value: str) -> None:
    match backend():
        case "security":
            _security_put(ref, value)
        case "secret-tool":
            _secret_tool_put(ref, value)
        case _:
            _file_put(ref, value)


def get(ref: str) -> str:
    match backend():
        case "security":
            return _security_get(ref)
        case "secret-tool":
            return _secret_tool_get(ref)
        case _:
            return _file_get(ref)


def delete(ref: str) -> None:
    match backend():
        case "security":
            _security_delete(ref)
        case "secret-tool":
            _secret_tool_delete(ref)
        case _:
            _file_delete(ref)


def exists(ref: str) -> bool:
    return bool(get(ref))


def reset() -> None:
    """Forget the cached backend choice (tests, or after installing libsecret)."""
    global _backend
    with _backend_lock:
        _backend = None
