import financial_mcp.doctor as doc


def test_check_python_version_ok(monkeypatch):
    # Exercise the gate logic directly via a monkeypatched version_info,
    # rather than asserting on the ambient interpreter (this dev venv runs
    # Python 3.14, which is outside the supported 3.12/3.13 window by design
    # — end-user installs use Python 3.12 via the uv installer).
    class V:
        major, minor, micro = 3, 12, 5

    monkeypatch.setattr(doc.sys, "version_info", V())
    ok, msg = doc.check_python()
    assert ok is True
    assert "3.12" in msg


def test_check_python_version_gate_too_old(monkeypatch):
    class V:
        major, minor, micro = 3, 11, 9

    monkeypatch.setattr(doc.sys, "version_info", V())
    ok, msg = doc.check_python()
    assert ok is False
    assert "need 3.12 or 3.13" in msg


def test_check_python_version_gate_too_new(monkeypatch):
    class V:
        major, minor, micro = 3, 14, 0

    monkeypatch.setattr(doc.sys, "version_info", V())
    ok, msg = doc.check_python()
    assert ok is False
    assert "need 3.12 or 3.13" in msg


def test_session_check_reports_count_not_names(monkeypatch):
    class FakeAcct:
        name = "SECRET ACCOUNT"

    class FakeAdapter:
        def __init__(self, *a, **k): pass
        def get_accounts(self): return [FakeAcct(), FakeAcct()]

    monkeypatch.setattr(doc, "CommunityAdapter", FakeAdapter)
    monkeypatch.setattr(doc, "_load_session", lambda: {"session_id": "x", "csrftoken": "y"})
    ok, msg = doc.check_session()
    assert ok is True
    assert "2 account" in msg
    assert "SECRET ACCOUNT" not in msg


def test_session_check_no_session(monkeypatch):
    monkeypatch.setattr(doc, "_load_session", lambda: None)
    ok, msg = doc.check_session()
    assert ok is False
    assert "financial-mcp-login" in msg


def test_run_returns_nonzero_when_a_check_fails(monkeypatch):
    monkeypatch.setattr(doc, "check_python", lambda: (True, "ok"))
    monkeypatch.setattr(doc, "check_config", lambda: (True, "ok"))
    monkeypatch.setattr(doc, "check_secure_connection", lambda: (True, "ok"))
    monkeypatch.setattr(doc, "check_session", lambda: (False, "no session"))
    assert doc.run() != 0
