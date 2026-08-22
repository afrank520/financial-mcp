# Credential Store (file-first, keyring opt-in) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store the Monarch session credential in a permission-locked local file by default (no OS-keyring prompt), with the OS keyring as a one-flag opt-in, behind a small swappable `CredentialStore` interface.

**Architecture:** Introduce `financial_mcp/credentials.py` with a `CredentialStore` protocol and two implementations — `FileCredentialStore` (default, `~/.financial_mcp/session.json`, `0o600`) and `KeyringCredentialStore` (today's keyring logic lifted unchanged). A `make_credential_store(cfg)` factory selects the backend from config. `main.py` and `login_cli.py` talk to the interface via the factory and stop importing `keyring`. `monarch/auth.py` is retained as thin re-exports over `KeyringCredentialStore` so legacy imports (and `community_adapter.py`, `test_auth.py`) keep working unchanged.

**Tech Stack:** Python 3.14, pydantic (config), `keyring` (opt-in backend only), pytest. POSIX file permissions via `os`/`stat`/`pathlib`.

**Spec:** docs/specs/2026-08-21-monarch-mcp-credential-store-design.md

## Global Constraints

- Test runner: `./.venv/bin/pytest` only. Baseline before Task 1: **277 passed**.
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **Default is file.** `Config.credential_store` defaults to `"file"`; a config that sets nothing gets the file store.
- **Byte-identical fence — do not edit these to make work "fit":** `tests/test_auth.py` stays green **unedited** (the `auth.py` re-exports must preserve today's keyring behavior and the `DEFAULT_SERVICE`/`SESSION_USERNAME` names). `load_session()` returns exactly `{"session_id": ..., "csrftoken": ...}` or `None` — same validated shape as today, so `main.py`'s "no session → CSV fallback" is unchanged.
- **File store security invariants (load-bearing):** parent dir created `0o700`; file written `0o600`; writes are atomic (`os.replace`); a file whose mode is looser than `0o600` on read is tightened to `0o600` (with a warning) before use; corrupt / missing / wrong-shape → `None` (fail-closed); **no secret values are ever written to logs** (paths, modes, outcomes only).
- No new third-party dependencies.

---

## File Structure

- **Create** `financial_mcp/credentials.py` — `CredentialStore` protocol, `FileCredentialStore`, `KeyringCredentialStore`, `make_credential_store`, `_validate`, `DEFAULT_SERVICE`, `SESSION_USERNAME`. One responsibility: app credential storage.
- **Rewrite** `financial_mcp/monarch/auth.py` — thin re-exports over `credentials` (legacy shim; keeps `test_auth.py` and `community_adapter.py` green).
- **Modify** `financial_mcp/config.py` — add `credential_store` and `credential_file` fields.
- **Modify** `financial_mcp/main.py` — `build_adapter` loads the session via `make_credential_store(cfg)`; drops the `auth` import.
- **Modify** `financial_mcp/login_cli.py` — save/delete via `make_credential_store(cfg)`; add `--migrate-from-keyring`.
- **Create** `tests/test_credentials.py` — file store, keyring store, factory.
- **Modify** `tests/test_main_adapter_selection.py`, `tests/test_login_cli.py`, `tests/test_config.py` — repoint to the new seam.
- **Modify** `README` (or docs entry) + `config.yaml` comment — honest trust model.

---

### Task 1: `FileCredentialStore` + `CredentialStore` protocol

**Files:**
- Create: `financial_mcp/credentials.py`
- Test: `tests/test_credentials.py`

**Interfaces:**
- Produces: `CredentialStore` protocol with `save_session(session_id: str, csrftoken: str) -> None`, `load_session() -> dict | None`, `delete_session() -> None`; `FileCredentialStore(path: str)`; module constant `SESSION_USERNAME = "monarch_session"`, `DEFAULT_SERVICE = "financial_mcp_monarch"`; helper `_validate(data) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_credentials.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_credentials.py -v`
Expected: FAIL with `ModuleNotFoundError: financial_mcp.credentials`

- [ ] **Step 3: Write the implementation**

```python
# financial_mcp/credentials.py
from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("financial_mcp")

DEFAULT_SERVICE = "financial_mcp_monarch"
SESSION_USERNAME = "monarch_session"


def _validate(data: object) -> dict | None:
    if not isinstance(data, dict) or "session_id" not in data or "csrftoken" not in data:
        return None
    return {"session_id": data["session_id"], "csrftoken": data["csrftoken"]}


class CredentialStore(Protocol):
    def save_session(self, session_id: str, csrftoken: str) -> None: ...
    def load_session(self) -> dict | None: ...
    def delete_session(self) -> None: ...


class FileCredentialStore:
    """Stores the session blob in a permission-locked local file (0600)."""

    def __init__(self, path: str) -> None:
        self._path = Path(path).expanduser()

    def save_session(self, session_id: str, csrftoken: str) -> None:
        blob = json.dumps({"session_id": session_id, "csrftoken": csrftoken})
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)  # best-effort; POSIX only
        except OSError:
            logger.warning("could not set 0700 on %s", parent)
        tmp = self._path.with_name(self._path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(blob)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load_session(self) -> dict | None:
        try:
            mode = stat.S_IMODE(self._path.stat().st_mode)
        except FileNotFoundError:
            return None
        if mode & 0o177:
            logger.warning(
                "credential file %s had loose permissions %o; tightening to 600",
                self._path, mode,
            )
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return _validate(data)

    def delete_session(self) -> None:
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_credentials.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/credentials.py tests/test_credentials.py
git commit -m "feat: FileCredentialStore + CredentialStore protocol (0600 file, atomic, fail-closed)"
```

---

### Task 2: `KeyringCredentialStore` + `auth.py` re-export shim

**Files:**
- Modify: `financial_mcp/credentials.py`
- Rewrite: `financial_mcp/monarch/auth.py`
- Test: `tests/test_credentials.py` (append)

**Interfaces:**
- Consumes: `_validate`, `DEFAULT_SERVICE`, `SESSION_USERNAME` from Task 1.
- Produces: `KeyringCredentialStore(service_name: str = DEFAULT_SERVICE)`. `auth.py` re-exports `save_session`, `load_session`, `delete_session`, `DEFAULT_SERVICE`, `SESSION_USERNAME` with today's signatures.

- [ ] **Step 1: Write the failing test** (append to `tests/test_credentials.py`)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_credentials.py::test_keyring_store_roundtrip_uses_backend -v`
Expected: FAIL with `ImportError: cannot import name 'KeyringCredentialStore'`

- [ ] **Step 3: Add `KeyringCredentialStore` to `credentials.py`**

Add `import keyring` at the top of `credentials.py` (with the other imports), then append:

```python
class KeyringCredentialStore:
    """Today's OS-keyring behavior, behind the CredentialStore interface."""

    def __init__(self, service_name: str = DEFAULT_SERVICE) -> None:
        self._service = service_name

    def save_session(self, session_id: str, csrftoken: str) -> None:
        blob = json.dumps({"session_id": session_id, "csrftoken": csrftoken})
        keyring.set_password(self._service, SESSION_USERNAME, blob)

    def load_session(self) -> dict | None:
        blob = keyring.get_password(self._service, SESSION_USERNAME)
        if not blob:
            return None
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            return None
        return _validate(data)

    def delete_session(self) -> None:
        try:
            keyring.delete_password(self._service, SESSION_USERNAME)
        except keyring.errors.PasswordDeleteError:
            pass  # already absent
```

- [ ] **Step 4: Rewrite `financial_mcp/monarch/auth.py` as a re-export shim**

```python
# financial_mcp/monarch/auth.py
# Legacy shim: credential storage moved to financial_mcp.credentials.
# Retained so existing imports (community_adapter, tests) keep working.
from __future__ import annotations

from financial_mcp.credentials import (
    DEFAULT_SERVICE,
    SESSION_USERNAME,
    KeyringCredentialStore,
)

__all__ = ["DEFAULT_SERVICE", "SESSION_USERNAME", "save_session", "load_session", "delete_session"]


def save_session(session_id: str, csrftoken: str, service_name: str = DEFAULT_SERVICE) -> None:
    KeyringCredentialStore(service_name).save_session(session_id, csrftoken)


def load_session(service_name: str = DEFAULT_SERVICE) -> dict | None:
    return KeyringCredentialStore(service_name).load_session()


def delete_session(service_name: str = DEFAULT_SERVICE) -> None:
    KeyringCredentialStore(service_name).delete_session()
```

- [ ] **Step 5: Run the new test AND the untouched auth regression suite**

Run: `./.venv/bin/pytest tests/test_credentials.py tests/test_auth.py -v`
Expected: PASS — `tests/test_auth.py` passes **unedited** (fence intact).

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/credentials.py financial_mcp/monarch/auth.py tests/test_credentials.py
git commit -m "feat: KeyringCredentialStore (opt-in) + auth.py legacy re-export shim"
```

---

### Task 3: Config fields + `make_credential_store` factory

**Files:**
- Modify: `financial_mcp/config.py:25-35`
- Modify: `financial_mcp/credentials.py`
- Test: `tests/test_credentials.py` (append), `tests/test_config.py`

**Interfaces:**
- Consumes: `FileCredentialStore`, `KeyringCredentialStore` (Tasks 1-2); `Config` fields below.
- Produces: `Config.credential_store: Literal["file","keyring"] = "file"`, `Config.credential_file: str = "~/.financial_mcp/session.json"`; `make_credential_store(cfg: Config) -> CredentialStore`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_credentials.py`)

```python
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
```

And append to `tests/test_config.py`:

```python
def test_credential_store_defaults_to_file():
    from financial_mcp.config import Config

    cfg = Config()
    assert cfg.credential_store == "file"
    assert cfg.credential_file == "~/.financial_mcp/session.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_credentials.py -k factory tests/test_config.py::test_credential_store_defaults_to_file -v`
Expected: FAIL (`credential_store` not a valid field / `make_credential_store` undefined)

- [ ] **Step 3: Add config fields**

In `financial_mcp/config.py`, inside `class Config`, after line 35 (`keyring_service_name`):

```python
    credential_store: Literal["file", "keyring"] = "file"
    credential_file: str = "~/.financial_mcp/session.json"
```

- [ ] **Step 4: Add the factory to `credentials.py`**

Append to `financial_mcp/credentials.py`:

```python
def make_credential_store(cfg) -> CredentialStore:
    """Build the configured credential store. Defaults to the file store."""
    if cfg.credential_store == "keyring":
        return KeyringCredentialStore(cfg.keyring_service_name)
    return FileCredentialStore(cfg.credential_file)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_credentials.py tests/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/config.py financial_mcp/credentials.py tests/test_credentials.py tests/test_config.py
git commit -m "feat: credential_store/credential_file config + make_credential_store factory"
```

---

### Task 4: Wire `main.py` to the factory

**Files:**
- Modify: `financial_mcp/main.py:11,29-36`
- Test: `tests/test_main_adapter_selection.py:22-45`

**Interfaces:**
- Consumes: `make_credential_store` (Task 3).
- Produces: `build_adapter(cfg)` loads the session via the store, not `auth.load_session`.

- [ ] **Step 1: Update the failing tests first** (`tests/test_main_adapter_selection.py`)

Replace the three `monkeypatch.setattr("financial_mcp.main.auth.load_session", ...)` lines so they patch the factory instead. Pattern for each:

```python
def test_live_with_session_builds_community_adapter(monkeypatch):
    class _Store:
        def load_session(self):
            return _SESSION
    monkeypatch.setattr("financial_mcp.main.make_credential_store", lambda cfg: _Store())
    # ... rest of the existing assertions unchanged ...
```

```python
def test_live_without_session_falls_back_to_csv(monkeypatch, caplog):
    class _Store:
        def load_session(self):
            return None
    monkeypatch.setattr("financial_mcp.main.make_credential_store", lambda cfg: _Store())
    # ... rest unchanged ...
```

```python
def test_live_without_session_and_no_csv_exits(monkeypatch):
    class _Store:
        def load_session(self):
            return None
    monkeypatch.setattr("financial_mcp.main.make_credential_store", lambda cfg: _Store())
    # ... rest unchanged ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_main_adapter_selection.py -v`
Expected: FAIL (`main` has no attribute `make_credential_store` yet)

- [ ] **Step 3: Update `main.py`**

Replace the import on line 11 (`from financial_mcp.monarch import auth`) with:

```python
from financial_mcp.credentials import make_credential_store
```

Replace `build_adapter` (lines 29-36):

```python
def build_adapter(cfg: Config) -> MonarchAdapter:
    if cfg.source == "live":
        session = make_credential_store(cfg).load_session()
        if session:
            return CommunityAdapter(session)
        logger.warning("no Monarch session — run `financial-mcp-login`; falling back to CSV")
        return _csv_or_exit(cfg)
    return _csv_or_exit(cfg)
```

(`CommunityAdapter(session)` — the `service_name` param keeps its default; `community_adapter.py` is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_main_adapter_selection.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/main.py tests/test_main_adapter_selection.py
git commit -m "feat: main.build_adapter loads session via make_credential_store (file-first)"
```

---

### Task 5: Wire `login_cli.py` (+ `--migrate-from-keyring`) + honest docs

**Files:**
- Modify: `financial_mcp/login_cli.py:9,14-16,143,147-152`
- Test: `tests/test_login_cli.py:49-55` (and add a migration test)
- Modify: `README.md` (or the project's docs entry) + `config.yaml` comment

**Interfaces:**
- Consumes: `make_credential_store` (Task 3), `KeyringCredentialStore` (Task 2).
- Produces: `financial-mcp-login` saves/deletes via the configured store; `--migrate-from-keyring` copies a keyring session into the file store and clears the keyring entry.

- [ ] **Step 1: Read `login_cli.py` in full** to see `_login`, `_service_name`, and `main`'s arg handling before editing (the exact surrounding lines vary).

- [ ] **Step 2: Update the failing tests first** (`tests/test_login_cli.py`)

The existing `_patch_tail` patches `cli.auth.save_session` and `cli._service_name`. Repoint it to patch a store the CLI builds. Replace `_patch_tail` so it captures saves against a fake store:

```python
def _patch_tail(monkeypatch):
    import financial_mcp.login_cli as cli
    monkeypatch.setattr(cli, "_validate", lambda session: True)
    saved = {}
    class _Store:
        def save_session(self, sid, csrf):
            saved["sid"], saved["csrf"] = sid, csrf
        def delete_session(self):
            saved["deleted"] = True
    monkeypatch.setattr(cli, "make_credential_store", lambda cfg: _Store())
    return saved
```

Update any existing assertion that checked `auth.save_session` was called to instead assert on the returned `saved` dict. Add a migration test:

```python
def test_migrate_from_keyring_copies_and_clears(monkeypatch):
    import financial_mcp.login_cli as cli

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
    monkeypatch.setattr(cli, "KeyringCredentialStore", lambda service: key)
    monkeypatch.setattr(cli, "make_credential_store", lambda cfg: _FileStore())
    monkeypatch.setattr(cli, "_service_name", lambda: "svc")

    cli._migrate_from_keyring()

    assert dest == {"sid": "sid", "csrf": "csrf"}
    assert key.cleared is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_login_cli.py -v`
Expected: FAIL (`login_cli` has no `make_credential_store` / `_migrate_from_keyring`)

- [ ] **Step 4: Update `login_cli.py`**

Change the import on line 9 from `from financial_mcp.monarch import auth` to:

```python
from financial_mcp.credentials import KeyringCredentialStore, make_credential_store
```

Keep `_service_name()` as-is (still reads `cfg.keyring_service_name`). Add a small helper to build the configured store, and rewrite the save/delete/migrate call sites. Replace the save call (line 143):

```python
    make_credential_store(_load_cfg()).save_session(session["session_id"], session["csrftoken"])
```

where `_load_cfg()` is the config loader `_service_name()` already uses internally — factor it out so both share it:

```python
def _load_cfg():
    from financial_mcp.config import load_config
    import os
    return load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))


def _service_name() -> str:
    return _load_cfg().keyring_service_name
```

Add the migration helper:

```python
def _migrate_from_keyring() -> None:
    src = KeyringCredentialStore(_service_name())  # one keyring read (may prompt once)
    session = src.load_session()
    if not session:
        print("No keyring session found to migrate.")
        return
    make_credential_store(_load_cfg()).save_session(session["session_id"], session["csrftoken"])
    src.delete_session()
    print("Migrated session from keyring to the file store; keyring entry removed.")
```

Rewrite `main()` (lines 147-152) to route the flags:

```python
def main() -> None:
    if "--migrate-from-keyring" in sys.argv:
        _migrate_from_keyring()
        return
    if "--logout" in sys.argv:
        make_credential_store(_load_cfg()).delete_session()
        return
    _login(force_curl="--curl" in sys.argv)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_login_cli.py -v`
Expected: PASS

- [ ] **Step 6: Add the honest trust-model docs**

In `README.md` add a "Where your credentials live" section (create the heading if absent):

```markdown
### Where your credentials live

Your Monarch session is stored in a file only your user account can read
(`~/.financial_mcp/session.json`, permissions `600`) — the same way the GitHub
CLI stores its token. This protects against accidental sharing and other users
on your computer. It is **not** encrypted at rest; rely on your disk encryption
(e.g. FileVault) to protect a lost or stolen machine.

For OS-managed encryption at rest, set `credential_store: keyring` in your
config (macOS may prompt once to allow access). To move an existing keyring
session into the file store, run `financial-mcp-login --migrate-from-keyring`.
```

In `config.yaml`, add a comment beside the credential settings (leave `credential_store` unset to keep the file default):

```yaml
# credentials: file store by default (~/.financial_mcp/session.json, chmod 600).
# Set credential_store: keyring for OS-keyring storage (may prompt once on macOS).
# credential_store: file
# credential_file: ~/.financial_mcp/session.json
```

- [ ] **Step 7: Commit**

```bash
git add financial_mcp/login_cli.py tests/test_login_cli.py README.md config.yaml
git commit -m "feat: login_cli saves via configured store + --migrate-from-keyring; honest credential docs"
```

---

### Task 6: Full-suite green + no stray `keyring`/`auth` imports outside the shim

**Files:**
- Verify only (no new code unless a gap is found).

- [ ] **Step 1: Run the whole suite**

Run: `./.venv/bin/pytest -q`
Expected: PASS — **277 baseline + the new `test_credentials.py` tests** (≈ 289+), zero failures. `tests/test_auth.py` unedited and green.

- [ ] **Step 2: Confirm the seam is clean**

Run: `grep -rn "import keyring\|monarch import auth\|auth.load_session\|auth.save_session\|auth.delete_session" financial_mcp | grep -v "credentials.py" | grep -v "monarch/auth.py"`
Expected: no output except `community_adapter.py`'s `from financial_mcp.monarch import auth` (which uses only `auth.DEFAULT_SERVICE`, satisfied by the shim). If `main.py` or `login_cli.py` still import `auth`, fix them.

- [ ] **Step 3: Commit (only if a gap was fixed)**

```bash
git add -A
git commit -m "chore: confirm credential-store seam; no direct keyring/auth use outside the shim"
```
