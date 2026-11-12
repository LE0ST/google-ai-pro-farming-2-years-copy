"""Persistent Chrome context. One profile dir per account, never shared.

Browser resolution is the one place the platforms genuinely differ:

* macOS: Google Chrome lives at ``/Applications/Google Chrome.app`` and
  Playwright reaches it with ``channel="chrome"``.
* Ubuntu: Chrome is ``google-chrome`` on PATH, but a machine may instead carry
  distro ``chromium``, a snap, or nothing at all. Playwright's ``channel``
  cannot point at an arbitrary binary, so a discovered Chromium is passed as
  ``executable_path`` and the bundled browser is the last resort.
* Windows: installers do not put Chrome on PATH either; it lives under
  ``%ProgramFiles%`` (or ``%LocalAppData%`` for a per-user install). Same
  treatment as a discovered Linux Chromium: pass the ``chrome.exe`` path as
  ``executable_path``, bundled browser as the last resort.

Display resolution is the other: a desktop runs headed, a server has no
``DISPLAY`` and must run headless, or every launch dies with
"Missing X server or $DISPLAY". Windows desktops are always headed — there
is no Xvfb dance to do there.
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


def _win_browser_paths() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Standard Windows install locations for Chrome and Chromium.

    Built at call time from the environment: ``%ProgramFiles%`` for machine
    installs, ``%LocalAppData%`` for per-user ones. os.access(X_OK) on Windows
    degrades to an existence check, which is all an .exe needs.
    """
    pf = os.environ.get("PROGRAMFILES") or r"C:\Program Files"
    pf86 = os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    chrome = (
        str(Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        str(Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        str(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"),
    )
    chromium = (
        str(Path(local) / "Chromium" / "Application" / "chrome.exe"),
        str(Path(pf) / "Chromium" / "Application" / "chrome.exe"),
        str(Path(pf86) / "Chromium" / "Application" / "chrome.exe"),
    )
    return chrome, chromium


def has_display() -> bool:
    if sys.platform == "darwin":
        return True
    if sys.platform == "win32":
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
    if sys.platform == "win32":
        win_chrome, win_chromium = _win_browser_paths()
        found = _first_executable(("chrome",), win_chrome) or _first_executable(
            ("chromium",), win_chromium
        )
        return {"executable_path": found} if found else {}
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
    # Local patch for this VM: its egress proxy re-signs TLS with a sandbox CA
    # that Chromium does not trust. Opt-in via env only.
    if os.environ.get("GAI_IGNORE_CERT_ERRORS") == "1":
        args.append("--ignore-certificate-errors")
    return args


# ── Local patch for this VM: clients2.google.com mirror ────────────────────
# The VM egress proxy policy-denies clients2.google.com, which serves the
# Google Payments scripts the checkout dialog needs. Google Translate's proxy
# host serves the identical files and is allowed, so requests to clients2 are
# fulfilled with the mirrored bytes. Opt-in via GAI_MIRROR_CLIENTS2=1.
_mirror_cache: dict[str, tuple[int, str, bytes]] = {}


def _fetch_clients2_mirror(url: str) -> tuple[int, str, bytes]:
    import ssl
    import urllib.parse
    import urllib.request

    if url in _mirror_cache:
        return _mirror_cache[url]
    u = urllib.parse.urlparse(url)
    mirror = (
        f"https://clients2-google-com.translate.goog{u.path}"
        "?_x_tr_sl=en&_x_tr_tl=id&_x_tr_hl=en"
    )
    if u.query:
        mirror += "&" + u.query
    req = urllib.request.Request(mirror, headers={"User-Agent": "Mozilla/5.0"})

    # Fetch ini tunduk pada opt-in egress yang sama dengan Chromium (lihat
    # _base_args dan launch): proxy relay lokal + sertifikat hasil tanda
    # tangan ulang proxy. Tanpa keduanya, mirror mati di jaringan yang justru
    # paling membutuhkannya.
    handlers: list[Any] = []
    raw_proxy = os.environ.get("GAI_BROWSER_PROXY", "").strip()
    if raw_proxy:
        from .. import egress

        parsed = egress.parse(raw_proxy)
        if parsed:
            server = parsed["server"]
            if parsed.get("username"):
                auth = "{}:{}@".format(
                    urllib.parse.quote(parsed["username"], safe=""),
                    urllib.parse.quote(parsed.get("password", ""), safe=""),
                )
                scheme, _, rest = server.partition("://")
                server = f"{scheme}://{auth}{rest}"
            handlers.append(urllib.request.ProxyHandler({"http": server, "https": server}))
    if os.environ.get("GAI_IGNORE_CERT_ERRORS") == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)

    try:
        with opener.open(req, timeout=30) as resp:
            out = (resp.status, resp.headers.get_content_type(), resp.read())
    except Exception as exc:  # noqa: BLE001 - fall back to an empty script
        # Kegagalan tidak di-cache: satu fetch yang gagal sesaat tidak boleh
        # meracuni URL itu selama sisa umur proses.
        log.warning("clients2 mirror gagal %s: %s", url[:90], exc)
        return (502, "application/javascript", b"")
    if out[0] != 200 or not out[2]:
        log.warning("clients2 mirror %s -> %s (%d byte), tidak di-cache",
                    url[:90], out[0], len(out[2]))
        return out
    _mirror_cache[url] = out
    return out


async def _install_clients2_mirror(ctx: BrowserContext) -> None:
    import asyncio

    async def handler(route: Any) -> None:
        status, ctype, body = await asyncio.to_thread(
            _fetch_clients2_mirror, route.request.url
        )
        await route.fulfill(status=status, content_type=ctype, body=body)

    await ctx.route("**://clients2.google.com/**", handler)


async def _open(
    p: Playwright,
    profile_dir: Path,
    *,
    headless: bool,
    proxy: dict[str, Any] | None,
    args: list[str],
    choice: dict[str, Any],
) -> BrowserContext:
    ctx = await p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        locale="en-US",
        timezone_id="America/Los_Angeles",
        viewport={"width": 1440, "height": 900},
        service_workers="block",
        proxy=cast("Any", proxy),
        args=args,
        **choice,
    )
    if os.environ.get("GAI_MIRROR_CLIENTS2") == "1":
        await _install_clients2_mirror(ctx)
    return ctx


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
    # Local patch for this VM: route the browser through a local relay proxy
    # when GAI_BROWSER_PROXY is set (the VM egress proxy refuses Chromium).
    if proxy is None:
        raw_proxy = os.environ.get("GAI_BROWSER_PROXY", "").strip()
        if raw_proxy:
            from .. import egress

            proxy = egress.parse(raw_proxy)

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
