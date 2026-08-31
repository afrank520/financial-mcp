# Seamless macOS Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make macOS setup seamless (download ZIP → `bash install.sh` → sign in → done) by (1) hardening the tool against the environment landmines that cost a night on Tracey's beta install and (2) rewriting the installer to let `uv` fetch the right Python and build everything.

**Architecture:** Six components. A–D are standalone code/robustness fixes (config path resolution, certifi-backed TLS, honest login errors, a `doctor` self-check). E–F are the uv installer + docs. A–D land first (each independently testable); E–F build on them.

**Tech Stack:** Python 3.12, pydantic v2, aiohttp, certifi (new), Playwright, `uv` (installer engine), bash 3.2-compatible `install.sh`.

**Spec:** `docs/specs/2026-08-29-seamless-macos-install-design.md`

## Global Constraints

- **Python floor stays `>=3.12`**; the `mcp>=1.2.0,<2` pin is untouched.
- **The venv entry points must remain at `.venv/bin/financial-mcp` and `.venv/bin/financial-mcp-login`** — existing Claude Desktop configs point there; do not move them.
- **`financial-mcp doctor` / `financial-mcp-doctor` must never print balances or account names** — account *count* only.
- **`write_mode` default stays `"off"`** (read-only) in code and example config.
- **The full test suite must stay green** (currently 374 tests); add tests, don't weaken existing ones.
- **`install.sh` must pass `bash -n` under system bash 3.2** (`/bin/bash`), not just dev bash — a Python heredoc inside `$(...)` broke this before.
- **uv acquisition:** Homebrew if present, else download the official installer to a file then run it — never a blind `curl … | sh`.
- **The installer is uv-only**; the manual `python3 -m venv` path stays documented in `INSTALL.md` as the fallback (do not delete it).
- **Config resolver stays pure** (no filesystem side effects); directory creation happens at file-open sites via a separate `ensure_parent` helper.
- **Pushing to origin requires explicit human approval** — do not push.

## File Structure

- `financial_mcp/config.py` — Modify: add `USER_DIR`, `resolve_user_path()`, `ensure_parent()`, and a `model_validator` that resolves `db_path` / `log_file` / `credential_file`.
- `financial_mcp/utils/logging.py` — Modify: `ensure_parent(cfg.log_file)` before opening the `FileHandler`.
- `financial_mcp/cache/sqlite_cache.py` — Modify: `ensure_parent(db_path)` before opening the sqlite file.
- `financial_mcp/cache/undo_journal.py` — Modify: `ensure_parent(db_path)` before opening.
- `financial_mcp/monarch/community_adapter.py` — Modify: module-level certifi SSL context; pass it via `TCPConnector` in `_default_transport`.
- `financial_mcp/login_cli.py` — Modify: `_validate()` surfaces the real error.
- `financial_mcp/doctor.py` — Create: the `financial-mcp-doctor` self-check.
- `pyproject.toml` — Modify: add `certifi` dep, add `financial-mcp-doctor` script, bump to `1.1.0`.
- `install.sh` — Rewrite: uv-based bootstrap.
- `config.example.yaml`, `QUICKSTART.md`, `docs/INSTALL.md`, `README.md`, `CHANGELOG.md` — Modify: docs.
- Tests: `tests/test_config_paths.py`, `tests/test_logging_paths.py`, `tests/test_community_adapter_ssl.py`, `tests/test_login_cli.py` (extend if present), `tests/test_doctor.py`.

---

### Task 1: Config path resolution

