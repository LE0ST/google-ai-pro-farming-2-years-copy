"""Keychain access through Apple's own `security` CLI.

No third-party keyring package: `security` ships with macOS, and the secret goes
in over stdin (it prompts twice for confirmation), so it never appears in a
process argument list. Values containing newlines are not supported — passwords,
TOTP seeds and card numbers never have any.
"""

from __future__ import annotations

import subprocess

SERVICE = "GaiConsole"


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def put(ref: str, value: str) -> None:
    proc = _run(
        ["add-generic-password", "-U", "-s", SERVICE, "-a", ref, "-w"],
        stdin=f"{value}\n{value}\n",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"keychain write failed for {ref}: {proc.stderr.strip()}")


def get(ref: str) -> str:
    proc = _run(["find-generic-password", "-s", SERVICE, "-a", ref, "-w"])
    if proc.returncode != 0:
        return ""
    return proc.stdout.rstrip("\n")


def delete(ref: str) -> None:
    _run(["delete-generic-password", "-s", SERVICE, "-a", ref])


def exists(ref: str) -> bool:
    return bool(get(ref))
