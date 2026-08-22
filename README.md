# Monarch MCP (personal, local)

A local Model Context Protocol server that connects the Claude Desktop app to
your Monarch Money data. Personal use, runs entirely on your Mac, no hosting.

- **Plan 1 (this):** CSV-backed read server — accounts, transactions, spending
  analytics, cache with TTLs. Fully offline.
- **Plan 2:** live Monarch auth + sync.
- **Plan 3:** forecasting, subscriptions, investment/income/net-worth reports.
- **Plan 4:** guarded write-back (bulk cleanup) with dry-run, batch cap, audit
  log, and undo.

See `docs/specs/2026-07-15-monarch-mcp-server-design.md` for the full design and
`docs/INSTALL.md` to get running.

### Where your credentials live

Your Monarch session is stored in a file only your user account can read
(`~/.financial_mcp/session.json`, permissions `600`) — the same way the GitHub
CLI stores its token. This protects against accidental sharing and other users
on your computer. It is **not** encrypted at rest; rely on your disk encryption
(e.g. FileVault) to protect a lost or stolen machine.

For OS-managed encryption at rest, set `credential_store: keyring` in your
config (macOS may prompt once to allow access). To move an existing keyring
session into the file store, run `financial-mcp-login --migrate-from-keyring`.
