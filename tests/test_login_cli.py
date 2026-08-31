from types import SimpleNamespace

import pytest

import financial_mcp.login_cli as cli
from financial_mcp.login_cli import parse_curl_cookies

# Synthetic blob — FAKE cookie values, safe to commit.
_CURL_B_FLAG = (
    "curl 'https://api.monarch.com/graphql' "
    "-H 'content-type: application/json' "
    "-b 'ajs_anonymous_id=xxx; cf_clearance=yyy; "
    "session_id=SID_FAKE_123; csrftoken=CSRF_FAKE_456; _fbp=zzz' "
    "-H 'x-csrftoken: CSRF_FAKE_456'"
)

_CURL_COOKIE_HEADER = (
    "curl 'https://api.monarch.com/graphql' "
    "-H 'cookie: session_id=SID_FAKE_123; csrftoken=CSRF_FAKE_456' "
    "-H 'x-csrftoken: CSRF_FAKE_456'"
)


def test_parse_b_flag():
    got = parse_curl_cookies(_CURL_B_FLAG)
    assert got == {"session_id": "SID_FAKE_123", "csrftoken": "CSRF_FAKE_456"}


def test_parse_cookie_header():
    got = parse_curl_cookies(_CURL_COOKIE_HEADER)
    assert got == {"session_id": "SID_FAKE_123", "csrftoken": "CSRF_FAKE_456"}


def test_missing_session_id_raises():
    blob = "curl 'https://api.monarch.com/graphql' -b 'csrftoken=CSRF_FAKE_456'"
    with pytest.raises(ValueError, match="session_id"):
        parse_curl_cookies(blob)


def test_missing_csrftoken_raises():
    blob = "curl 'https://api.monarch.com/graphql' -b 'session_id=SID_FAKE_123'"
    with pytest.raises(ValueError, match="csrftoken"):
        parse_curl_cookies(blob)


def test_empty_blob_raises():
    with pytest.raises(ValueError):
        parse_curl_cookies("")


def _patch_tail(monkeypatch):
    """Stub the shared validate+save tail; return a dict that records the save."""
    monkeypatch.setattr(cli, "_validate", lambda session: True)
    saved = {}

    class _Store:
        def save_session(self, sid, csrf):
            saved["sid"], saved["csrf"] = sid, csrf

        def delete_session(self):
            saved["deleted"] = True

    monkeypatch.setattr(cli, "make_credential_store", lambda cfg: _Store())
    return saved


def test_login_uses_browser_session_when_available(monkeypatch):
    saved = _patch_tail(monkeypatch)
    curl_calls = {"n": 0}
    monkeypatch.setattr(cli, "_browser_login", lambda: {"session_id": "BROWSER", "csrftoken": "X"})

    def fake_curl():
        curl_calls["n"] += 1
        return {"session_id": "CURL", "csrftoken": "C"}

    monkeypatch.setattr(cli, "_curl_login", fake_curl)

    cli._login(force_curl=False)

    assert curl_calls["n"] == 0
    assert saved["sid"] == "BROWSER"


def test_login_falls_back_to_curl_when_browser_returns_none(monkeypatch):
    saved = _patch_tail(monkeypatch)
    monkeypatch.setattr(cli, "_browser_login", lambda: None)
    monkeypatch.setattr(cli, "_curl_login", lambda: {"session_id": "CURL", "csrftoken": "C"})

    cli._login(force_curl=False)

    assert saved["sid"] == "CURL"


def test_curl_flag_skips_browser(monkeypatch):
    saved = _patch_tail(monkeypatch)
    browser_calls = {"n": 0}

    def fake_browser():
        browser_calls["n"] += 1
        return {"session_id": "BROWSER", "csrftoken": "X"}

    monkeypatch.setattr(cli, "_browser_login", fake_browser)
    monkeypatch.setattr(cli, "_curl_login", lambda: {"session_id": "CURL", "csrftoken": "C"})

    cli._login(force_curl=True)

    assert browser_calls["n"] == 0
    assert saved["sid"] == "CURL"


def test_browser_login_returns_none_on_error(monkeypatch):
    def raiser(**kwargs):
        from financial_mcp.monarch.browser_login import BrowserLoginError

        raise BrowserLoginError("boom")

    monkeypatch.setattr("financial_mcp.monarch.browser_login.capture_session_via_browser", raiser)

    assert cli._browser_login() is None


