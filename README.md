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
