from __future__ import annotations

import os
import shlex
import sys

from financial_mcp.config import load_config
from financial_mcp.monarch import auth

_WANTED = ("session_id", "csrftoken")


def _service_name() -> str:
    cfg = load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))
    return cfg.keyring_service_name


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


def _login() -> None:
    print(
        "Monarch login.\n"
        "1. In your browser, open Monarch and log in.\n"
        "2. DevTools -> Network tab, filter 'graphql', click a POST request to "
        "api.monarch.com.\n"
        "3. Right-click it -> Copy -> Copy as cURL.\n"
        "4. Paste it below, then press Ctrl-D (Ctrl-Z Enter on Windows):"
    )
    blob = sys.stdin.read()
    try:
        session = parse_curl_cookies(blob)
    except ValueError as exc:
        raise SystemExit(f"Login failed: {exc}")
    if not _validate(session):
        raise SystemExit("Login failed. Try again with a fresh request.")
    auth.save_session(session["session_id"], session["csrftoken"], _service_name())
    print("Session saved to Keychain.")


def main() -> None:
    if "--logout" in sys.argv:
        auth.delete_session(_service_name())
        print("Logged out — session removed from Keychain.")
        return
    _login()


if __name__ == "__main__":
    main()
