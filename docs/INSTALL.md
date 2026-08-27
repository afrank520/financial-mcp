# Install — Monarch MCP

Runs locally on your Mac and connects the Claude Desktop app to your Monarch
Money data. Requires Python 3.12+.

> **Not a developer?** Follow [`../QUICKSTART.md`](../QUICKSTART.md) instead — a
> step-by-step, zero-to-running guide. This file is the concise reference.

> **Just want it working fast?** From the repo root, run `./install.sh` — it does
> everything below (venv, install, Chromium, config, Monarch sign-in) and offers
> to connect the Claude Desktop app for you. The steps below are the manual
> equivalent.

## 1. Install

Work in a virtual environment so dependencies stay isolated and the
`financial-mcp` command lands at a stable path you can point Claude at:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
python3 -m playwright install chromium   # browser used only for sign-in (Step 3)
```

This installs the server and two commands: `financial-mcp` (the server) and
`financial-mcp-login` (one-time sign-in). Both live at `.venv/bin/` — note that
absolute path; Step 4 needs it.

## 2. Configure

```bash
cp config.example.yaml config.yaml
```

For live Monarch data, set `source: live` in `config.yaml`. Control writes with
`write_mode`: `"off"` (read-only), `"safe"` (reversible writes), or `"full"`
(adds destructive operations). Quote the value — bare `off` is parsed as boolean
`false` by YAML. **Never commit `config.yaml`** (it's git-ignored).

> You can also run fully offline against Monarch CSV exports by setting
> `source: csv` and pointing `csv_transactions_path` / `csv_balances_path` at
> files in a git-ignored location (e.g. `~/.financial_mcp/`). Live mode is the
> normal path; CSV mode needs no login.

## 3. Log in once (live mode)

Monarch has no public API, so the server uses your logged-in browser session.
It reads only `session_id` + `csrftoken` — never your password — and stores them
in a permission-locked file (see "Where your credentials live" in the README).

> **Google-only sign-in?** If you normally sign in to Monarch with Google, add an
> email/password to your Monarch account first (account settings → security, or
> the "Forgot password?" link on the login page) — automated local sign-in can't
> use Google's button. This does **not** disable Google sign-in; both methods
> coexist, so your Monarch website and mobile app logins with Google are
> unaffected.

```bash
financial-mcp-login
```

A browser window opens. **Sign in with your email and password** (use the
regular email/password form — the automated Google sign-in button is blocked by
Monarch's bot detection). Once you're in, the session is captured and saved
automatically.

- Cold first-time sign-in with MFA can take longer than the 180-second default.
  If it times out, retry with a longer window:
  `FINANCIAL_MCP_LOGIN_TIMEOUT=600 financial-mcp-login`
- To log out: `financial-mcp-login --logout`

### Manual fallback (if the browser login can't run)

`financial-mcp-login` falls back to this automatically, or force it with
`financial-mcp-login --curl`:

1. In your browser, open Monarch (`app.monarch.com`) and log in.
2. Open DevTools → **Network** tab, filter `graphql`.
3. Click a **POST** request to `api.monarch.com/graphql` (reload if none appear).
4. Right-click → **Copy → Copy as cURL**.
5. Paste it at the prompt, then press **Ctrl-D**.

Only `session_id` + `csrftoken` are extracted; every other cookie is discarded.

## 4. Connect the Claude Desktop app

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "monarch": {
      "command": "/absolute/path/to/repo/.venv/bin/financial-mcp",
      "env": { "FINANCIAL_MCP_CONFIG": "/absolute/path/to/config.yaml" }
    }
  }
}
```

**Use the absolute path to the venv's `financial-mcp`** (print it with
`echo "$(pwd)/.venv/bin/financial-mcp"` from the repo). Claude Desktop launches
the server outside your shell, so a bare `financial-mcp` won't be found on
`PATH`. Restart the Claude Desktop app, then ask: **"What are my account
balances?"**

For Claude Code instead of the Desktop app:

```bash
claude mcp add monarch --env FINANCIAL_MCP_CONFIG="$(pwd)/config.yaml" -- "$(pwd)/.venv/bin/financial-mcp"
```

## Sessions expire

If the Monarch session expires, tools return a clear "re-run `financial-mcp-login`"
message and the server keeps serving cached data with its age shown. Just log in
again.

## Optional: OS keyring instead of a file

Set `credential_store: keyring` in `config.yaml` for OS-managed encryption at
rest (macOS may prompt once to allow access). To move an existing keyring
session into the default file store: `financial-mcp-login --migrate-from-keyring`.
