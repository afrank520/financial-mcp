# Changelog

All notable changes to this project are documented here. This project adheres
to [Semantic Versioning](https://semver.org).

## [1.1.0] — 2026-08-31

Install hardening: a smoother, more reliable first-run on a clean Mac.

- **`uv`-based installer.** `install.sh` now uses `uv` to fetch a known-good
  Python 3.12 and build `.venv` itself, then runs a self-check — no more
  asking users to have the right system Python already installed.
- **`certifi`-backed TLS.** HTTPS to Monarch now uses the `certifi` CA bundle,
  so it works without the python.org "Install Certificates.command" ceremony.
- **Config path resolution.** Bare filenames (`cache.db`, etc.) now resolve
  under `~/.financial_mcp/` automatically, fixing a crash under Claude
  Desktop's `cwd=/`.
- **Honest login errors.** Sign-in failures now name the real cause instead
  of a generic "session didn't work."
- **`financial-mcp-doctor`.** A new one-command health check verifies Python
  version, config paths, secure connection to Monarch, and saved session in one
  shot.

## [1.0.0] — 2026-08-27

First release: a local MCP server that lets Claude read and safely maintain your
Monarch Money data, running entirely on your own Mac.

### Read & analyze
- Accounts, transactions, and net worth
- Balance history, point-in-time balance lookups, and balance deltas
- Date-range spending and cash-flow summaries, by category or merchant
- Budget status split by income / expense / transfer, with category and group
  roll-ups
- Savings rate with a where-it-went reconciliation
- Investment holdings with gain/loss, and recurring/subscription totals
- Net-worth trend over time
- Freshness envelope on every result (as-of timestamp + staleness) and a
  local cache with per-type TTLs

### Maintain (guarded write layer)
- Recategorize transactions, individually or in bulk
- Create, rename, and delete categories
- Consolidate messy merchant names and rename merchants in bulk
- Create, update, and delete transaction rules (merchant renames and
  auto-categorization on future imports)
- `undo_last` reverses the most recent write via compensating changes

### Safety
- Write gate (`off` / `safe` / `full`) — a read-only install can never mutate
- Dry-run → confirm handshake on every write (nothing writes without confirming
  the exact change-set)
- Local, reversible write journal
- Fail-closed name/category/rule resolution
- Batch caps on bulk operations

### Auth & storage
- One-time browser-driven sign-in (`financial-mcp-login`), with a manual
  cURL-paste fallback
- File-first credential store (`~/.financial_mcp/session.json`, `600`) with an
  opt-in OS keyring backend and a migration path between them

### Not in this release (planned as a separate advisory layer)
- A `financial_context` resource for goals/rules/targets that don't live in
  Monarch
- Goal and budget *editing* (budgets are read-only in v1)
- Longer-horizon planning tools (e.g. debt glide-paths)
