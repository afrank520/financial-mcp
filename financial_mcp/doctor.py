from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

from financial_mcp.config import ensure_parent, load_config
from financial_mcp.credentials import make_credential_store
from financial_mcp.monarch.community_adapter import (
    CommunityAdapter,
    SessionExpiredError,
    _SSL_CTX,
)


def _cfg():
    return load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))


def _load_session():
    return make_credential_store(_cfg()).load_session()


def check_python() -> tuple[bool, str]:
    v = sys.version_info
    ok = (3, 12) <= (v.major, v.minor) < (3, 14)
    return ok, f"Python {v.major}.{v.minor}.{v.micro}" + ("" if ok else " (need 3.12 or 3.13)")


def check_config() -> tuple[bool, str]:
    try:
        cfg = _cfg()
    except Exception as exc:  # noqa: BLE001
        return False, f"config failed to load: {exc}"
    for label, path in (("db_path", cfg.db_path), ("credential_file", cfg.credential_file)):
        if not Path(path).is_absolute():
            return False, f"{label} did not resolve to an absolute path: {path}"
        try:
            ensure_parent(path)
        except OSError as exc:
            return False, f"{label} parent not writable: {exc}"
    return True, "config paths resolve and are writable"


def check_secure_connection() -> tuple[bool, str]:
    try:
        req = urllib.request.Request("https://api.monarch.com/", method="HEAD")
        urllib.request.urlopen(req, timeout=15, context=_SSL_CTX)
        return True, "secure connection to Monarch OK"
    except Exception as exc:  # noqa: BLE001
        if "CERTIFICATE_VERIFY" in str(exc):
            return False, (
                "secure connection failed (certificate). On a python.org Mac install, "
                "run the bundled 'Install Certificates.command'."
            )
        # A reachable-but-unauthorized/redirect response still proves TLS works.
        if "HTTP Error" in str(exc):
            return True, "secure connection to Monarch OK"
        return False, f"secure connection failed: {type(exc).__name__}: {exc}"


def check_session() -> tuple[bool, str]:
    session = _load_session()
    if not session:
        return False, "no saved session — run `financial-mcp-login`"
    try:
        n = len(CommunityAdapter(session).get_accounts())
        return True, f"session valid — {n} accounts"
    except SessionExpiredError:
        return False, "session expired — run `financial-mcp-login`"
    except Exception as exc:  # noqa: BLE001
        return False, f"session check failed: {type(exc).__name__}: {exc}"


CHECKS = ("check_python", "check_config", "check_secure_connection", "check_session")


def run() -> int:
    failures = 0
    for name in CHECKS:
        ok, msg = globals()[name]()
        print(f"{'✅' if ok else '❌'} {msg}")
        if not ok:
            failures += 1
    print("\nAll green — you're set." if failures == 0 else f"\n{failures} issue(s) found — see above.")
    return 0 if failures == 0 else 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
