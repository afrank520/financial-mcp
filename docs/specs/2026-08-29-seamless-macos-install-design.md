# Seamless macOS Install — Design

**Date:** 2026-08-29
**Status:** Approved (design), pending implementation
**Author:** Adam + Claude
**Type:** Architectural — restructures the install/bootstrap path (uv) plus four cross-cutting code fixes.

## Problem

The first two real beta installs (Tracey's Intel MacBook Air, macOS 14.8.9) cost
a full night of debugging. None of the failures were bugs in the tool's logic —
they were **environment landmines** hidden behind a misleading error message.
The goal is a genuinely seamless macOS experience: **download the ZIP → run the
installer → authenticate → done.**

### What actually went wrong (the experiential learning)

Chronologically, five distinct traps, plus one blindfold:

1. `cryptography` compiled from source (needs a Rust/OpenSSL toolchain) →
   already fixed by forcing `--only-binary=:all:` (commit a2fd951).
2. Python 3.14 has no prebuilt wheels yet → already fixed by a 3.12 pin + guard
   (commit d954250).
3. A stale 3.14 `.venv` was reused past the version guard → already fixed by
   version-checking the existing venv (commit c0317a2).
4. **Relative config paths crash under Claude Desktop.** Claude Desktop launches
   the MCP server with the working directory set to `/` (filesystem root). The
   shipped `config.example.yaml` uses relative paths (`db_path: cache.db`,
   `log_file: financial_mcp.log`), which then resolve against `/` — a read-only
   location — and the server crashes on startup (`OSError [Errno 30]`, then
   `FileNotFoundError` once the folder was missing). Adam's own dev config never
   hit this because his paths happen to be absolute. **NOT yet fixed in code.**
5. **python.org's macOS Python ships CA certificates but never activates them.**
   Every HTTPS call to Monarch failed with
   `ssl.SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED: unable to get local
   issuer certificate`. The python.org installer bundles a one-time
   `Install Certificates.command` that had never been run. **NOT yet fixed in
   code.**

The blindfold: `login_cli._validate()` wraps the live check in a bare
`except Exception` and prints a generic *"That session did not work — it may be
expired or incomplete."* on ANY error. This hid the real SSL error for hours and
made a certificate problem masquerade as a login problem. The login itself was
**perfect from the first attempt** (`session_id: 32, csrftoken: 32`).

## Goals

- A non-technical Mac user can go **download ZIP → `bash install.sh` → sign in →
  done**, with no manual Python installation and no certificate ceremony.
- The tool works regardless of whether the machine's certificates were ever
  "activated."
- When something does go wrong, the error names the real cause in plain English.
- Existing installs (Adam's, Tracey's) keep working and self-heal on re-run.

## Non-goals

- Windows / Linux install paths (this tool is macOS + Claude Desktop, per README).
- Migrating off `mcp<2` (tracked separately).
- Any change to the tool's financial capabilities or the write-gate safety model.

## Decisions (owner-made)

- **uv acquisition:** use Homebrew (`brew install uv`) when present; otherwise
  download uv's official install script *to a file* and run it — never a blind
  `curl … | sh`. This honors the "don't pipe a web script straight into your
  shell" principle already stated in QUICKSTART.
- **Old plain-Python path:** the installer becomes **uv-only** (one reliable
  path). The manual `python3 -m venv` + python.org steps stay in `INSTALL.md` as
  a developer/edge-case fallback.

---

## Architecture

Six components. A–D are code/robustness fixes that stand on their own and are
valuable even for a hand-install. E–F are the seamless installer + docs. They
ship together as v1.1.0 but A–D land first so each is independently reviewable.

### A. Config path resolution (`financial_mcp/config.py`)

The failure is that a relative path is interpreted against an unpredictable
working directory. The fix normalizes every user-file path to an absolute path
anchored in a stable, writable home directory.

- Introduce a module constant `USER_DIR = Path("~/.financial_mcp").expanduser()`
  and a pure helper:

  ```python
  def resolve_user_path(value: str) -> str:
      """Expand ~ and anchor a relative path under ~/.financial_mcp/.
      Absolute paths (after ~ expansion) are returned unchanged."""
      p = Path(value).expanduser()
      if not p.is_absolute():
          p = USER_DIR / p
      return str(p)
  ```

- After a `Config` is built, resolve the three user-file fields:
  `db_path` (default `cache.db` → `~/.financial_mcp/cache.db`), `log_file`
  (only if set; `None` stays `None` = no file logging), and `credential_file`
  (already `~/...`; routed through the same helper for consistency). Implement
  as a pydantic `model_validator(mode="after")` so resolution happens once at
  load time and is identical everywhere.

- **Folder creation is separate from resolution** (keeps the resolver pure and
  unit-testable without touching `$HOME`). The parent directory is created
  (mkdir parents, mode `0o700` to match the credential store's sensitivity) at
  the point of first use:
  - in `utils/logging.py` immediately before opening the `FileHandler`, and
  - in the cache init immediately before opening the sqlite file.
  A tiny shared helper `ensure_parent(path)` does the mkdir.

- `config.example.yaml` comments updated to explain that bare filenames resolve
  under `~/.financial_mcp/` automatically, so the example can no longer produce a
  broken install.

### B. Certificate robustness (`financial_mcp/monarch/community_adapter.py`)

Do not depend on OS-level certificate activation. Use `certifi`'s bundle
directly for the one place the tool talks to Monarch.

- Add `certifi` to `[project].dependencies`.
- Build an SSL context from certifi once at module load:

  ```python
  import ssl, certifi
  _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
  ```

- In `_default_transport`, create the `ClientSession` with a connector that uses
  it:

  ```python
  connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
  async with aiohttp.ClientSession(cookies=cookies, connector=connector) as session:
      ...
  ```

  (Playwright's browser sign-in uses its own bundled Chromium/certs and is
  unaffected; the post-login `_validate()` call goes through this adapter and is
  now cert-safe.)

### C. Honest login errors (`financial_mcp/login_cli.py`)

`_validate()` stops swallowing the cause. Behavior:

- Catch `SessionExpiredError` / `CloudflareBlockedError` and print their own
  (already user-facing) message.
- For any other exception, print the real thing:
  `f"Couldn't verify the session: {type(exc).__name__}: {exc}"` plus a one-line
  hint that a `CERTIFICATE_VERIFY_FAILED`-style error usually means a secure
  connection problem, not a bad login.
- Still returns `False` (never crashes the CLI); the point is the *message*.

### D. `financial-mcp doctor` self-check (`financial_mcp/doctor.py`, new)

A single command that turns last night's multi-hour hunt into one line of
output. Shipped as its own console script `financial-mcp-doctor` so it never
touches the server-startup path in `main.py`.

- Checks, each printing `✅`/`❌` and a plain-English line:
  1. **Python version** in `[3.12, 3.14)`.
  2. **Config** loads; resolved `db_path` / `log_file` / `credential_file` are
     absolute and their parent is writable; `~/.financial_mcp/` exists/creatable.
  3. **Secure reachability** — a lightweight HTTPS request to
     `https://api.monarch.com` using the same certifi-backed SSL context. A cert
     failure here is reported explicitly as the certificate problem (with the
     `Install Certificates.command` hint for hand-installs).
  4. **Session** — a saved session exists and a live `get_accounts()` succeeds.
     Reports `session valid — N accounts` on success. **Never prints balances or
     account names** (privacy). On `SessionExpiredError`, tells the user to run
     `financial-mcp-login`.
- Prints a final summary (`All green` or `N issue(s) found — see above`) and
  exits `0` when all green, non-zero otherwise, so the installer can branch on it.

### E. uv-based installer (`install.sh`, rewrite)

Ordered steps, replacing the current Python-detection + venv logic:

1. **Ensure uv.** If `command -v uv` succeeds, use it. Else if `command -v brew`,
   run `brew install uv`. Else download the official installer to a temp file
   (`curl -LsSf https://astral.sh/uv/install.sh -o "$TMP/uv-install.sh"`), print
   a note that it was downloaded and can be inspected, then `sh "$TMP/uv-install.sh"`.
   Add `$HOME/.local/bin` to `PATH` for the rest of the run and verify
   `uv --version`. `die()` with a friendly message if uv still isn't available.
2. **Python + venv + install (uv owns Python).** `uv venv --python 3.12 .venv`
   — uv downloads a known-good standalone CPython 3.12 if the machine lacks one
   (no admin, no python.org, certificates already working). Then
   `uv pip install --python .venv/bin/python -e .`. This lands the console
   scripts at **`.venv/bin/financial-mcp`** — the exact same path as today — so
   existing Claude Desktop configs keep working with no edit.
3. **Sign-in browser.** `.venv/bin/python -m playwright install chromium`.
4. **Config.** Unchanged from today (create `config.yaml` from the example with
   `source: live`, `write_mode` stays `off`). Component A means even a relative
   path in the example resolves correctly.
5. **Monarch sign-in.** Unchanged (`.venv/bin/financial-mcp-login`).
6. **Doctor.** Run `.venv/bin/financial-mcp-doctor` and surface the result.
7. **Claude Desktop wiring.** Unchanged (the safe JSON merge already in place).

- `--fresh` still deletes and rebuilds `.venv` (now via uv), keeping config +
  login. Re-running is idempotent and **self-heals** a broken venv because uv
  always targets `--python 3.12`. The old python.org detection, the 3.14 guard,
  and the stale-venv rebuild logic are removed (their concerns are now uv's job).
- Keep `set -euo pipefail`, the `say/ok/warn/die` helpers, `-h/--help`,
  unknown-flag rejection, and the non-interactive-safe `read … || true` prompts.

### F. Documentation

- **QUICKSTART.md:** the "install the right Python by hand" section moves out of
  the installer path — the Overview + Path A become **download ZIP → `bash
  install.sh` → sign in → done**. Lead the install command with `bash install.sh`
  (the ZIP strips the executable bit, so `./install.sh` can fail
  "permission denied"). Manual Part 1 stays as the labeled fallback. Add
  troubleshooting entries: certificate error → run doctor / Install
  Certificates.command; permission denied → `bash install.sh`.
- **INSTALL.md:** uv path as primary; the existing `python3 -m venv` +
  `--only-binary` steps stay as the manual/developer fallback. Note `certifi` and
  the `financial-mcp-doctor` command.
- **config.example.yaml:** comment updates (Component A).
- **README.md:** mention `financial-mcp doctor`.
- **CHANGELOG.md / pyproject.toml:** bump to **1.1.0** with an entry summarizing
  the install hardening.

---

## Testing

Unit (must keep the full suite green — currently 374 tests):

- **Config resolution:** a relative `db_path`/`log_file` resolves to an absolute
  path under `~/.financial_mcp/` regardless of the process cwd (chdir to a temp
  dir in the test); `~` expands; `log_file=None` stays `None`; an already-absolute
  path passes through unchanged. Use monkeypatched `USER_DIR` / `$HOME` so the
  test never writes to the real home.
- **certifi wiring:** the adapter builds its SSL context from `certifi.where()`
  and passes it to the connector (assert via a monkeypatched `TCPConnector` /
  transport spy — no real network).
- **Honest errors:** monkeypatch `CommunityAdapter.get_accounts` to raise a
  generic error → `_validate` prints the real class+message and returns `False`;
  a `SessionExpiredError` → its own message.
- **doctor:** each check exercised with fakes (good Python, bad path, cert
  failure, no session, expired session, all-green); assert the summary line and
  exit code, and assert **no balance/account-name** ever printed.

Non-unit:

- `bash -n install.sh` under **system bash 3.2** (`/bin/bash`), per the standing
  lesson that dev bash hides 3.2 parse errors.

## Validation gate (the decisive one)

A manual, end-to-end run on a **clean Mac account** with no developer tools:
download ZIP → `bash install.sh` → sign in → *"What are my Monarch account
balances?"* returns live data → `financial-mcp doctor` all green. Exercise **both
uv paths** (a Mac with Homebrew and one without, to hit the download-then-run
branch) and **both architectures** (Intel + Apple Silicon). This is what catches
what unit tests structurally cannot — it caught the `mcp 2.x` and cert traps that
the dev machine hid.

## Rollout

Branch `seamless-macos-install`. Build task-by-task (subagent-driven), keep the
suite green, then the clean-Mac validation gate. **Push to origin requires
explicit approval** (private repo, but still an outward action). Live-prove on a
clean machine before pointing more beta testers at it.

## Risks & mitigations

- **uv download step could itself be blocked** on a locked-down machine → the
  installer `die()`s with a clear message and INSTALL.md's manual venv path
  remains as the fallback.
- **uv-managed Python path drift** — mitigated by keeping the venv at the fixed
  `.venv/bin/financial-mcp` location so Claude Desktop config is stable.
- **certifi staleness** — pinned as a normal dependency; it updates like any
  other and uv reinstalls it on `--fresh`.
