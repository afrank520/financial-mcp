# Changelog

All notable changes to this project are documented here. This project adheres
to [Semantic Versioning](https://semver.org).

## [Unreleased]

### Added
- **Budget write** — a gated `set_budget` tool sets or changes a category's monthly
  budget behind the dry-run → confirm → undo safety flow, defaulting to this-month-only
  and prompting for this-month-vs-ongoing scope. Works for already-budgeted categories
  and for ones that aren't budgeted yet (currently $0); when a category has no budget,
  the dry-run carries a `warning` so you confirm adding a new budget on purpose.

## [1.3.0] — 2026-09-27

The advisory layer's first piece: your financial plan gets a durable home.

### Added
- **Plan store (`plan.yaml`).** The financial plan now has a durable home the
  MCP persists on your Mac and reads back every session, so it survives the chat
  and travels to any client. `plan_get` returns the plan (north_star, baseline,
  goals, guardrails, levers, trajectory, watchlist, narrative) plus which goals
  are still unfilled; `plan_update` creates or replaces it entirely through
  conversation (dry-run → confirm, one-level undo via `plan_undo`) — you never
  edit a file by hand. Independent of `write_mode`: the plan is your own local
  intent, not a change to your Monarch records.

### Changed
- **Dollars-aware trust gate.** A duplicate-holding finding (I-9) whose
  double-counted amount is exactly $0.00 no longer flips `trust` to *degraded*
  or leads the brief — a $0 double-count distorts nothing, so it is treated as
  materially inert. The finding stays visible in `integrity_check` and
  `findings_list` (nothing is hidden); only its weight on the trust flag and
  ranking drops. The gate is opt-in per finding: it applies only where the
  dollar amount *is* the distortion, so genuinely serious $0 findings (not
  connected live, stale feed, refresh failure, duplicate transaction ids) keep
  degrading trust as before. (A $0 duplicate stays surfaced every run rather
  than aging to backlog; dismiss-with-reason if you want it held.)

### Security
- **Plan file locked to owner-only (`600`).** `plan.yaml` (and its one-slot
  backup) holds real income, expenses, and balances, so it is now written with
  the same permission posture as the saved session — every write path (first
  seed, save, backup, undo) leaves it readable only by your user account,
  instead of the world-readable default a plain file write produced.

The plan store above is the first piece of the advisory layer that builds on
the trustworthy-data foundation below. Next up: the tools that measure behavior
against the plan — adherence to the trajectory, divergence from the goals.
**More is on the way.**

## [1.2.0] — 2026-09-25

The trustworthy-data milestone: everything an advisor has to get right *before*
it can advise — telling the truth about the data, knowing when *not* to trust
it, and staying connected without a Terminal trip.

### Honest data
- **`net_worth` tool.** Computes net worth the way Monarch defines it (sum of
  in-net-worth balances, excluding carry-forward placeholders), reported as of
  the latest real snapshot date rather than a naive same-day sum — closing a
  ~$436k overstatement the old roll-up could produce.
- **Read tools that stop quietly lying.** `get_accounts` no longer drops
  accounts, `source` reflects the adapter actually in use, epoch-0/1970 dates
  no longer leak, `health_check` and `refresh_cache` report honestly, and
  duplicate transactions are de-duplicated.

### Integrity layer (the Steward)
- **On-device integrity check.** A new `integrity_check` runs eight detectors
  (I-1…I-8) over the local cache — stale feeds, carry-forward balances silently
  leaving net worth, net-worth recompute mismatches, missing budgets, refresh
  failures, duplicate transactions, and unrecognized accounts — and reports a
  `trust` flag that goes *degraded* whenever a high-confidence problem would
  distort the numbers.
- **Findings ledger.** Problems are recorded in a local `findings.db` with a
  stable identity (the same issue re-detects as one row, not a pile-up),
  dismiss-with-reason suppression, snooze, and auto-resolution when a problem
  stops recurring.
- **Policy & account registry.** A `policy.yaml` (seeded on first run, never
  overwritten) holds household rules and thresholds; `policy_get` /
  `policy_validate` read and check it. An account registry lets the Steward tell
  a benign carry-forward (a frozen 401k feed) from an alarming one (a live 529
  whose balance just left net worth), and recognize every known account so it
  only flags genuinely new ones.
- **Five new MCP tools**, all read-only or ledger-only: `integrity_check`,
  `findings_list`, `findings_update`, `policy_get`, `policy_validate`. No new
  writes to Monarch.

### Reconnect without leaving Claude
- **`reauthenticate` tool.** When the Monarch session expires mid-chat, just ask
  Claude to reconnect — a sign-in window opens on your Mac (and completes on its
  own if the browser is still signed in), then the fresh session is saved *and*
  hot-swapped into the running server, so the next request works with no Terminal
  trip and no Claude restart. Auth-expired errors now steer you to this tool.
- **Self-healing sessions.** If a request hits an expired session, the server
  now reloads the saved login from disk and retries once — so a re-auth done
  anywhere (the tool above, a Terminal `financial-mcp-login`, another chat) takes
  effect on the very next request without restarting Claude. When the login is
  genuinely dead it fails fast and prompts you to reconnect, instead of the long
  retry hang that could otherwise occur.

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
