from __future__ import annotations

import os
import time
from pathlib import Path

_WANTED = ("session_id", "csrftoken")


def default_profile_dir() -> str:
    """The persistent Playwright profile shared by every sign-in path.

    Both the login CLI and the in-Claude reauth tool must point at the SAME
    profile, or a reauth would open a cold browser and lose the "completes on
    its own if already signed in" behavior. Lives at the repo root beside
    financial_mcp/ (this file is financial_mcp/monarch/browser_login.py, so up
    two parents)."""
    return str(Path(__file__).resolve().parents[2] / ".pw-profile")


def timeout_kwargs() -> dict:
    """Optional timeout override from FINANCIAL_MCP_LOGIN_TIMEOUT.

    Returns {"timeout_seconds": int} when the env var is a valid integer, else
    {} so capture_session_via_browser uses its default. A cold first-time
    sign-in (email+password+MFA) can need longer than the default 180s."""
    raw = os.environ.get("FINANCIAL_MCP_LOGIN_TIMEOUT")
    if raw:
        try:
            return {"timeout_seconds": int(raw)}
        except ValueError:
            pass
    return {}

MONARCH_LOGIN_URL = "https://app.monarch.com/login"
# session_id and csrftoken are scoped to different Monarch domains
# (session_id → .api.monarch.com, csrftoken → .monarch.com). The cookie read
# must cover BOTH, or the poll never sees session_id and loops until timeout.
# Kept Monarch-only (no Google/other SSO domains) to preserve defense-in-depth.
_COOKIE_DOMAINS = ("https://app.monarch.com", "https://api.monarch.com")
DEFAULT_TIMEOUT_SECONDS = 180
_POLL_INTERVAL_SECONDS = 1.0


class BrowserLoginError(RuntimeError):
    """Browser-driven login could not complete; the caller should fall back."""


def extract_wanted_cookies(cookies: list[dict]) -> dict:
    """Pull session_id + csrftoken from a Playwright cookie list.

    `cookies` is the list of dicts returned by BrowserContext.cookies(),
    each shaped like {"name": ..., "value": ..., "domain": ...}. Raises
    ValueError (naming the missing cookie) if either required cookie is
    absent or empty — same error shape as parse_curl_cookies().
    """
    jar = {c.get("name"): c.get("value") for c in cookies if c.get("name")}
    out = {}
    for name in _WANTED:
        if not jar.get(name):
            raise ValueError(
                f"could not find '{name}' cookie in the browser session — "
                "make sure you are fully signed in to Monarch"
            )
        out[name] = jar[name]
    return out


def capture_session_via_browser(
    *,
    monarch_url: str = MONARCH_LOGIN_URL,
    profile_dir: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Open a persistent-context Chromium, let the user sign in to Monarch,
    and return {"session_id", "csrftoken"} once both cookies are present.

    This function returns as soon as the cookies appear in the browser;
    validating the session against a live Monarch call is the caller's
    responsibility. Raises BrowserLoginError if Playwright is unavailable,
    the browser cannot launch, or the sign-in is not completed before
    timeout. The persistent context (profile_dir) lets the browser session
    persist between logins, which also mitigates Google's automation checks.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # Playwright not installed
        raise BrowserLoginError("Playwright is not installed") from exc

    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(profile_dir, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(monarch_url)
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    try:
                        return extract_wanted_cookies(context.cookies(list(_COOKIE_DOMAINS)))
                    except ValueError:
                        time.sleep(_POLL_INTERVAL_SECONDS)
                raise BrowserLoginError("timed out waiting for Monarch sign-in")
            finally:
                context.close()
    except BrowserLoginError:
        raise
    except Exception as exc:  # launch failure, MDM block, closed window, etc.
        raise BrowserLoginError(f"browser login failed: {exc}") from exc
