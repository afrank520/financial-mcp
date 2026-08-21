# financial_mcp/monarch/auth.py
# Legacy shim: credential storage moved to financial_mcp.credentials.
# Retained so existing imports (community_adapter, tests) keep working.
from __future__ import annotations

from financial_mcp.credentials import (
    DEFAULT_SERVICE,
    SESSION_USERNAME,
    KeyringCredentialStore,
)

__all__ = ["DEFAULT_SERVICE", "SESSION_USERNAME", "save_session", "load_session", "delete_session"]


def save_session(session_id: str, csrftoken: str, service_name: str = DEFAULT_SERVICE) -> None:
    KeyringCredentialStore(service_name).save_session(session_id, csrftoken)


def load_session(service_name: str = DEFAULT_SERVICE) -> dict | None:
    return KeyringCredentialStore(service_name).load_session()


def delete_session(service_name: str = DEFAULT_SERVICE) -> None:
    KeyringCredentialStore(service_name).delete_session()
