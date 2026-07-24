# Monarch MCP — Plan 2.2: Browser-Driven Login (Design)

**Date:** 2026-07-24
**Status:** Design — pending user review
**Plan family:** Plan 2 (authentication). Plan 2.1 (cookie auth via cURL-paste) shipped and is live-proven; this is the Plan 2.2 usability upgrade.

---

## 1. Problem & Goal

**Problem:** Today's login (`financial-mcp-login`) requires the user to open DevTools, filter for a GraphQL request to `api.monarch.com`, "Copy as cURL," and paste the blob into a terminal. It works and is proven, but it is fiddly and impossible to ask a non-technical Monarch customer to do — which blocks the eventual Goal #3 (share the tool with other Monarch users).

**Goal:** Replace the manual cURL-paste with a browser-driven login: the user runs one command, a real browser opens to Monarch, they sign in with Google exactly as normal, and the script captures the session cookies and saves them to the macOS Keychain — no copy/paste, no DevTools.

**Scope decision (Option 3, "Adam-grade, non-cornering"):** Optimize for Adam's Mac now. Do NOT build cross-platform packaging, browser bundling, or a distributable installer yet — that is a separate, much larger effort tied to Goal #3 and should be informed by real usage first. But keep the cookie-capture logic in a clean, isolated interface so extending toward shareable later does not require a rewrite.

**Two design values (stated by the user, carried through the spec):**
1. **Seamless for the end user** — one command; a browser opens, they sign in, it closes; they never see the backend.
2. **Legible for the machinery** — one focused module with a clean interface, honest structured errors (never silent failures), and a fallback a future coding agent can reason about and troubleshoot. This is what makes it shareable and agent-friendly later.

**Success criterion:** Running `financial-mcp-login` opens a browser, the user completes Google SSO, and the session is validated + saved to Keychain with no manual copy/paste — verified live end-to-end through Claude Desktop (a tool call succeeds afterward).

---

## 2. Architecture

**Principle: Playwright is a new *cookie source*; nothing downstream changes.** Plan 2.1 already funnels every login down to one call — `auth.save_session(session_id, csrftoken, service_name)`. Plan 2.2 adds a second way to *reach* that call. Everything below the seam — Keychain storage (`auth.py`), `CommunityAdapter`, transport, cache, all MCP tools, and the `_validate()` live-check — is untouched and already proven.

Current flow (Plan 2.1):
```
cURL paste → parse_curl_cookies() → {session_id, csrftoken} → _validate() → auth.save_session()
```

Plan 2.2 adds a parallel branch into the same pipe:
```
Browser login → capture_session_via_browser() → {session_id, csrftoken} → _validate() → auth.save_session()
cURL paste ─── parse_curl_cookies() ──────────┘   (kept permanently as fallback)
```

**New unit — one focused module:** `financial_mcp/monarch/browser_login.py`, exposing a single function:

```
capture_session_via_browser(*, monarch_url, profile_dir, timeout_seconds) -> dict
```

It launches Chromium (persistent context), navigates to Monarch, waits for the user to complete Google SSO, reads `session_id` + `csrftoken` from the browser cookie jar, and returns the same `{"session_id": ..., "csrftoken": ...}` dict that `parse_curl_cookies()` returns. One job, clean interface — which is also what keeps the door open for Goal #3.

**`login_cli.py` becomes the router:**
- `financial-mcp-login` (no args) → attempt browser login; on ANY failure, fall back to the cURL-paste prompt.
- `financial-mcp-login --curl` → force the manual paste path (skips the browser entirely).
- `financial-mcp-login --logout` → unchanged (clears Keychain).

Both paths share the existing `_validate()` + `save_session()` tail, so a session captured either way is byte-identical and equally trusted.

---

## 3. The Login Experience & Cookie Capture

**What the user sees (the seamless path):**
1. Run `financial-mcp-login`.
2. A real Chromium window opens directly to Monarch's login page.
3. Sign in with Google exactly as normal.
4. The moment a valid session is detected (both cookies present AND a live accounts call succeeds), the script saves to Keychain, prints `✅ Signed in — session saved.`, and **closes the browser automatically.**
5. Done. No copy, no paste, no flags.

**How the capture works (the machinery):**
- Launch a **persistent context** — a real on-disk browser profile under a gitignored directory (`.pw-profile/`) — rather than the default automation mode. This is the Google-automation-block mitigation AND it lets the next login reuse the profile (often no re-typing).
- Navigate to the Monarch app URL; **poll the cookie jar** for `session_id` + `csrftoken` on a short interval.
- **Bounded wait** (default ~180 seconds) so it never hangs forever. If the user closes the window or the timeout elapses, exit cleanly and fall back to cURL.
- On cookies-present → run the existing `_validate()` (one live accounts call) → `save_session()`. Same trusted tail as today.

**Profile decision (user-approved):** persistent profile at `.pw-profile/` (gitignored) for maximum smoothness between logins. It is local to Adam's personal machine and never committed.

---

## 4. Error Handling & the Fallback Contract

