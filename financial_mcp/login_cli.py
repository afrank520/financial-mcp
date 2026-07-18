from __future__ import annotations

import asyncio
import getpass
import sys

from financial_mcp.config import load_config
import os

from financial_mcp.monarch import auth


def _service_name() -> str:
    cfg = load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))
    return cfg.keyring_service_name


async def _validate_token(token: str) -> bool:
    from monarchmoney import MonarchMoney

    mm = MonarchMoney(token=token)
    try:
        await mm.get_accounts()
        return True
    except Exception:  # noqa: BLE001
        print("That token did not work. Check the format and try again.", file=sys.stderr)
        return False


async def _login_with_password(email: str, password: str) -> str | None:
    from monarchmoney import MonarchMoney, RequireMFAException

    mm = MonarchMoney()
    try:
        await mm.login(email, password, save_session=False)
    except RequireMFAException:
        code = input("MFA code: ").strip()
        await mm.multi_factor_authenticate(email, password, code)
    return mm.token


def _token_path() -> None:
    token = getpass.getpass(
        "Paste your Monarch token (from the `Authorization: Token XXXX` "
        "request header in browser DevTools): "
    ).strip()
    if token.lower().startswith("token "):
        token = token[len("token ") :].strip()
    if asyncio.run(_validate_token(token)):
        auth.save_token(token, _service_name())
        print("Session saved to Keychain.")
    else:
        raise SystemExit("Login failed. Try again.")


def _password_path() -> None:
    email = input("Monarch email: ").strip()
    password = getpass.getpass("Monarch password (not stored): ")
    token = asyncio.run(_login_with_password(email, password))
    if not token:
        raise SystemExit("Login failed — no token returned.")
    auth.save_token(token, _service_name())
    print("Session saved to Keychain.")


def main() -> None:
    if "--logout" in sys.argv:
        auth.delete_token(_service_name())
        print("Logged out — token removed from Keychain.")
        return
    print("Monarch login. Choose a method:")
    print("  1) Paste browser token (recommended)")
    print("  2) Email + password + MFA")
    choice = input("> ").strip()
    if choice == "2":
        _password_path()
    else:
        _token_path()


if __name__ == "__main__":
    main()
