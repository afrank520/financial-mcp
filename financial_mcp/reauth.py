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

import os
from pathlib import Path

from financial_mcp.config import load_config
from financial_mcp.credentials import make_credential_store


class ReauthError(RuntimeError):
    """Re-authentication could not complete (sign-in cancelled/timed out, or the
    captured session failed validation). The tool layer turns this into a
    friendly, actionable message."""


def _load_cfg():
    return load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))


def _capture_browser_session(cfg) -> dict | None:
    """Open the persistent-profile Chromium and capture a fresh session.

    Returns {"session_id", "csrftoken"} on success, or None if the browser could
    not be driven to a signed-in state (launch failure, MDM block, timeout,
    window closed). Never prints — safe to call from inside the MCP server.
    """
    from financial_mcp.monarch.browser_login import (
        BrowserLoginError,
        capture_session_via_browser,
    )

    profile_dir = str(Path(__file__).resolve().parent.parent / ".pw-profile")
    kwargs = {"profile_dir": profile_dir}
    raw_timeout = os.environ.get("FINANCIAL_MCP_LOGIN_TIMEOUT")
    if raw_timeout:
        try:
            kwargs["timeout_seconds"] = int(raw_timeout)
        except ValueError:
            pass
    try:
        return capture_session_via_browser(**kwargs)
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
    except Exception:  # noqa: BLE001 — cert/connection problems also mean "not usable"
        return False


def _build_live_adapter(session: dict):
    from financial_mcp.monarch.community_adapter import CommunityAdapter

    return CommunityAdapter(session)


def reauthenticate():
    """Run the full re-auth flow and return a live adapter on success.

    Raises ReauthError if the sign-in did not complete or the captured session
    failed validation; in either case nothing is saved.
    """
    cfg = _load_cfg()
    session = _capture_browser_session(cfg)
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