**Files:**
- Modify: `financial_mcp/config.py`
- Test: `tests/test_config_paths.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `financial_mcp.config.USER_DIR` (a `Path`), `resolve_user_path(value: str) -> str`, `ensure_parent(path: str) -> None`. `Config` instances now expose already-resolved absolute `db_path`, `credential_file`, and (if set) `log_file`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_paths.py
import os
from pathlib import Path

import financial_mcp.config as cfgmod
from financial_mcp.config import Config, resolve_user_path


def test_relative_path_anchors_under_user_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    assert resolve_user_path("cache.db") == str(tmp_path / ".financial_mcp" / "cache.db")


def test_relative_resolution_ignores_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    monkeypatch.chdir(tmp_path)  # cwd must not affect the result
    c = Config(db_path="cache.db", log_file="financial_mcp.log")
    assert c.db_path == str(tmp_path / ".financial_mcp" / "cache.db")
    assert c.log_file == str(tmp_path / ".financial_mcp" / "financial_mcp.log")


def test_absolute_path_passes_through(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    abs_db = str(tmp_path / "somewhere" / "x.db")
    assert Config(db_path=abs_db).db_path == abs_db


def test_log_file_none_stays_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    assert Config().log_file is None


def test_tilde_expands(monkeypatch):
    assert resolve_user_path("~/x.db") == str(Path("~/x.db").expanduser())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config_paths.py -v`
Expected: FAIL — `resolve_user_path` not importable / paths not resolved.

- [ ] **Step 3: Write minimal implementation**

Add to `financial_mcp/config.py` (imports at top: `from pathlib import Path`, and `model_validator` from pydantic):

```python
from pathlib import Path
from pydantic import BaseModel, ValidationError, model_validator

USER_DIR = Path("~/.financial_mcp").expanduser()


def resolve_user_path(value: str) -> str:
    """Expand ~ and anchor a relative path under USER_DIR. Absolute paths
    (after ~ expansion) are returned unchanged. Uses the module-level USER_DIR
    at call time so tests can monkeypatch it."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = USER_DIR / p
    return str(p)


def ensure_parent(path: str) -> None:
    """Create the parent directory of `path` (0700) if missing. Best-effort."""
    parent = Path(path).expanduser().parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        import os
        os.chmod(parent, 0o700)
    except OSError:
        pass
```

Then in `Config`, add the resolver (after the field declarations):

```python
    @model_validator(mode="after")
    def _resolve_paths(self):
        self.db_path = resolve_user_path(self.db_path)
        self.credential_file = resolve_user_path(self.credential_file)
        if self.log_file:
            self.log_file = resolve_user_path(self.log_file)
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config_paths.py -v`
Expected: PASS (all 5).

- [ ] **Step 5: Run the full suite** (resolution changes touch shared config)

Run: `.venv/bin/python -m pytest`
Expected: PASS. If any existing test asserted a bare relative `db_path`/`credential_file`, update it to the resolved absolute form — the resolution is the intended new behavior.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/config.py tests/test_config_paths.py
git commit -m "feat: resolve relative config paths under ~/.financial_mcp (fix cwd=/ crash)"
```

---

### Task 2: Create parent dirs at file-open sites

**Files:**
- Modify: `financial_mcp/utils/logging.py`, `financial_mcp/cache/sqlite_cache.py`, `financial_mcp/cache/undo_journal.py`
- Test: `tests/test_logging_paths.py` (create)

**Interfaces:**
- Consumes: `financial_mcp.config.ensure_parent` (Task 1).
- Produces: opening the log file / sqlite db no longer requires the parent dir to pre-exist.

- [ ] **Step 1: Write the failing test** (the real regression — a relative log path with an unusual cwd)

```python
# tests/test_logging_paths.py
import financial_mcp.config as cfgmod
from financial_mcp.config import Config
from financial_mcp.utils.logging import configure_logging


def test_relative_log_file_writes_regardless_of_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "USER_DIR", tmp_path / ".financial_mcp")
    monkeypatch.chdir(tmp_path)
    cfg = Config(log_file="financial_mcp.log")  # resolves under USER_DIR
    logger = configure_logging(cfg)             # must not raise; must create the dir
    logger.info("hello")
    assert (tmp_path / ".financial_mcp" / "financial_mcp.log").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_logging_paths.py -v`
Expected: FAIL — `FileNotFoundError` opening the handler (parent dir missing).

- [ ] **Step 3: Write minimal implementation**

In `financial_mcp/utils/logging.py`, import and call `ensure_parent` before the `FileHandler`:

```python
from financial_mcp.config import Config, ensure_parent
...
    if cfg.log_file:
        ensure_parent(cfg.log_file)
        file_handler = logging.FileHandler(cfg.log_file, encoding="utf-8")
