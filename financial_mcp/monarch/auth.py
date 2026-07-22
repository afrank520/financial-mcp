from __future__ import annotations

import json

import keyring

DEFAULT_SERVICE = "financial_mcp_monarch"
SESSION_USERNAME = "monarch_session"


def save_session(session_id: str, csrftoken: str, service_name: str = DEFAULT_SERVICE) -> None:
    blob = json.dumps({"session_id": session_id, "csrftoken": csrftoken})
    keyring.set_password(service_name, SESSION_USERNAME, blob)


def load_session(service_name: str = DEFAULT_SERVICE) -> dict | None:
    blob = keyring.get_password(service_name, SESSION_USERNAME)
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "session_id" not in data or "csrftoken" not in data:
        return None
    return {"session_id": data["session_id"], "csrftoken": data["csrftoken"]}


def delete_session(service_name: str = DEFAULT_SERVICE) -> None:
    try:
        keyring.delete_password(service_name, SESSION_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent
