# Plan 2.2 Browser-Driven Login — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual cURL-paste login with a browser-driven login — `financial-mcp-login` opens Chromium, the user signs in via Google SSO, and the script captures `session_id` + `csrftoken` and saves them to the macOS Keychain — while keeping cURL-paste as a permanent fallback.

**Architecture:** Playwright is added as a new *cookie source* feeding the unchanged Plan 2.1 pipe. A new focused module `browser_login.py` launches a persistent-context Chromium and reads the two cookies; `login_cli.py` becomes a router (browser-first, cURL fallback). Everything below the seam — `auth.py`, `CommunityAdapter`, transport, cache, MCP tools, `_validate()` — is untouched.

**Tech Stack:** Python 3.12+ (venv is 3.14.5), Playwright (Microsoft), `keyring`, pytest with `monkeypatch`.

**Spec:** `docs/specs/2026-07-24-monarch-mcp-plan-2.2-browser-login-design.md`

## Global Constraints

- **cURL-paste is a PERMANENT additive fallback** — the browser path may fail for any reason and the user must never be locked out. `financial-mcp-login --curl` always works; the bare command auto-falls-back on any browser-login error. Playwright never removes or degrades the Plan 2.1 login.
- **Do NOT modify** `auth.py`, `community_adapter.py`, transport, cache, or any MCP tool. This plan only changes the login entry path.
- **Both login paths share the same trusted tail:** `_validate()` (one live accounts call) then `auth.save_session()`. A session captured either way is identical.
- **macOS Keychain only** — no cross-platform credential stores (deferred to Goal #3).
- **Tests use synthetic FAKE cookie values only** (e.g. `SID_FAKE_123`, `CSRF_FAKE_456`) — never real session data — matching `tests/test_login_cli.py`. Playwright is mocked in the suite; no real browser launches during tests.
- **The two required cookies are** `session_id` and `csrftoken` (the existing `_WANTED` tuple).
- **Security protocol:** the Playwright dependency was assessed and approved pre-install on 2026-07-24. The install-time scan (Task 1) MUST run and be recorded in `~/.claude/SECURITY-REPORT.md`, and the pending note `~/.claude/SECURITY-REPORT.md.pending-plan-2.2` deleted.
- **Do NOT commit** `TeamFrank_12Month_Plan_v3a.pdf`, anything under `.superpowers/`, `.pw-profile/`, or any real cookies.

---

## File Structure

- **Create `financial_mcp/monarch/browser_login.py`** — browser cookie capture. Two functions: `extract_wanted_cookies(cookies)` (pure, unit-testable) and `capture_session_via_browser(...)` (thin Playwright orchestration, verified by manual live test). Defines `BrowserLoginError`.
- **Modify `financial_mcp/login_cli.py`** — refactor into a router: extract the existing paste flow into `_curl_login()`, add `_browser_login()`, add a shared `_login(force_curl)` tail that validates + saves, wire `--curl` and unchanged `--logout` in `main()`.
- **Modify `pyproject.toml`** — add `playwright` to dependencies.
- **Modify `.gitignore`** — add `.pw-profile/`.
- **Create `tests/test_browser_login.py`** — cookie-extraction unit tests (Playwright mocked / not imported).
- **Modify `tests/test_login_cli.py`** — add router-fallback tests; existing parse tests unchanged.

---

## Task 1: Add Playwright dependency, gitignore the profile, run the security scan

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Modify: `.gitignore`
- External: install Playwright + Chromium; append to `~/.claude/SECURITY-REPORT.md`; delete `~/.claude/SECURITY-REPORT.md.pending-plan-2.2`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: an importable `playwright` package in `.venv`; `.pw-profile/` git-ignored. Later tasks import `playwright.sync_api` lazily.

- [ ] **Step 1: Add `.pw-profile/` to `.gitignore`**

Add these two lines under the "Local cache / secrets" section of `.gitignore`:

```
# Playwright persistent browser profile — local, never commit
.pw-profile/
```

- [ ] **Step 2: Verify the profile dir is ignored**

Run: `mkdir -p .pw-profile && git check-ignore .pw-profile && rmdir .pw-profile`
Expected: prints `.pw-profile` (confirming it is ignored), exit code 0.

- [ ] **Step 3: Add `playwright` to `pyproject.toml` dependencies**

In the `[project]` `dependencies` list, add `"playwright>=1.40"` as the last entry:

```toml
dependencies = [
    "mcp>=1.2.0",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "keyring>=25.0",
    "aiohttp>=3.9",
    "playwright>=1.40",
]
```

- [ ] **Step 4: Install the dependency**

Run: `.venv/bin/pip install -e .`
Expected: resolves and installs `playwright`.
**Contingency:** if pip fails specifically because Playwright does not support Python 3.14, do NOT guess a pin — report status **BLOCKED** with the exact pip error so the controller can decide (e.g. pin a version or use a 3.12 interpreter). Do not proceed past this step if the import in Step 6 fails.

- [ ] **Step 5: Install the Chromium browser binary**

Run: `.venv/bin/playwright install chromium`
Expected: downloads Chromium (~one-time). **Contingency:** if this is blocked by network/endpoint controls (a known Salesforce-managed-Mac risk), report status **DONE_WITH_CONCERNS** noting the block — the code still works via the cURL fallback, and the binary can be installed later. Do not treat a blocked binary download as a hard failure of this task.

- [ ] **Step 6: Verify the package imports**

Run: `.venv/bin/python -c "import playwright.sync_api; print('playwright ok')"`
Expected: prints `playwright ok`.

- [ ] **Step 7: Run the security scan and record it**

Run these and capture output:
```bash
.venv/bin/pip show playwright
find .venv/lib/*/site-packages/playwright -name "*.py" | head -20
grep -rniE "eval\(|base64|exec\(|subprocess" .venv/lib/*/site-packages/playwright/__init__.py
```
Then append a dated entry to `~/.claude/SECURITY-REPORT.md` with: package name/version, source (PyPI/Microsoft), trust level (HIGH — verified partner), what was scanned, external-connection note (install-time browser download from Microsoft CDN; runtime drives a local browser, our code makes no outbound calls), and verdict (Approved). Then delete the pending note: `rm ~/.claude/SECURITY-REPORT.md.pending-plan-2.2`.

- [ ] **Step 8: Confirm the existing suite still passes**

Run: `.venv/bin/python -m pytest -q`
Expected: all existing tests pass (adding a dependency must not break anything).

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml .gitignore
git commit -m "build: add Playwright dependency + gitignore browser profile

Playwright (Microsoft) added for Plan 2.2 browser login. Security-scanned
and recorded in ~/.claude/SECURITY-REPORT.md per protocol. .pw-profile/
persistent browser profile is git-ignored.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `browser_login.py` — cookie capture module

**Files:**
- Create: `financial_mcp/monarch/browser_login.py`
- Test: `tests/test_browser_login.py`

**Interfaces:**
- Consumes: `playwright.sync_api.sync_playwright` (imported lazily inside `capture_session_via_browser`).
- Produces:
  - `BrowserLoginError(RuntimeError)` — raised when browser login cannot complete; the caller catches it to fall back.
  - `extract_wanted_cookies(cookies: list[dict]) -> dict` — returns `{"session_id": str, "csrftoken": str}`; raises `ValueError` naming the missing cookie.
  - `capture_session_via_browser(*, monarch_url: str = MONARCH_LOGIN_URL, profile_dir: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict` — returns the same `{"session_id", "csrftoken"}` dict; raises `BrowserLoginError` on any failure/timeout.
  - Module constants: `MONARCH_LOGIN_URL = "https://app.monarch.com/login"`, `DEFAULT_TIMEOUT_SECONDS = 180`.

- [ ] **Step 1: Write the failing tests for cookie extraction**

Create `tests/test_browser_login.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_browser_login.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` (module does not exist yet).

- [ ] **Step 3: Write the module**

Create `financial_mcp/monarch/browser_login.py`:

```python
from __future__ import annotations

import time

_WANTED = ("session_id", "csrftoken")

MONARCH_LOGIN_URL = "https://app.monarch.com/login"
DEFAULT_TIMEOUT_SECONDS = 180
_POLL_INTERVAL_SECONDS = 1.0


class BrowserLoginError(RuntimeError):
    """Browser-driven login could not complete; the caller should fall back."""


def extract_wanted_cookies(cookies: list[dict]) -> dict:
    """Pull session_id + csrftoken from a Playwright cookie list.

    `cookies` is the list of dicts returned by BrowserContext.cookies(),
    each shaped like {"name": ..., "value": ..., "domain": ...}. Raises
    ValueError (naming the missing cookie) if either required cookie is
    absent or empty — same error shape as parse_curl_cookies().
    """
    jar = {c.get("name"): c.get("value") for c in cookies if c.get("name")}
    out = {}
    for name in _WANTED:
        if not jar.get(name):
            raise ValueError(
                f"could not find '{name}' cookie in the browser session — "
                "make sure you are fully signed in to Monarch"
            )
        out[name] = jar[name]
    return out


def capture_session_via_browser(
    *,
    monarch_url: str = MONARCH_LOGIN_URL,
    profile_dir: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Open a persistent-context Chromium, let the user sign in to Monarch,
    and return {"session_id", "csrftoken"} once both cookies are present.

    Raises BrowserLoginError if Playwright is unavailable, the browser
    cannot launch, or the sign-in is not completed before timeout. The
    persistent context (profile_dir) lets the browser session persist
    between logins, which also mitigates Google's automation checks.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # Playwright not installed
        raise BrowserLoginError("Playwright is not installed") from exc

    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                profile_dir, headless=False
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(monarch_url)
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    try:
                        return extract_wanted_cookies(context.cookies())
                    except ValueError:
                        time.sleep(_POLL_INTERVAL_SECONDS)
                raise BrowserLoginError(
                    "timed out waiting for Monarch sign-in"
                )
            finally:
                context.close()
    except BrowserLoginError:
        raise
    except Exception as exc:  # launch failure, MDM block, closed window, etc.
        raise BrowserLoginError(f"browser login failed: {exc}") from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_browser_login.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint & format**

Run: `.venv/bin/ruff check financial_mcp/monarch/browser_login.py tests/test_browser_login.py && .venv/bin/black financial_mcp/monarch/browser_login.py tests/test_browser_login.py`
Expected: ruff clean; black leaves files formatted.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/monarch/browser_login.py tests/test_browser_login.py
git commit -m "feat: browser_login — capture Monarch session cookies via Chromium

extract_wanted_cookies() (pure, unit-tested) pulls session_id + csrftoken
from a Playwright cookie list; capture_session_via_browser() launches a
persistent-context Chromium, polls for the cookies with a bounded wait,
and raises BrowserLoginError on any failure so the caller can fall back.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `login_cli.py` router — browser-first with cURL fallback

**Files:**
- Modify: `financial_mcp/login_cli.py`
- Test: `tests/test_login_cli.py` (add router tests; existing parse tests unchanged)

**Interfaces:**
- Consumes: `browser_login.capture_session_via_browser`, `browser_login.BrowserLoginError` (Task 2); existing `parse_curl_cookies`, `_validate`, `_service_name`, `auth.save_session`.
- Produces: `_browser_login() -> dict | None`, `_curl_login() -> dict`, `_login(force_curl: bool = False) -> None`. `main()` routes `--logout` / `--curl` / default.

- [ ] **Step 1: Write the failing router tests**

Add to `tests/test_login_cli.py`:

```python
import financial_mcp.login_cli as cli


def _patch_tail(monkeypatch):
    """Stub the shared validate+save tail; return a dict that records the save."""
    saved = {}
    monkeypatch.setattr(cli, "_validate", lambda session: True)
    monkeypatch.setattr(cli, "_service_name", lambda: "svc")
    monkeypatch.setattr(
        cli.auth,
        "save_session",
        lambda sid, csrf, svc: saved.update(sid=sid, csrf=csrf, svc=svc),
    )
    return saved


def test_login_uses_browser_session_when_available(monkeypatch):
    saved = _patch_tail(monkeypatch)
    curl_calls = {"n": 0}
    monkeypatch.setattr(
        cli, "_browser_login", lambda: {"session_id": "BROWSER", "csrftoken": "X"}
    )

    def fake_curl():
        curl_calls["n"] += 1
        return {"session_id": "CURL", "csrftoken": "C"}

    monkeypatch.setattr(cli, "_curl_login", fake_curl)

    cli._login(force_curl=False)

    assert curl_calls["n"] == 0
    assert saved["sid"] == "BROWSER"


def test_login_falls_back_to_curl_when_browser_returns_none(monkeypatch):
    saved = _patch_tail(monkeypatch)
    monkeypatch.setattr(cli, "_browser_login", lambda: None)
    monkeypatch.setattr(
        cli, "_curl_login", lambda: {"session_id": "CURL", "csrftoken": "C"}
    )

    cli._login(force_curl=False)

    assert saved["sid"] == "CURL"


def test_curl_flag_skips_browser(monkeypatch):
    saved = _patch_tail(monkeypatch)
    browser_calls = {"n": 0}

    def fake_browser():
        browser_calls["n"] += 1
        return {"session_id": "BROWSER", "csrftoken": "X"}

    monkeypatch.setattr(cli, "_browser_login", fake_browser)
    monkeypatch.setattr(
        cli, "_curl_login", lambda: {"session_id": "CURL", "csrftoken": "C"}
    )

    cli._login(force_curl=True)

    assert browser_calls["n"] == 0
    assert saved["sid"] == "CURL"


def test_browser_login_returns_none_on_error(monkeypatch):
    def raiser(**kwargs):
        from financial_mcp.monarch.browser_login import BrowserLoginError

        raise BrowserLoginError("boom")

    monkeypatch.setattr(
        "financial_mcp.monarch.browser_login.capture_session_via_browser", raiser
    )

    assert cli._browser_login() is None
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_login_cli.py -v`
Expected: the 4 new tests FAIL with `AttributeError` (`_browser_login` / `_curl_login` / `_login` don't exist yet); the 5 existing parse tests still PASS.

- [ ] **Step 3: Refactor `login_cli.py` into a router**

Replace the body of `financial_mcp/login_cli.py` from the `_login` definition onward. Keep `parse_curl_cookies`, `_service_name`, `_validate`, and the `_WANTED` tuple exactly as they are. Add the imports `from pathlib import Path` (top of file) and replace `_login`/`main` with:

```python
def _curl_login() -> dict:
    """Manual fallback: read a 'Copy as cURL' blob from stdin and parse it."""
    print(
        "Monarch manual login.\n"
        "1. In your browser, open Monarch and log in.\n"
        "2. DevTools -> Network tab, filter 'graphql', click a POST request to "
        "api.monarch.com.\n"
        "3. Right-click it -> Copy -> Copy as cURL.\n"
        "4. Paste it below, then press Ctrl-D (Ctrl-Z Enter on Windows):"
    )
    blob = sys.stdin.read()
    try:
        return parse_curl_cookies(blob)
    except ValueError:
        raise SystemExit(
            "Login failed: could not extract session cookies from the pasted "
            "cURL command. Make sure you copied a request to api.monarch.com "
            "while logged in."
        )


def _browser_login() -> dict | None:
    """Attempt browser-driven login. Returns the session dict on success, or
    None on any failure so the caller falls back to the manual path."""
    from financial_mcp.monarch.browser_login import (
        BrowserLoginError,
        capture_session_via_browser,
    )

    profile_dir = str(Path(__file__).resolve().parent.parent / ".pw-profile")
    print(
        "Opening a browser for Monarch sign-in — complete Google sign-in in "
        "the window that appears."
    )
    try:
        return capture_session_via_browser(profile_dir=profile_dir)
    except BrowserLoginError as exc:
        print(
            f"Browser login unavailable ({exc}). Falling back to manual login.",
            file=sys.stderr,
        )
        return None


def _login(force_curl: bool = False) -> None:
    session = None
    if not force_curl:
        session = _browser_login()
    if session is None:
        session = _curl_login()
    if not _validate(session):
        raise SystemExit("Login failed. Try again with a fresh session.")
    auth.save_session(session["session_id"], session["csrftoken"], _service_name())
    print("Signed in — session saved to Keychain.")


def main() -> None:
    if "--logout" in sys.argv:
        auth.delete_session(_service_name())
        print("Logged out — session removed from Keychain.")
        return
    _login(force_curl="--curl" in sys.argv)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the full login test file to verify all pass**

Run: `.venv/bin/python -m pytest tests/test_login_cli.py -v`
Expected: PASS — 5 existing parse tests + 4 new router tests = 9 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass (no regressions).

- [ ] **Step 6: Lint & format**

Run: `.venv/bin/ruff check financial_mcp/login_cli.py tests/test_login_cli.py && .venv/bin/black financial_mcp/login_cli.py tests/test_login_cli.py`
Expected: ruff clean; black leaves files formatted.

- [ ] **Step 7: Commit**

```bash
git add financial_mcp/login_cli.py tests/test_login_cli.py
git commit -m "feat: login router — browser-first login with cURL fallback

financial-mcp-login now attempts browser-driven login first and falls back
to the manual cURL-paste path on any failure. --curl forces the manual
path; --logout unchanged. Both paths share the same _validate() +
save_session() tail. cURL-paste remains a permanent fallback.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Post-Implementation: Manual Live Verification (user-run, not automated)

After all three tasks are merged and the code is live, the user verifies end-to-end (like the Plan 2.1 smoke test):

1. Run `.venv/bin/financial-mcp-login`.
2. Complete Google SSO in the Chromium window that opens.
3. Confirm it prints `Signed in — session saved to Keychain.` and closes.
4. In a fresh Claude Desktop chat (after Cmd+Q + reopen), confirm a tool call (e.g. account balances) works against the browser-captured session.
5. Also verify the fallback: `.venv/bin/financial-mcp-login --curl` still runs the paste flow.

Record the result in project memory as the Plan 2.2 live-proof.

---

## Self-Review

**Spec coverage:**
- §2 architecture (Playwright as additive cookie source, router) → Tasks 2 & 3. ✅
- §3 seamless login, persistent `.pw-profile/`, bounded 180s wait, auto-close → Task 2 (`capture_session_via_browser`, `DEFAULT_TIMEOUT_SECONDS = 180`, `context.close()`), Task 1 (gitignore). ✅
- §4 error handling / fallback contract → Task 2 (`BrowserLoginError` on all failures), Task 3 (`_browser_login` returns None → `_curl_login`; `--curl` skip). Tests cover fallback + skip. ✅
- §5 dependency + security scan → Task 1 (Steps 3–7, incl. scan + pending-note deletion). ✅
- §6 testing (mocked unit tests, manual live verify) → Tasks 2 & 3 tests; manual step documented separately. ✅
- §7 files touched → matches File Structure. ✅
- §8 out of scope → nothing in the plan strays into cross-platform/packaging. ✅

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows complete code; every command has expected output. ✅

**Type consistency:** `extract_wanted_cookies(list[dict]) -> dict` and `capture_session_via_browser(*, profile_dir, ...) -> dict` are used identically in Task 3 (`_browser_login` calls `capture_session_via_browser(profile_dir=...)`; router functions return `dict | None`). `BrowserLoginError` defined in Task 2, caught in Task 3. Constant `DEFAULT_TIMEOUT_SECONDS = 180` consistent with spec's ~180s. ✅
