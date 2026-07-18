import keyring
from keyring.backends.fail import Keyring as FailKeyring


class MemoryKeyring(FailKeyring):
    """In-memory keyring backend for tests — no OS Keychain touched."""

    priority = 1  # type: ignore[assignment]

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        self._store.pop((service, username), None)


def _use_memory_backend():
    keyring.set_keyring(MemoryKeyring())


def test_save_and_load_roundtrip():
    _use_memory_backend()
    from financial_mcp.monarch import auth

    assert auth.load_token("svc") is None
    auth.save_token("tok-123", "svc")
    assert auth.load_token("svc") == "tok-123"


def test_delete_token():
    _use_memory_backend()
    from financial_mcp.monarch import auth

    auth.save_token("tok-123", "svc")
    auth.delete_token("svc")
    assert auth.load_token("svc") is None


def test_delete_absent_is_noop():
    _use_memory_backend()
    from financial_mcp.monarch import auth

    auth.delete_token("svc")  # must not raise
    assert auth.load_token("svc") is None
