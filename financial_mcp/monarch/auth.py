from __future__ import annotations

import keyring

DEFAULT_SERVICE = "financial_mcp_monarch"
TOKEN_USERNAME = "monarch_token"


def save_token(token: str, service_name: str = DEFAULT_SERVICE) -> None:
    keyring.set_password(service_name, TOKEN_USERNAME, token)


def load_token(service_name: str = DEFAULT_SERVICE) -> str | None:
    return keyring.get_password(service_name, TOKEN_USERNAME)


def delete_token(service_name: str = DEFAULT_SERVICE) -> None:
    try:
        keyring.delete_password(service_name, TOKEN_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent
