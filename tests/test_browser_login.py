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


# ── Orchestration tests (mocked) ───────────────────────────────────


def test_capture_timeout_path_raises_and_closes_context(monkeypatch):
    """When cookies never appear, capture_session_via_browser times out,
    raises BrowserLoginError, and still closes the context (finally runs)."""
    from financial_mcp.monarch import browser_login

    # Make monotonic advance past the deadline after two polls
    mono_seq = iter([0.0, 0.5, 1.5])  # third call exceeds timeout=1
    monkeypatch.setattr("time.monotonic", lambda: next(mono_seq))
    monkeypatch.setattr("time.sleep", lambda _: None)

    # Fake context that tracks close() and returns empty cookies
    fake_ctx_holder = {}

    class FakeContext:
        def __init__(self):
            self.closed = False
            self.pages = []
            fake_ctx_holder["ctx"] = self

        def new_page(self):
            return FakePage()

        def cookies(self, url):
            return []  # Never returns wanted cookies

        def close(self):
            self.closed = True

    class FakePage:
        def goto(self, url):
            pass

    class FakeChromium:
        def launch_persistent_context(self, profile_dir, **kwargs):
            return FakeContext()

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePlaywright())

    with pytest.raises(browser_login.BrowserLoginError, match="timed out"):
        browser_login.capture_session_via_browser(profile_dir="/tmp/fake", timeout_seconds=1)

    assert fake_ctx_holder["ctx"].closed, "context.close() was not called in finally block"


def test_capture_success_returns_cookies_and_closes_context(monkeypatch):
    """When cookies are present on first poll, capture returns them
    AND still closes the context."""
    from financial_mcp.monarch import browser_login

    monkeypatch.setattr("time.monotonic", lambda: 0.0)
    monkeypatch.setattr("time.sleep", lambda _: None)

    # Fake context that returns wanted cookies immediately
    fake_ctx_holder = {}

    class FakeContext:
        def __init__(self):
            self.closed = False
            self.pages = []
            fake_ctx_holder["ctx"] = self

        def new_page(self):
            return FakePage()

        def cookies(self, url):
            return [
                {"name": "session_id", "value": "SID_FAKE_123"},
                {"name": "csrftoken", "value": "CSRF_FAKE_456"},
            ]

        def close(self):
            self.closed = True

    class FakePage:
        def goto(self, url):
            pass

    class FakeChromium:
        def launch_persistent_context(self, profile_dir, **kwargs):
            return FakeContext()

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePlaywright())

    result = browser_login.capture_session_via_browser(profile_dir="/tmp/fake", timeout_seconds=1)

    assert result == {"session_id": "SID_FAKE_123", "csrftoken": "CSRF_FAKE_456"}
    assert fake_ctx_holder["ctx"].closed, "context.close() was not called after success"