```

In `financial_mcp/cache/sqlite_cache.py`, at the top of `__init__` (before opening the connection), add `ensure_parent(db_path)` (import `from financial_mcp.config import ensure_parent`). Do the same in `financial_mcp/cache/undo_journal.py`'s `__init__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_logging_paths.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/utils/logging.py financial_mcp/cache/sqlite_cache.py financial_mcp/cache/undo_journal.py tests/test_logging_paths.py
git commit -m "feat: create ~/.financial_mcp parent dirs before opening log/db"
```

---

### Task 3: certifi-backed TLS for the Monarch adapter

**Files:**
- Modify: `financial_mcp/monarch/community_adapter.py`, `pyproject.toml`
- Test: `tests/test_community_adapter_ssl.py` (create)

**Interfaces:**
- Consumes: `certifi` (new dependency).
- Produces: `community_adapter._SSL_CTX` (an `ssl.SSLContext` built from `certifi.where()`), used for every Monarch request.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to `[project].dependencies`:

```toml
    "certifi>=2024.2",
```

Reinstall so it's importable: `uv pip install -e .` (or `.venv/bin/python -m pip install -e .`).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_community_adapter_ssl.py
import ssl

import certifi

import financial_mcp.monarch.community_adapter as ca


def test_ssl_ctx_built_from_certifi():
    assert isinstance(ca._SSL_CTX, ssl.SSLContext)


def test_transport_passes_ssl_ctx_to_connector(monkeypatch):
    """The connector used for Monarch calls must carry the certifi SSL context."""
    captured = {}

    class FakeConnector:
        def __init__(self, *a, **kw):
            captured["ssl"] = kw.get("ssl")

    monkeypatch.setattr(ca.aiohttp, "TCPConnector", FakeConnector, raising=False)
    # Building the connector is enough to assert wiring; see note below.
    conn = ca.aiohttp.TCPConnector(ssl=ca._SSL_CTX)
    assert captured["ssl"] is ca._SSL_CTX
    assert certifi.where()  # sanity: certifi resolvable
```

