from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from financial_mcp.config import load_config
from financial_mcp.credentials import KeyringCredentialStore, make_credential_store

_WANTED = ("session_id", "csrftoken")


def _load_cfg():
    return load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))


def _service_name() -> str:
    return _load_cfg().keyring_service_name


def parse_curl_cookies(blob: str) -> dict:
    """Extract session_id + csrftoken from a browser 'Copy as cURL' command.

    Reads cookies from a -b/--cookie argument or a 'Cookie:' header. Raises
    ValueError (naming the missing cookie) if either required cookie is absent.
    """
    text = blob.replace("\\\n", " ").strip()
    if not text:
        raise ValueError("empty input — paste the full 'Copy as cURL' command")
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError(f"could not parse the cURL command: {exc}") from exc

    cookie_str = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-b", "--cookie") and i + 1 < len(tokens):
            cookie_str = tokens[i + 1]
            i += 2
            continue
        if tok in ("-H", "--header") and i + 1 < len(tokens):
            k, _, v = tokens[i + 1].partition(":")
            if k.strip().lower() == "cookie":
                cookie_str = cookie_str or v.strip()
            i += 2
            continue
        i += 1

    jar: dict[str, str] = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            jar[k.strip()] = v.strip()

    out = {}
    for name in _WANTED:
        if name not in jar or not jar[name]:
            raise ValueError(
                f"could not find '{name}' cookie in the pasted request — make sure you "
                "copied a request to api.monarch.com while logged in"
            )
        out[name] = jar[name]
    return out


def _validate(session: dict) -> bool:
    """Confirm the session works with one live accounts call."""
    from financial_mcp.monarch.community_adapter import CommunityAdapter

    try:
        CommunityAdapter(session).get_accounts()
        return True
    except Exception:  # noqa: BLE001
        print(
            "That session did not work — it may be expired or incomplete. "
            "Log in fresh in your browser and copy a new request.",
            file=sys.stderr,
        )
        return False


def _curl_login() -> dict:
    """Manual fallback: read a 'Copy as cURL' blob from stdin and parse it."""
    print(
        "Monarch manual login.\n"
        "1. In your browser, open Monarch and log in.\n"
        "2. DevTools -> Network tab, filter 'graphql', click a POST request to "
        "api.monarch.com.\n"
        "3. Right-click it -> Copy -> Copy as cURL.\n"
        "4. Paste it below, then press Ctrl-D (Ctrl-Z Enter on Windows):"
    )
    blob = sys.stdin.read()
    try:
        return parse_curl_cookies(blob)
    except ValueError:
        raise SystemExit(
            "Login failed: could not extract session cookies from the pasted "
            "cURL command. Make sure you copied a request to api.monarch.com "
            "while logged in."
        )


def _browser_login() -> dict | None:
    """Attempt browser-driven login. Returns the session dict on success, or
    None on any failure so the caller falls back to the manual path."""
    from financial_mcp.monarch.browser_login import (
        BrowserLoginError,
        capture_session_via_browser,
    )

    profile_dir = str(Path(__file__).resolve().parent.parent / ".pw-profile")
    print(
        "Opening a browser for Monarch sign-in — complete Google sign-in in "
        "the window that appears."
    )
    kwargs = {"profile_dir": profile_dir}
    # Optional override for a cold first-time sign-in (email+password+MFA) that
    # needs longer than the default 180s. Ignored if unset or non-integer.
    raw_timeout = os.environ.get("FINANCIAL_MCP_LOGIN_TIMEOUT")
    if raw_timeout:
        try:
            kwargs["timeout_seconds"] = int(raw_timeout)
        except ValueError:
            pass
    try:
        return capture_session_via_browser(**kwargs)
    except BrowserLoginError as exc:
        print(
            f"Browser login unavailable ({exc}). Falling back to manual login.",
            file=sys.stderr,
        )
        return None


def _login(force_curl: bool = False) -> None:
    session = None
    if not force_curl:
        session = _browser_login()
    if session is None:
        session = _curl_login()
    if not _validate(session):
        raise SystemExit("Login failed. Try again with a fresh session.")
    make_credential_store(_load_cfg()).save_session(session["session_id"], session["csrftoken"])
    print("Signed in — session saved.")


def _migrate_from_keyring() -> None:
    cfg = _load_cfg()
    if cfg.credential_store == "keyring":
        # The destination store IS the keyring; migrating keyring->keyring would
        # save then delete the same entry, losing the session. Refuse.
        raise SystemExit(
            "Nothing to migrate: config has credential_store: keyring, so the "
            "destination IS the keyring. Set credential_store: file (or leave it "
            "unset — file is the default) before running --migrate-from-keyring."
        )
    src = KeyringCredentialStore(_service_name())  # one keyring read (may prompt once)
    session = src.load_session()
    if not session:
        print("No keyring session found to migrate.")
        return
    make_credential_store(cfg).save_session(session["session_id"], session["csrftoken"])
    src.delete_session()
    print("Migrated session from keyring to the file store; keyring entry removed.")


def main() -> None:
    if "--migrate-from-keyring" in sys.argv:
        _migrate_from_keyring()
        return
    if "--logout" in sys.argv:
        make_credential_store(_load_cfg()).delete_session()
        print("Logged out — session removed.")
        return
    _login(force_curl="--curl" in sys.argv)


if __name__ == "__main__":
    main()