def test_browser_login_passes_timeout_env_var(monkeypatch):
    """FINANCIAL_MCP_LOGIN_TIMEOUT overrides the browser-login timeout so a
    cold first-time sign-in (email+password+MFA) isn't rushed."""
    captured = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return {"session_id": "S", "csrftoken": "C"}

    monkeypatch.setattr("financial_mcp.monarch.browser_login.capture_session_via_browser", spy)
    monkeypatch.setenv("FINANCIAL_MCP_LOGIN_TIMEOUT", "300")

    cli._browser_login()

    assert captured["timeout_seconds"] == 300


def test_browser_login_ignores_invalid_timeout_env(monkeypatch):
    """A non-integer timeout env value is ignored (no timeout_seconds passed,
    so capture_session_via_browser uses its default)."""
    captured = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return {"session_id": "S", "csrftoken": "C"}

    monkeypatch.setattr("financial_mcp.monarch.browser_login.capture_session_via_browser", spy)
    monkeypatch.setenv("FINANCIAL_MCP_LOGIN_TIMEOUT", "not-a-number")

    cli._browser_login()

    assert "timeout_seconds" not in captured


def test_migrate_from_keyring_copies_and_clears(monkeypatch):
    class _KeyStore:
        def __init__(self):
            self.cleared = False

        def load_session(self):
            return {"session_id": "sid", "csrftoken": "csrf"}

        def delete_session(self):
            self.cleared = True

    dest = {}

    class _FileStore:
        def save_session(self, sid, csrf):
            dest["sid"], dest["csrf"] = sid, csrf

    key = _KeyStore()
    monkeypatch.setattr(cli, "_load_cfg", lambda: SimpleNamespace(credential_store="file"))
    monkeypatch.setattr(cli, "KeyringCredentialStore", lambda service: key)
    monkeypatch.setattr(cli, "make_credential_store", lambda cfg: _FileStore())
    monkeypatch.setattr(cli, "_service_name", lambda: "svc")

    cli._migrate_from_keyring()

    assert dest == {"sid": "sid", "csrf": "csrf"}
    assert key.cleared is True


def test_validate_surfaces_real_error(monkeypatch, capsys):
    class Boom(RuntimeError):
        pass

    def raiser(self):
        raise Boom("CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate")

    monkeypatch.setattr(
        "financial_mcp.monarch.community_adapter.CommunityAdapter.get_accounts", raiser
    )
    assert cli._validate({"session_id": "x", "csrftoken": "y"}) is False
    err = capsys.readouterr().err
    assert "Boom" in err
    assert "CERTIFICATE_VERIFY_FAILED" in err


def test_validate_expired_message(monkeypatch, capsys):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    known_message = "Monarch session expired — re-run `financial-mcp-login`."

    def raiser(self):
        raise SessionExpiredError(known_message)

    monkeypatch.setattr(
        "financial_mcp.monarch.community_adapter.CommunityAdapter.get_accounts", raiser
    )
    assert cli._validate({"session_id": "x", "csrftoken": "y"}) is False
    err = capsys.readouterr().err
    assert "expired" in err.lower()
    # Pin to the specific except (SessionExpiredError, ...) branch: it prints the
    # exception's own message verbatim. If that branch were ever dropped and
    # SessionExpiredError fell through to the generic `except Exception` handler,
    # the message would still contain "expired" (false pass) but would ALSO be
    # wrapped in "Couldn't verify the session: ..." — so asserting the wrapper's
    # absence is what actually catches that regression.
    assert known_message in err
    assert "Couldn't verify the session:" not in err


def test_migrate_from_keyring_refuses_when_dest_is_keyring(monkeypatch):
    """Guard: if config selects the keyring as the destination, migration would
    save then delete the same entry, losing the session — so it must refuse and
    never touch the keyring."""
    touched = {"loaded": False, "deleted": False}

    class _KeyStore:
        def load_session(self):
            touched["loaded"] = True
            return {"session_id": "sid", "csrftoken": "csrf"}

        def delete_session(self):
            touched["deleted"] = True

    monkeypatch.setattr(cli, "_load_cfg", lambda: SimpleNamespace(credential_store="keyring"))
    monkeypatch.setattr(cli, "KeyringCredentialStore", lambda service: _KeyStore())
    monkeypatch.setattr(cli, "_service_name", lambda: "svc")

    with pytest.raises(SystemExit, match="credential_store: keyring"):
        cli._migrate_from_keyring()

    assert touched == {"loaded": False, "deleted": False}
