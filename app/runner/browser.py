"""Browser selection for the read-only diagnostic runner only."""

from __future__ import annotations

from typing import Any

from ..config import settings

_SUPPORTED_CHANNELS = {"chrome", "msedge", "chrome-beta", "chrome-dev", "chrome-canary"}


def resolve_browser() -> dict[str, Any]:
    """Select a browser binary without stealth, proxy, or TLS override flags."""
    executable = (settings.browser_executable or "").strip()
    if executable:
        return {"executable_path": executable}
    channel = (settings.browser_channel or "auto").strip().lower()
    return {"channel": channel} if channel in _SUPPORTED_CHANNELS else {}


def describe() -> str:
    selected = resolve_browser()
    if "channel" in selected:
        return f"Playwright channel: {selected['channel']}"
    if "executable_path" in selected:
        return "Custom browser executable configured (path withheld)"
    return "Playwright-managed Chromium"
