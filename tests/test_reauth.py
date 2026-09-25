"""Tests for the in-Claude re-authentication path.

reauthenticate lets the user recover an expired Monarch session from inside
Claude (no Terminal trip): it drives the same browser sign-in the login CLI
uses, validates + saves the fresh session, then HOT-SWAPS a live adapter into
the running DataService so the fix takes effect in the current session.
"""
from types import SimpleNamespace

import pytest

import financial_mcp.reauth as reauth


class _RecordingAdapter:
    def __init__(self, name="fake"):
        self._name = name

    def source_name(self):
        return self._name


def test_reauthenticate_captures_saves_and_returns_live_adapter(monkeypatch):
    """Happy path: browser yields a session, validate passes, session is saved,
    and a live CommunityAdapter built from the new session is returned."""
    saved = {}

    monkeypatch.setattr(
        reauth, "_capture_browser_session",
        lambda cfg: {"session_id": "SID_NEW", "csrftoken": "CSRF_NEW"},
    )
    monkeypatch.setattr(reauth, "_validate", lambda session: True)

    class _Store:
        def save_session(self, sid, csrf):
            saved["sid"], saved["csrf"] = sid, csrf

    monkeypatch.setattr(reauth, "make_credential_store", lambda cfg: _Store())
    built = {}
    monkeypatch.setattr(
        reauth, "_build_live_adapter",
        lambda session: built.setdefault("adapter", _RecordingAdapter("live")),
    )
    monkeypatch.setattr(reauth, "_load_cfg", lambda: SimpleNamespace())

    adapter = reauth.reauthenticate()

    assert saved == {"sid": "SID_NEW", "csrf": "CSRF_NEW"}
    assert adapter is built["adapter"]
    assert adapter.source_name() == "live"


def test_capture_runs_off_the_event_loop(monkeypatch):
    """Root-cause regression: FastMCP invokes the tool ON a running asyncio loop,
    but sync Playwright refuses to run inside one ("Sync API inside the asyncio
    loop"). The browser capture must be offloaded to a worker thread so it runs
    where NO loop is visible. This asserts the capture callable sees no running
    loop even when reauthenticate() is driven from within a running loop."""
    import asyncio

    seen = {}

    def fake_capture(cfg):
        try:
            asyncio.get_running_loop()
            seen["loop_visible"] = True
        except RuntimeError:
            seen["loop_visible"] = False
        return {"session_id": "S", "csrftoken": "C"}

    monkeypatch.setattr(reauth, "_capture_browser_session", fake_capture)
    monkeypatch.setattr(reauth, "_validate", lambda session: True)

    class _Store:
        def save_session(self, sid, csrf):
            pass

    monkeypatch.setattr(reauth, "make_credential_store", lambda cfg: _Store())
    monkeypatch.setattr(reauth, "_build_live_adapter", lambda session: _RecordingAdapter("live"))
    monkeypatch.setattr(reauth, "_load_cfg", lambda: SimpleNamespace())

    async def drive():
        return reauth.reauthenticate()

    adapter = asyncio.run(drive())

    assert seen["loop_visible"] is False  # capture ran where sync Playwright works
    assert adapter.source_name() == "live"


def test_reauthenticate_raises_when_browser_cancelled(monkeypatch):
    """If the browser sign-in doesn't complete (returns None), reauth raises
    ReauthError and never saves a session."""
    saved = {"called": False}

    monkeypatch.setattr(reauth, "_capture_browser_session", lambda cfg: None)

    class _Store:
        def save_session(self, sid, csrf):
            saved["called"] = True

    monkeypatch.setattr(reauth, "make_credential_store", lambda cfg: _Store())
    monkeypatch.setattr(reauth, "_load_cfg", lambda: SimpleNamespace())

    with pytest.raises(reauth.ReauthError):
        reauth.reauthenticate()
    assert saved["called"] is False


def test_reauthenticate_raises_when_validation_fails(monkeypatch):
    """A captured-but-rejected session (e.g. still expired / cert problem) must
    NOT be saved and must raise ReauthError."""
    saved = {"called": False}

    monkeypatch.setattr(
        reauth, "_capture_browser_session",
        lambda cfg: {"session_id": "SID", "csrftoken": "CSRF"},
    )
    monkeypatch.setattr(reauth, "_validate", lambda session: False)

    class _Store:
        def save_session(self, sid, csrf):
            saved["called"] = True

    monkeypatch.setattr(reauth, "make_credential_store", lambda cfg: _Store())
    monkeypatch.setattr(reauth, "_load_cfg", lambda: SimpleNamespace())

    with pytest.raises(reauth.ReauthError):
        reauth.reauthenticate()
    assert saved["called"] is False