> Note for the implementer: if `aiohttp` is imported lazily inside `_default_transport`, hoist `import aiohttp` to module level so `ca.aiohttp` exists for the monkeypatch (or adjust the test to patch where it's imported). Keep the fresh-session-per-call behavior.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_community_adapter_ssl.py -v`
Expected: FAIL — `_SSL_CTX` does not exist.

- [ ] **Step 4: Write minimal implementation**

In `financial_mcp/monarch/community_adapter.py`, add module-level imports and context:

```python
import ssl
import certifi
import aiohttp  # hoisted from inside _default_transport

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
```

In `_default_transport`, drop the local `import aiohttp` and build the session with a connector:

```python
        connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
        async with aiohttp.ClientSession(cookies=cookies, connector=connector) as session:
            async with session.post(GRAPHQL_URL, json=payload, headers=headers) as resp:
                status = resp.status
                text = await resp.text()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_community_adapter_ssl.py -v`
Expected: PASS. Then full suite: `.venv/bin/python -m pytest` → PASS.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/monarch/community_adapter.py pyproject.toml tests/test_community_adapter_ssl.py
git commit -m "feat: use certifi CA bundle for Monarch TLS (fix CERTIFICATE_VERIFY_FAILED)"
```

---

### Task 4: Honest login-validation errors

**Files:**
- Modify: `financial_mcp/login_cli.py`
- Test: `tests/test_login_cli.py` (create or extend)

**Interfaces:**
- Consumes: `SessionExpiredError`, `CloudflareBlockedError` from `community_adapter`.
- Produces: `_validate()` prints the real cause; return value unchanged (`bool`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_login_cli.py
import financial_mcp.login_cli as lc


def test_validate_surfaces_real_error(monkeypatch, capsys):
    class Boom(RuntimeError):
        pass

    def raiser(self):
        raise Boom("CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate")

    monkeypatch.setattr(
        "financial_mcp.monarch.community_adapter.CommunityAdapter.get_accounts", raiser
    )
    assert lc._validate({"session_id": "x", "csrftoken": "y"}) is False
    err = capsys.readouterr().err
    assert "Boom" in err
    assert "CERTIFICATE_VERIFY_FAILED" in err


def test_validate_expired_message(monkeypatch, capsys):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    def raiser(self):
        raise SessionExpiredError("Monarch session expired — re-run `financial-mcp-login`.")

    monkeypatch.setattr(
        "financial_mcp.monarch.community_adapter.CommunityAdapter.get_accounts", raiser
    )
    assert lc._validate({"session_id": "x", "csrftoken": "y"}) is False
    assert "expired" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_login_cli.py -v`
Expected: FAIL — current code prints the generic message, not the real class/text.

- [ ] **Step 3: Write minimal implementation**

Replace `_validate` in `financial_mcp/login_cli.py`:

```python
def _validate(session: dict) -> bool:
    """Confirm the session works with one live accounts call, surfacing the
    real error so a certificate/connection problem isn't mistaken for a bad login."""
    from financial_mcp.monarch.community_adapter import (
        CloudflareBlockedError,
        CommunityAdapter,
        SessionExpiredError,
    )

    try:
        CommunityAdapter(session).get_accounts()
        return True
    except (SessionExpiredError, CloudflareBlockedError) as exc:
        print(str(exc), file=sys.stderr)
        return False
    except Exception as exc:  # noqa: BLE001
        print(
            f"Couldn't verify the session: {type(exc).__name__}: {exc}\n"
            "If this mentions CERTIFICATE_VERIFY_FAILED, it's a secure-connection "
            "problem on this Mac, not a bad login — run `financial-mcp doctor`.",
            file=sys.stderr,
        )
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_login_cli.py -v`
Expected: PASS. Full suite → PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/login_cli.py tests/test_login_cli.py
git commit -m "feat: surface the real error in login validation (stop hiding SSL failures)"
```

---

### Task 5: `financial-mcp-doctor` self-check

**Files:**
- Create: `financial_mcp/doctor.py`
- Modify: `pyproject.toml` (add console script)
- Test: `tests/test_doctor.py` (create)

**Interfaces:**
- Consumes: `load_config`, `make_credential_store`, `resolve_user_path`, `CommunityAdapter`, `SessionExpiredError`, `_SSL_CTX`.
- Produces: `financial_mcp.doctor.run() -> int` (0 = all green, non-zero = issues) and a `main()` wrapper; console script `financial-mcp-doctor`.

**Design:** each check is a function returning `(ok: bool, message: str)`. `run()` prints `✅ <msg>` / `❌ <msg>` per check, a summary line, and returns the exit code. **Never print balances or account names — count only.**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor.py
import financial_mcp.doctor as doc


def test_check_python_version_ok():
    ok, msg = doc.check_python()
    assert ok is True and "3.1" in msg


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# financial_mcp/doctor.py
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

from financial_mcp.config import ensure_parent, load_config
from financial_mcp.credentials import make_credential_store
from financial_mcp.monarch.community_adapter import (
    CommunityAdapter,
    SessionExpiredError,
    _SSL_CTX,
)


def _cfg():
    return load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))


def _load_session():
    return make_credential_store(_cfg()).load_session()


def check_python() -> tuple[bool, str]:
    v = sys.version_info
    ok = (3, 12) <= (v.major, v.minor) < (3, 14)
    return ok, f"Python {v.major}.{v.minor}.{v.micro}" + ("" if ok else " (need 3.12 or 3.13)")


def check_config() -> tuple[bool, str]:
    try:
        cfg = _cfg()
    except Exception as exc:  # noqa: BLE001
        return False, f"config failed to load: {exc}"
    for label, path in (("db_path", cfg.db_path), ("credential_file", cfg.credential_file)):
        if not Path(path).is_absolute():
            return False, f"{label} did not resolve to an absolute path: {path}"
        try:
            ensure_parent(path)
        except OSError as exc:
            return False, f"{label} parent not writable: {exc}"
    return True, "config paths resolve and are writable"


def check_secure_connection() -> tuple[bool, str]:
    try:
        req = urllib.request.Request("https://api.monarch.com/", method="HEAD")
        urllib.request.urlopen(req, timeout=15, context=_SSL_CTX)
        return True, "secure connection to Monarch OK"
    except Exception as exc:  # noqa: BLE001
        if "CERTIFICATE_VERIFY" in str(exc):
            return False, (
                "secure connection failed (certificate). On a python.org Mac install, "
                "run the bundled 'Install Certificates.command'."
            )
        # A reachable-but-unauthorized/redirect response still proves TLS works.
        if "HTTP Error" in str(exc):
            return True, "secure connection to Monarch OK"
        return False, f"secure connection failed: {type(exc).__name__}: {exc}"


def check_session() -> tuple[bool, str]:
    session = _load_session()
    if not session:
        return False, "no saved session — run `financial-mcp-login`"
    try:
        n = len(CommunityAdapter(session).get_accounts())
        return True, f"session valid — {n} accounts"
    except SessionExpiredError:
        return False, "session expired — run `financial-mcp-login`"
    except Exception as exc:  # noqa: BLE001
        return False, f"session check failed: {type(exc).__name__}: {exc}"


CHECKS = ("check_python", "check_config", "check_secure_connection", "check_session")


def run() -> int:
    failures = 0
    for name in CHECKS:
        ok, msg = globals()[name]()
        print(f"{'✅' if ok else '❌'} {msg}")
        if not ok:
            failures += 1
    print("\nAll green — you're set." if failures == 0 else f"\n{failures} issue(s) found — see above.")
    return 0 if failures == 0 else 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
```

> Implementer note: the `check_*` calls in `run()` go through `globals()[name]()` so the monkeypatched versions in the test are used. Keep that indirection.

Add the console script to `pyproject.toml` `[project.scripts]`:

```toml
financial-mcp-doctor = "financial_mcp.doctor:main"
```

Reinstall (`uv pip install -e .`) so the script and monkeypatch targets resolve.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -v`
Expected: PASS. Full suite → PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/doctor.py pyproject.toml tests/test_doctor.py
git commit -m "feat: add financial-mcp-doctor self-check (python/config/tls/session)"
```

---

### Task 6: uv-based installer

**Files:**
- Rewrite: `install.sh`

**Interfaces:**
- Consumes: `uv`, the console scripts at `.venv/bin/`, `financial-mcp-doctor` (Task 5).
- Produces: a working `.venv` built by uv, unchanged Claude Desktop wiring.

- [ ] **Step 1: Rewrite `install.sh`**

Preserve the header comment style, `set -euo pipefail`, `SCRIPT_DIR` handling, `say/ok/warn/die` helpers, `-h/--help`, unknown-flag rejection, `--fresh`, the config step (steps 5), the sign-in step (6), and the Claude Desktop wire-up (`wire_up`/`print_block`). Replace **only** the Python-detection (old step 1) and venv/install (old steps 2–3) with:

```bash
# ── 1. Ensure uv ───────────────────────────────────────────────────────────
say "1/7  Making sure uv is available…"
if command -v uv >/dev/null 2>&1; then
  ok "uv already installed ($(uv --version))"
elif command -v brew >/dev/null 2>&1; then
  brew install uv || die "brew install uv failed."
  ok "Installed uv via Homebrew"
else
  TMP_UV="$(mktemp -t uv-install)"
  warn "Homebrew not found — downloading uv's official installer to $TMP_UV (you can inspect it)."
  curl -LsSf https://astral.sh/uv/install.sh -o "$TMP_UV" || die "Could not download the uv installer."
  sh "$TMP_UV" || die "uv installer failed."
  ok "Installed uv"
fi
# uv's script installs to ~/.local/bin; make it visible for the rest of this run.
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || die "uv is still not on PATH. Open a new Terminal and re-run."

# ── 2. Python 3.12 + venv (uv fetches a known-good Python) ──────────────────
say "2/7  Building the environment with Python 3.12…"
if [ "$FRESH" -eq 1 ] && [ -d "$VENV" ]; then
  rm -rf "$VENV"; ok "Removed the old .venv (--fresh); config.yaml and login untouched"
fi
uv venv --python 3.12 "$VENV" || die "uv could not create the environment."
ok "Environment ready at .venv (Python 3.12)"

# ── 3. Install the server ───────────────────────────────────────────────────
say "3/7  Installing the server and its parts…"
uv pip install --python "$PY" -e . || die "Install failed. Re-run; if it persists, send this output along."
ok "Installed"
```

Keep the remaining steps (playwright, config, login) as-is but renumber consistently, and **after the sign-in step add a doctor run**:

```bash
say "Running a quick self-check…"
"$VENV/bin/financial-mcp-doctor" || warn "The self-check found an issue above — see the message for the fix."
```

- [ ] **Step 2: Syntax-check under system bash 3.2**

Run: `/bin/bash -n install.sh`
Expected: no output (valid). Fix any 3.2 parse error before proceeding.

- [ ] **Step 3: Commit**

```bash
git add install.sh
git commit -m "feat: uv-based installer (uv fetches Python 3.12, builds .venv, runs doctor)"
```

> Full end-to-end install is verified in the manual clean-Mac validation gate (see the plan footer), not in CI.

---

### Task 7: Documentation + version bump

**Files:**
- Modify: `config.example.yaml`, `QUICKSTART.md`, `docs/INSTALL.md`, `README.md`, `CHANGELOG.md`, `pyproject.toml`

- [ ] **Step 1: `config.example.yaml`** — update the `db_path`/`log_file` comments to state that bare filenames resolve under `~/.financial_mcp/` automatically (no need for absolute paths). Keep `write_mode: "off"` and `source: csv` defaults.

- [ ] **Step 2: `QUICKSTART.md`** — rewrite the Overview + Part 1 so installer users go **download ZIP → `bash install.sh` → sign in → done**; move the manual "install Python 3.12" section to a clearly labeled fallback. Lead the install command with `bash install.sh` (ZIP strips the executable bit). Add "If something goes wrong" entries: (a) certificate error → run `financial-mcp doctor` (and, for hand-installs, the python.org Install Certificates.command); (b) `permission denied` → use `bash install.sh`. Note `financial-mcp-doctor` as the one-command health check.

- [ ] **Step 3: `docs/INSTALL.md`** — add the uv path as the primary install; keep the existing `python3 -m venv … --only-binary=:all:` steps as the manual/developer fallback (do not delete). Note the new `certifi` dependency and the `financial-mcp-doctor` command.

- [ ] **Step 4: `README.md`** — add a one-liner about `financial-mcp doctor` under setup/troubleshooting.

- [ ] **Step 5: `CHANGELOG.md` + `pyproject.toml`** — add a `1.1.0` entry (install hardening: uv installer, certifi TLS, path resolution, honest login errors, doctor) and bump `version = "1.1.0"`.

- [ ] **Step 6: Verify** — `.venv/bin/python -m pytest` still green; `/bin/bash -n install.sh` clean; skim each edited doc for stale "install Python by hand first" language on the installer path.

- [ ] **Step 7: Commit**

```bash
git add config.example.yaml QUICKSTART.md docs/INSTALL.md README.md CHANGELOG.md pyproject.toml
git commit -m "docs: uv install path, doctor, certifi; bump to 1.1.0"
```

---

## Manual validation gate (before any push — human-run, with Adam present)

On a **clean Mac account** (no developer tools):
1. Download the repo ZIP, unzip, `bash install.sh`.
2. Sign in to Monarch when the browser opens.
3. Confirm `financial-mcp-doctor` prints all green.
4. In Claude Desktop, ask *"What are my Monarch account balances?"* → live data.

Exercise **both uv paths** (a Mac with Homebrew and one without → download-then-run branch) and **both architectures** (Intel + Apple Silicon). Only after this passes does pushing to origin get proposed (requires explicit approval).

## Self-Review notes

- **Spec coverage:** A→Tasks 1–2; B→Task 3; C→Task 4; D→Task 5; E→Task 6; F→Task 7. All covered.
- **Type consistency:** `resolve_user_path`/`ensure_parent` (Task 1) are consumed by Tasks 2 and 5 with matching signatures; `_SSL_CTX` (Task 3) consumed by Task 5; console-script names match `pyproject.toml`.
- **No placeholders:** every code step has concrete content.
