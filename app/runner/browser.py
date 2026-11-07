"""Persistent Chrome context. One profile dir per account, never shared.

Browser resolution is the one place the two platforms genuinely differ:

* macOS: Google Chrome lives at ``/Applications/Google Chrome.app`` and
  Playwright reaches it with ``channel="chrome"``.
* Ubuntu: Chrome is ``google-chrome`` on PATH, but a machine may instead carry
  distro ``chromium``, a snap, or nothing at all. Playwright's ``channel``
  cannot point at an arbitrary binary, so a discovered Chromium is passed as
  ``executable_path`` and the bundled browser is the last resort.

Display resolution is the other: a desktop runs headed, a server has no
``DISPLAY`` and must run headless, or every launch dies with
"Missing X server or $DISPLAY".
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, cast

from playwright.async_api import BrowserContext, Error as PlaywrightError, Playwright

from ..config import settings

log = logging.getLogger("gai.browser")

ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
]

# Containers and small VMs give /dev/shm 64 MB; Chrome crashes on that.
LINUX_ARGS = ["--disable-dev-shm-usage"]

_CHROME_NAMES = ("google-chrome", "google-chrome-stable")
_CHROMIUM_NAMES = ("chromium", "chromium-browser")
_CHROME_PATHS = ("/opt/google/chrome/chrome", "/opt/google/chrome/google-chrome")
_CHROMIUM_PATHS = ("/snap/bin/chromium", "/usr/lib/chromium/chromium")


def has_display() -> bool:
    if sys.platform == "darwin":
        return True
    if sys.platform.startswith("win"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def resolve_headless(requested: bool | None = None) -> bool:
    """Precedence: GAI_HEADLESS, then the call site, then the display.

    A call site asking for headed on a box with no display is overridden — the
    alternative is a hard crash on the first launch — and says so in the log.
    """
    mode = (settings.headless or "auto").strip().lower()
    if mode in {"1", "true", "yes", "on"}:
        return True
    if mode in {"0", "false", "no", "off"}:
        return False
    if requested is not None:
        if not requested and not has_display():
            log.warning("no DISPLAY/WAYLAND_DISPLAY — running headless instead of headed")
            return True
        return requested
    return not has_display()


def _first_executable(names: tuple[str, ...], paths: tuple[str, ...]) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for path in paths:
        if os.access(path, os.X_OK):
            return path
    return ""


def resolve_browser() -> dict[str, Any]:
    """Arguments that select a browser, in the order the operator asked for."""
    exe = (settings.browser_executable or "").strip()
    if exe:
        return {"executable_path": exe}

    want = (settings.browser_channel or "auto").strip().lower()
    if want in {"bundled", "playwright", "none"}:
        return {}
    if want not in {"auto", ""}:
        return {"channel": want}

    # auto
    if sys.platform == "darwin" and Path("/Applications/Google Chrome.app").exists():
        return {"channel": "chrome"}
    chrome = _first_executable(_CHROME_NAMES, _CHROME_PATHS)
    if chrome:
        return {"channel": "chrome"}
    chromium = _first_executable(_CHROMIUM_NAMES, _CHROMIUM_PATHS)
    if chromium:
        return {"executable_path": chromium}
    return {}


def describe() -> str:
    choice = resolve_browser()
    label = (
        choice.get("channel")
        or choice.get("executable_path")
        or "playwright bundled chromium"
    )
    return f"{label} ({'headless' if resolve_headless() else 'headed'})"


def _base_args() -> list[str]:
    args = list(ARGS)
    if sys.platform.startswith("linux"):
        args += LINUX_ARGS
    if settings.no_sandbox:
        args.append("--no-sandbox")
    return args


async def _open(
    p: Playwright,
    profile_dir: Path,
    *,
    headless: bool,
    proxy: dict[str, Any] | None,
    args: list[str],
    choice: dict[str, Any],
) -> BrowserContext:
    return await p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        locale="en-US",
        timezone_id="America/Los_Angeles",
        viewport={"width": 1440, "height": 900},
        proxy=cast("Any", proxy),
        args=args,
        **choice,
    )


def _is_missing_browser(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        needle in text
        for needle in (
            "executable doesn't exist",
            "executable doesn't exist at",
            "browser executable",
            "is not found",
            "failed to launch chromium",
            "playwright install",
        )
    )


def _is_sandbox(exc: Exception) -> bool:
    text = str(exc).lower()
    return "no usable sandbox" in text or "sandboxing" in text or "clone3" in text


async def launch(
    p: Playwright,
    profile_dir: Path,
    *,
    headless: bool | None = None,
    proxy: dict[str, Any] | None = None,
) -> BrowserContext:
    profile_dir.mkdir(parents=True, exist_ok=True)
    headless_now = resolve_headless(headless)
    choice = resolve_browser()
    args = _base_args()

    try:
        return await _open(
            p, profile_dir, headless=headless_now, proxy=proxy, args=args, choice=choice
        )
    except PlaywrightError as exc:
        if _is_sandbox(exc) and "--no-sandbox" not in args:
            log.warning("chromium sandbox unavailable — retrying with --no-sandbox")
            return await _open(
                p,
                profile_dir,
                headless=headless_now,
                proxy=proxy,
                args=[*args, "--no-sandbox"],
                choice=choice,
            )
        if choice and _is_missing_browser(exc):
            log.warning(
                "browser %s unusable (%s) — falling back to playwright's bundled chromium; "
                "run `python -m playwright install chromium` if that also fails",
                choice,
                str(exc).splitlines()[0][:120],
            )
            return await _open(
                p, profile_dir, headless=headless_now, proxy=proxy, args=args, choice={}
            )
        raise
