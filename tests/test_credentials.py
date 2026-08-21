import json
import os
import stat

from financial_mcp.credentials import FileCredentialStore


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_file_save_load_roundtrip(tmp_path):
    store = FileCredentialStore(str(tmp_path / "sub" / "session.json"))
    assert store.load_session() is None
    store.save_session("sid-1", "csrf-1")
    assert store.load_session() == {"session_id": "sid-1", "csrftoken": "csrf-1"}


def test_file_perms_are_600_and_dir_700(tmp_path):
    p = tmp_path / "sub" / "session.json"
    store = FileCredentialStore(str(p))
    store.save_session("sid", "csrf")
    assert _mode(p) == 0o600
    assert _mode(p.parent) == 0o700


def test_file_loose_perms_tightened_on_load(tmp_path, caplog):
    p = tmp_path / "session.json"
    store = FileCredentialStore(str(p))
    store.save_session("sid", "csrf")
    os.chmod(p, 0o644)
    assert store.load_session() == {"session_id": "sid", "csrftoken": "csrf"}
    assert _mode(p) == 0o600


def test_file_missing_returns_none(tmp_path):
    assert FileCredentialStore(str(tmp_path / "nope.json")).load_session() is None


def test_file_corrupt_json_returns_none(tmp_path):
    p = tmp_path / "session.json"
    p.write_text("not-json", encoding="utf-8")
    assert FileCredentialStore(str(p)).load_session() is None


def test_file_missing_key_returns_none(tmp_path):
    p = tmp_path / "session.json"
    p.write_text(json.dumps({"session_id": "sid"}), encoding="utf-8")
    assert FileCredentialStore(str(p)).load_session() is None


def test_file_delete_removes_and_absent_is_noop(tmp_path):
    p = tmp_path / "session.json"
    store = FileCredentialStore(str(p))
    store.save_session("sid", "csrf")
    store.delete_session()
    assert not p.exists()
    store.delete_session()  # must not raise


def test_file_save_leaves_no_temp_file(tmp_path):
    p = tmp_path / "session.json"
    FileCredentialStore(str(p)).save_session("sid", "csrf")
    assert sorted(x.name for x in tmp_path.iterdir()) == ["session.json"]
