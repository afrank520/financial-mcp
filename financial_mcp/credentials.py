from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("financial_mcp")

DEFAULT_SERVICE = "financial_mcp_monarch"
SESSION_USERNAME = "monarch_session"


def _validate(data: object) -> dict | None:
    if not isinstance(data, dict) or "session_id" not in data or "csrftoken" not in data:
        return None
    return {"session_id": data["session_id"], "csrftoken": data["csrftoken"]}


class CredentialStore(Protocol):
    def save_session(self, session_id: str, csrftoken: str) -> None: ...
    def load_session(self) -> dict | None: ...
    def delete_session(self) -> None: ...


class FileCredentialStore:
    """Stores the session blob in a permission-locked local file (0600)."""

    def __init__(self, path: str) -> None:
        self._path = Path(path).expanduser()

    def save_session(self, session_id: str, csrftoken: str) -> None:
        blob = json.dumps({"session_id": session_id, "csrftoken": csrftoken})
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)  # best-effort; POSIX only
        except OSError:
            logger.warning("could not set 0700 on %s", parent)
        tmp = self._path.with_name(self._path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(blob)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load_session(self) -> dict | None:
        try:
            mode = stat.S_IMODE(self._path.stat().st_mode)
        except FileNotFoundError:
            return None
        if mode & 0o177:
            logger.warning(
                "credential file %s had loose permissions %o; tightening to 600",
                self._path, mode,
            )
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return _validate(data)

    def delete_session(self) -> None:
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass
