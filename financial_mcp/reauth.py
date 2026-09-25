"""In-Claude re-authentication.

Recovers an expired Monarch session from inside Claude (no Terminal trip): it
drives the SAME browser sign-in the login CLI uses, validates the fresh session
against one live call, saves it to the configured credential store, and returns
a live adapter the caller hot-swaps into the running DataService.

This deliberately does NOT reuse login_cli._browser_login / _login: those print
progress to stdout, which would corrupt the MCP stdio protocol stream. The
underlying capture/validate/save helpers are the shared, tested primitives.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os

from financial_mcp.config import load_config
from financial_mcp.credentials import make_credential_store

logger = logging.getLogger("financial_mcp")


class ReauthError(RuntimeError):
    """Re-authentication could not complete (sign-in cancelled/timed out, or the
    captured session failed validation). The tool layer turns this into a
    friendly, actionable message."""


def _load_cfg():
    return load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))


def _capture_browser_session() -> dict | None:
    """Open the persistent-profile Chromium and capture a fresh session.

    Returns {"session_id", "csrftoken"} on success, or None if the browser could
    not be driven to a signed-in state (launch failure, MDM block, timeout,
    window closed). Never prints — safe to call from inside the MCP server.
    Uses the SAME persistent profile + timeout override as the login CLI, so a
    warm session completes on its own.
    """
    from financial_mcp.monarch.browser_login import (
        BrowserLoginError,
        capture_session_via_browser,
        default_profile_dir,
        timeout_kwargs,
    )

    try:
        return capture_session_via_browser(
            profile_dir=default_profile_dir(), **timeout_kwargs()
        )
    except BrowserLoginError:
        return None


def _validate(session: dict) -> bool:
    """Confirm the session works with one live accounts call."""
    from financial_mcp.monarch.community_adapter import (
        CloudflareBlockedError,
        CommunityAdapter,
        SessionExpiredError,
    )

    try:
        CommunityAdapter(session).get_accounts()
        return True
    except (SessionExpiredError, CloudflareBlockedError):
        return False
    except Exception as exc:  # noqa: BLE001 — cert/connection problems also mean "not usable"
        # Never print (stdio); log so an unexpected failure is diagnosable
        # rather than silently read as "session couldn't be verified".
        logger.warning("reauth session validation failed: %s: %s", type(exc).__name__, exc)
        return False


def _build_live_adapter(session: dict):
    from financial_mcp.monarch.community_adapter import CommunityAdapter

    # Give the hot-swapped adapter the same self-heal callback the startup
    # adapter gets, so a later expiry reloads from the credential store too.
    store = make_credential_store(_load_cfg())
    return CommunityAdapter(session, reload_session=store.load_session)


def _run_off_loop(fn, *args):
    """Run a blocking callable to completion, tolerating an already-running loop.

    FastMCP invokes sync tools ON its running asyncio loop, but sync Playwright
    refuses to run inside one ("Sync API inside the asyncio loop"). A fresh
    worker thread has no running loop, so offload there when one is running.
    Mirrors CommunityAdapter._run (same reason, same repo pattern).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn(*args)  # no loop (e.g. tests, CLI) — run inline
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn, *args).result()


def reauthenticate():
    """Run the full re-auth flow and return a live adapter on success.

    Raises ReauthError if the sign-in did not complete or the captured session
    failed validation; in either case nothing is saved.
    """
    cfg = _load_cfg()
    # The browser capture (sync Playwright) must run off any running event loop.
    session = _run_off_loop(_capture_browser_session)
    if not session:
        raise ReauthError(
            "the sign-in window did not complete — no fresh session was captured"
        )
    if not _validate(session):
        raise ReauthError(
            "the captured session could not be verified against Monarch "
            "(it may still be expired, or this Mac has a secure-connection problem)"
        )
    make_credential_store(cfg).save_session(session["session_id"], session["csrftoken"])
    return _build_live_adapter(session)