Every failure mode exits cleanly with a message a human AND a coding agent can act on, and the cURL path is always reachable.

| Situation | Behavior | Message (intent) |
|---|---|---|
| Playwright not installed / browser binary missing | Catch import/launch error → fall back to cURL prompt | "Browser login unavailable (Playwright not installed or browser missing). Falling back to manual login." + cURL steps |
| MDM/egress blocks download or launch (known Salesforce-managed-Mac risk) | Launch throws → same fallback | Names the likely cause (blocked binary/network) so it is not a mystery, then falls back |
| Google resists automation-controlled browser | Visible in the browser; on timeout → fallback | "Didn't detect a signed-in session. Retry or use manual login." |
| User closes window / timeout (~180s) | Clean exit, no hang | "Login window closed before sign-in completed." → offer retry or cURL |
| Cookies captured but `_validate()` fails | Do not save; exit | Reuse today's "that session didn't work" message |
| `--curl` flag | Skip browser entirely → paste path | (unchanged Plan 2.1 behavior) |

**Fallback contract (HARD REQUIREMENT):** the browser path may fail for any reason and the user is never locked out. `financial-mcp-login --curl` always works, and the bare command auto-falls-back to the paste path on any browser-login error. Playwright is strictly additive — it never removes or degrades the Plan 2.1 login.

---

## 5. Dependency & Security

**Dependency:** `playwright` (Python), published by Microsoft. Added to `pyproject.toml`. Browser binary installed separately via `playwright install chromium` (~300MB, one-time).

**Security review (per Adam's skill/dependency-installation protocol):** assessed and **approved by Adam on 2026-07-24** BEFORE install. Trust: HIGH (verified partner, Microsoft). Runtime use is read-only — launch a browser, let the user log in, read only the two Monarch cookies; no credential automation, no password storage, no outbound calls of our own. A pending-scan note is at `~/.claude/SECURITY-REPORT.md.pending-plan-2.2`.

**Install-time scan (protocol Step 3B/3C — MUST run during implementation):** scan the installed package for executables/scripts/external connections/malware patterns, append results to `~/.claude/SECURITY-REPORT.md`, then delete the pending-note file.

**Known environment risks (recognition + mitigation, not blockers):**
1. **Salesforce MDM/endpoint controls** may block the browser-binary download or the browser launch, possibly with a misleading error. Adam is an employee (not a security risk) — this is context for diagnosing failures. Mitigation: cURL-paste fallback always works.
2. **Google automation detection** — mitigated by persistent-context launch; fallback to cURL if it ever refuses.
3. **Python 3.14.5** is ahead of Playwright's officially tested matrix; if `pip install playwright` complains, pin a compatible version. Known-shape issue, not a mystery.

---

## 6. Testing

- **Unit tests (automated, mock Playwright — no real browser in the suite):**
  - Cookie extraction given a fake cookie jar → returns the correct `{session_id, csrftoken}` dict.
  - Missing cookie in the jar → raises the same ValueError shape as `parse_curl_cookies`.
  - Router: when `capture_session_via_browser` raises, `login_cli` falls back to the cURL-paste path.
  - Router: `--curl` flag skips the browser path entirely.
- **Manual live verification (once, like the Plan 2.1 smoke test):** run `financial-mcp-login`, complete Google SSO, confirm "session saved," then confirm a tool call works in a fresh Claude Desktop chat. Recorded in memory as the Plan 2.2 live-proof.
- **Regression:** the full existing suite must still pass; the cURL-paste path retains its current tests unchanged.

---

## 7. Files Touched

- Create: `financial_mcp/monarch/browser_login.py` — `capture_session_via_browser()`.
- Modify: `financial_mcp/login_cli.py` — router (browser-first, cURL fallback, `--curl`, unchanged `--logout`).
- Modify: `pyproject.toml` — add `playwright` dependency.
- Modify: `.gitignore` — add `.pw-profile/`.
- Create: `tests/test_browser_login.py` — cookie-extraction + router-fallback unit tests (Playwright mocked).
- Docs: README login section — document browser-first login, `--curl` fallback, and the one-time `playwright install chromium` step.

**Unchanged (reused as-is):** `auth.py`, `community_adapter.py`, transport, cache, all MCP tools, `_validate()`.

---

## 8. Explicitly Out of Scope (YAGNI / Goal #3, later)

- Cross-platform credential stores (Windows Credential Manager, libsecret) — macOS Keychain only for now.
- Browser bundling / installer / code signing / notarization for distribution to strangers.
- Handling arbitrary Google SSO edge cases (corporate SSO, exotic 2FA, passkeys) beyond Adam's own account.
- Any change to the adapter, transport, cache, or tool surface.

---

## 9. Open Questions

None. All decisions resolved during brainstorming:
- Scope = Adam-grade, non-cornering (Option 3).
- Cookie source = Playwright launching its own Chromium (Approach A), persistent context.
- Profile = persistent, gitignored `.pw-profile/`.
- cURL-paste = permanent additive fallback, never removed.
- Dependency = Playwright (Microsoft), security-approved pre-install 2026-07-24.
