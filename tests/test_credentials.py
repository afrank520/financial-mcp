import json
import logging
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
    with caplog.at_level(logging.WARNING, logger="financial_mcp"):
        assert store.load_session() == {"session_id": "sid", "csrftoken": "csrf"}
    assert _mode(p) == 0o600
    assert "loose permissions" in caplog.text


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


def test_keyring_store_roundtrip_uses_backend():
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    class Mem(FailKeyring):
        priority = 1
        def __init__(self):
            self._s = {}
        def get_password(self, service, username):
            return self._s.get((service, username))
        def set_password(self, service, username, password):
            self._s[(service, username)] = password
        def delete_password(self, service, username):
            self._s.pop((service, username), None)

    keyring.set_keyring(Mem())
    from financial_mcp.credentials import KeyringCredentialStore

    store = KeyringCredentialStore("svc")
    assert store.load_session() is None
    store.save_session("sid", "csrf")
    assert store.load_session() == {"session_id": "sid", "csrftoken": "csrf"}
    store.delete_session()
    assert store.load_session() is None
    store.delete_session()  # absent → no raise


def test_factory_defaults_to_file(tmp_path):
    from financial_mcp.config import Config
    from financial_mcp.credentials import FileCredentialStore, make_credential_store

    store = make_credential_store(Config(credential_file=str(tmp_path / "s.json")))
    assert isinstance(store, FileCredentialStore)


def test_factory_keyring_when_selected():
    from financial_mcp.config import Config
    from financial_mcp.credentials import KeyringCredentialStore, make_credential_store

    store = make_credential_store(Config(credential_store="keyring", keyring_service_name="svc"))
    assert isinstance(store, KeyringCredentialStore)


def test_factory_file_uses_configured_path(tmp_path):
    from financial_mcp.config import Config
    from financial_mcp.credentials import make_credential_store

    target = tmp_path / "custom.json"
    store = make_credential_store(Config(credential_file=str(target)))
    store.save_session("sid", "csrf")
    assert target.exists()
