# Monarch MCP — Credential store: file-first, keyring opt-in (design)

**Status:** Designed — not yet built.

**Roadmap slot:** A usability + security foundation for **Goal #3 (sharing)**. It
does not add a user-facing analytics capability; it changes *how the Monarch session
credential is stored at rest* so a non-technical shared user never sees the scary,
unbranded macOS Keychain prompt ("Python wants to use your confidential information
stored in 'financial_mcp_monarch'"). Sits before the later Plan 4 slices / the
`financial_context` resource / the HELOC glide-path, because it gates comfortable
sharing.

## 1. What this is

Replace the single hard-wired OS-keyring credential path with a small
**`CredentialStore` abstraction** and two implementations:

- **`FileCredentialStore` (the new default)** — the Monarch session blob in a
  permission-locked local file (`~/.financial_mcp/session.json`, `0o600`), the same
  pattern the GitHub CLI / AWS CLI / npm use. No OS prompt, works on first launch,
  cross-platform.
- **`KeyringCredentialStore` (opt-in)** — today's exact `keyring` logic, lifted behind
  the interface unchanged, selected with one config flag for users who want OS-level
  encryption-at-rest and accept a (properly one-time) prompt.

### Out of scope (YAGNI / later)
- A passphrase-encrypted file backend (a possible third store later, for users who want
  real at-rest encryption against file exfiltration — see §7). Not built now because it
  reintroduces per-startup friction, which is exactly what this slice removes.
- Any change to the *login* mechanics (browser login / cURL-paste) beyond swapping the
  storage call. Any change to how the cookie is *used* by `CommunityAdapter`.
- Windows-grade permission hardening (best-effort only; §4).

## 2. Why file-first is the honest best practice here

The tempting idea — "an encrypted local file, no user effort" — is mostly security
theater: encryption needs a key, and if the app stores the key beside the file so the
user isn't bothered, anyone who can read the file can read the key. Real encryption
needs a secret the app does not hold (a passphrase or managed env var), which
reintroduces the friction we are trying to remove.

Two facts drive the design:

1. **The scary macOS prompt is a code-signature ACL check, not a per-read passphrase.**
   It re-fires because Homebrew's Python is ad-hoc signed (unstable code identity), so
   "Always Allow" can't durably bind. It is not asking for extra security on each read.
2. **The keyring's real, honest benefit is "encrypted at rest while the machine is
   locked or powered off."** Once the user is logged in, a process running *as them* can
   generally read the keyring without a prompt. Against malware running as the user, the
   keyring does not meaningfully do more than a permission-locked file.

**The realistic threats for this tool** — and what a `0o600` file in a non-synced
directory defends against with zero friction:

| Threat | File store (0o600, ~/.financial_mcp/, gitignored) |
|---|---|
| Accidental exposure (commit to git, synced/shared folder, screen-share) | ✅ location + perms + .gitignore |
| Another non-admin user on the same machine | ✅ perms |
| Casual snooping | ✅ perms |
| Malware / root running as the user | ❌ (keyring doesn't really stop this either, once logged in) |
| Machine stolen while locked/off | ⚠️ keyring opt-in covers this; file store relies on full-disk encryption (FileVault) |

This is why `gh`, `aws`, and `npm` default to a permission-locked file, not the OS
keyring: the reliability + zero-friction win beats a marginal, machine-locked-only
security gain that is fragile to package across machines we do not control. For a tool
meant to be shared with non-technical users, that logic is stronger, not weaker.

**Honest docs are part of the deliverable** (§6): we state plainly what protects what,
and never imply the file is encrypted when it is permission-locked.

## 3. Architecture: the `CredentialStore` interface

A thin protocol in front of storage; the rest of the app never knows the backend.

```
CredentialStore (protocol)
  save_session(session_id: str, csrftoken: str) -> None
  load_session() -> dict | None      # {"session_id": ..., "csrftoken": ...} or None
  delete_session() -> None

FileCredentialStore(path)     — DEFAULT
KeyringCredentialStore(service_name)  — opt-in; today's logic, unchanged
```

- A `make_credential_store(cfg) -> CredentialStore` factory builds the right store from
  config. `main.py` (startup) and `login_cli.py` (login/logout) call the factory and
  then the interface — they **stop importing `keyring`** entirely.
- `load_session` returns the identical validated dict shape used today
  (`{"session_id", "csrftoken"}` or `None`), so `main.py`'s existing "no session →
  unauthenticated/CSV fallback" is byte-for-byte unchanged.

**Module home:** the current `financial_mcp/monarch/auth.py` is repurposed into a new
`financial_mcp/credentials.py` holding the protocol, both stores, and the factory (it is
no longer Monarch-specific — it is app credential storage). `auth.py` is retained as
thin re-exports if any external caller imports it; all internal callers
(`main.py`, `login_cli.py`, `community_adapter.py`) move to `credentials`.

## 4. FileCredentialStore mechanics (load-bearing details)

- **Path:** `~/.financial_mcp/session.json`, overridable via config `credential_file`.
  Co-located with the existing `cache.db` and `financial_mcp.log`.
- **Write (atomic + private):** ensure the parent dir exists with mode `0o700` → write
  the JSON blob to a temp file *in the same directory* → `os.chmod(temp, 0o600)` →
  `os.replace(temp, path)`. Atomic replace guarantees no half-written or transiently
  world-readable file is ever observable at the final path.
- **Read:** if the file's mode is broader than `0o600`, log a warning and tighten it to
  `0o600` before reading (do not silently trust a loosened file). Missing file → `None`;
  non-JSON / `JSONDecodeError` → `None`; JSON not a dict or missing `session_id` /
  `csrftoken` → `None`. (Mirrors today's keyring validation exactly.)
- **Delete:** remove the file; a no-op (no error) when already absent.
- **Cross-platform:** POSIX perms enforced on macOS/Linux. On Windows `os.chmod` is
  limited to the read-only bit; the store does best-effort and the docs carry the
  caveat that Windows relies on the user profile's ACLs. macOS is the proven target;
  Windows sharing is a documented future note, not a blocker.
- **No secret values are ever written to logs** (only paths, modes, and outcomes).

## 5. Config, selection, and migration

**Config (`config.py`):**
- `credential_store: "file" | "keyring"` — **default `"file"`**.
- `credential_file: str` — default `~/.financial_mcp/session.json` (expanded from `~`).
- `keyring_service_name: str` — kept; used only when `credential_store: keyring`.

The live `config.yaml` sets none of these, so it silently adopts the file default. This
means the next server start looks for the file, finds none, and falls back to
unauthenticated — which is why migration (below) is part of shipping this.

**Migration (so the existing live keyring session is not lost):**
- **Primary — re-run `financial-mcp-login` once.** It writes to the new file store,
  promptless (the persistent browser profile remembers the Monarch session). Guaranteed,
  simplest path.
- **Convenience — `financial-mcp-login --migrate-from-keyring`:** reads the old keyring
  entry (one final, expected Keychain prompt), writes it to the file store, then
  **deletes the keyring entry** so it can never prompt again. Included if cheap; the
  re-login path is the guaranteed fallback and the documented default.

## 6. Documentation deliverable (honest trust model)

README / login help gains a short, plain-language section:

> Your Monarch session is stored in a file only your user account can read
> (`~/.financial_mcp/session.json`, permissions `600`) — the same way the GitHub CLI
> stores its token. This protects against accidental sharing and other users on your
> computer. It is **not** encrypted at rest; rely on your disk encryption (FileVault) for
> a lost or stolen machine. For OS-managed encryption at rest, set
> `credential_store: keyring` in your config (macOS may prompt once to allow access).

No overpromising: "permission-locked," never "encrypted," for the file store.

## 7. Refusals / failure behavior (all clear, never a bare stack trace)

- Parent dir or file cannot be created/written at login → a clear, actionable error
  naming the path and the OS reason.
- File unreadable / corrupt / wrong shape at startup → `load_session` returns `None`; the
  server proceeds on its existing unauthenticated path and logs one non-secret line.
- `credential_store: keyring` selected but `keyring` backend unavailable → the existing
  keyring error surfaces (unchanged from today).

## 8. Testing

New `tests/test_credentials.py` (unit, offline, `tmp_path`):
- `FileCredentialStore`: save→load round-trips the exact blob; file is `0o600` and parent
  dir `0o700`; a pre-loosened `0o644` file is tightened to `0o600` on load (+ warning);
  missing → `None`; corrupt JSON → `None`; JSON missing a required key → `None`; write is
  atomic (no leftover temp file); delete removes the file and is a no-op when absent.
- `KeyringCredentialStore`: today's behavior re-run against the interface with `keyring`
  mocked — proves the opt-in path is unchanged.
- `make_credential_store(cfg)`: `FileCredentialStore` by default, `KeyringCredentialStore`
  when `credential_store: keyring`, honors `credential_file`.
- **Regression fence:** `main.py` "no session → unauthenticated fallback" and
  `login_cli` save/delete stay green; auth-importing tests updated only for the moved
  module path.

## 9. What this enables next

- Removes the scary, unbranded credential prompt from the default experience — the trust
  blocker for handing this MCP to a non-technical person (Goal #3, sharing).
- Establishes the `CredentialStore` seam so a future passphrase-encrypted backend (for
  at-rest encryption without the keyring) is a third implementation and a factory branch,
  nothing more.

## 10. Two design insights captured here (surfaced during the Slice 3 live test)

Recorded for context; neither changes this slice, both inform the shareable future:
1. **The confirm handshake is not a human-in-loop pause.** It guards against
   accidental/stale writes (content-hash token, re-checked at confirm), but an agent can
   fetch its own dry-run token and replay it in one turn. If a shared instance ever needs
   true human gating, that is a separate mechanism, not the handshake.
2. **Create/delete undo asymmetry.** `delete_category` is reversible (undo = recreate);
   `create_category` is journaled but not auto-undoable (undoing a create = a delete,
   deferred in Slice 2). A future slice could make create undoable for a symmetric undo
   stack, or add a docstring warning. Documented design choice, not a bug.
