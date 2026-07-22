import pytest

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
