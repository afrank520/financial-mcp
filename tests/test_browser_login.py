import pytest

from financial_mcp.monarch.browser_login import extract_wanted_cookies

# Playwright's context.cookies() returns a list of dicts with these keys.
# FAKE values — safe to commit.
_COOKIES = [
    {"name": "ajs_anonymous_id", "value": "xxx", "domain": ".monarch.com"},
    {"name": "session_id", "value": "SID_FAKE_123", "domain": ".monarch.com"},
    {"name": "csrftoken", "value": "CSRF_FAKE_456", "domain": ".monarch.com"},
    {"name": "_fbp", "value": "zzz", "domain": ".monarch.com"},
]


def test_extract_returns_both_cookies():
    got = extract_wanted_cookies(_COOKIES)
    assert got == {"session_id": "SID_FAKE_123", "csrftoken": "CSRF_FAKE_456"}


def test_extract_missing_session_id_raises():
    cookies = [{"name": "csrftoken", "value": "CSRF_FAKE_456"}]
    with pytest.raises(ValueError, match="session_id"):
        extract_wanted_cookies(cookies)


def test_extract_missing_csrftoken_raises():
    cookies = [{"name": "session_id", "value": "SID_FAKE_123"}]
    with pytest.raises(ValueError, match="csrftoken"):
        extract_wanted_cookies(cookies)


def test_extract_empty_value_raises():
    cookies = [
        {"name": "session_id", "value": ""},
        {"name": "csrftoken", "value": "CSRF_FAKE_456"},
    ]
    with pytest.raises(ValueError, match="session_id"):
        extract_wanted_cookies(cookies)


def test_extract_empty_list_raises():
    with pytest.raises(ValueError, match="session_id"):
        extract_wanted_cookies([])
