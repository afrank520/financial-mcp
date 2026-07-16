# Monarch Money MCP Server — Design Spec

**Date:** 2026-07-15
**Author:** Adam Frank (with Claude)
**Status:** Draft design, pending approval

---

## 1. Problem Statement

Monarch Money ran an official hosted MCP server (`https://api.monarch.com/mcp`) that let
Claude connect to a user's Monarch account and both analyze and clean up financial data.
Monarch shut that service off and now encourages customers to build their own using the
available community resources.

This project rebuilds that capability as a **personal-use, local MCP server** that connects
the Claude Desktop app (signed into Adam's personal Claude account) to Adam's Monarch Money
account. It lets Claude answer natural-language questions about finances, run rich analytics,
and — with guardrails — clean up transactions in bulk (the single most valued capability of
the original service).

It is intentionally conservative: local-only, private, cheap, and built to be maintained for
years. It is the "Monarch MCP integration — Phase 2" that the existing `teamfrank` financial
calculator spec (2026-06-15) deferred, built as its own standalone project.

## 2. Goals

- Launch a local MCP server that the Claude Desktop app connects to over stdio.
- Authenticate securely with Monarch Money outside of Claude; never expose credentials to Claude.
- Cache financial data locally in SQLite so Claude answers most questions instantly and keeps
  working when Monarch is unavailable.
- Expose a broad set of read/analytics MCP tools (accounts, transactions, budgets, cashflow,
  net worth, holdings, spending summaries, subscriptions, forecasting).
- Support **bulk transaction cleanup** (categorize, tag, set merchant, set budget amount)
  behind dry-run confirmation and a batch cap, with a local audit log and an undo path.
- Keep financial data on Adam's Mac; the only external network connection is to Monarch itself.
- Zero recurring cost beyond existing Monarch and Claude subscriptions.

## 3. Non-Goals (intentionally NOT in v1)

- **Remote / hosted deployment** — no claude.ai-browser or mobile access. That would require a
  public HTTPS host, an OAuth authorization server, a monthly bill, and financial data living on
  hosted infrastructure — all of which violate the local-only privacy charter. The transport
  layer is abstracted so a future private self-host is an additive change, not a rewrite.
- No cloud services, hosted infrastructure, telemetry, analytics collection, external databases,
  API gateways, Docker/Kubernetes/containers, or web server.
- No multi-user, no auth server, no sharing — single-user local tool.
- No background sync daemon — refresh is TTL-driven on read or user/Claude-initiated.
- No writing of Monarch usernames or passwords to disk, ever.

## 4. Architecture

Single local Python process, launched by Claude Desktop over stdio.

- **Language:** Python 3.12+, modern typing throughout, Pydantic models for data shapes.
- **MCP layer:** the official MCP Python SDK. Contains almost no business logic — it validates
  inputs, calls analytics/adapter functions, and formats results.
- **Monarch integration:** the `MonarchMoneyCommunity` library (an actively maintained fork of
  the Monarch API with full MFA support), wrapped behind an **adapter** so the concrete library
  can be swapped or supplemented (e.g. CSV) without touching analytics or MCP layers.
- **Cache:** SQLite (via a thin data-access layer). Serves reads; refreshed per-type by TTL.
- **Credentials:** OS keyring (macOS Keychain) via `keyring`, with a file fallback. Stores only
  the Monarch **session token + device-uuid** — never the password.
- **Config:** one YAML file (no secrets in it).
- **Run model:** launched by Claude Desktop via `claude_desktop_config.json`. A separate
  `login` CLI is run once by the user in a terminal to establish the session.

### Project layout

```
Monarch MCP/
├── financial_mcp/
│   ├── main.py              ← entrypoint the Claude Desktop config launches (stdio server)
│   ├── login_cli.py         ← one-time interactive login (browser cookie / email+pw+MFA)
│   ├── mcp_server.py        ← MCP tool registration; thin — no business logic
│   ├── config.py            ← loads YAML config + resolves paths
│   ├── transport.py         ← transport abstraction (stdio now; remote-capable later)
│   ├── monarch/
│   │   ├── adapter.py       ← interface the rest of the app depends on (read + write methods)
│   │   ├── community_adapter.py  ← MonarchMoneyCommunity-backed implementation
│   │   ├── csv_adapter.py   ← offline/historical fallback backed by CSV exports
│   │   ├── auth.py          ← session token acquisition + keyring storage
│   │   └── models.py        ← Pydantic models for Monarch entities
│   ├── analytics/
│   │   ├── spending.py  budgeting.py  investments.py
│   │   ├── cashflow.py  networth.py   forecasting.py  subscriptions.py
│   ├── cache/
│   │   ├── sqlite_cache.py  ← schema, read/write, per-type TTL, staleness
│   │   └── audit_log.py     ← append-only record of every write + revert support
│   ├── writes/
│   │   └── mutations.py     ← Tier 1/2 write operations, dry-run + batch-cap enforcement
│   └── utils/               ← logging (with sensitive-field masking), helpers
├── tests/
│   └── fixtures/            ← small ANONYMIZED slice copied from real CSV exports
├── docs/
│   ├── specs/               ← this file
│   ├── README.md  INSTALL.md  ARCHITECTURE.md  SECURITY.md  BACKUP.md  UPGRADING.md  CHANGELOG.md
├── config.example.yaml
├── pyproject.toml
└── .gitignore               ← ignores real CSVs, db, keyring fallback file, .env
```

The MCP layer calls analytics (for reads) and `writes/mutations.py` (for writes); both depend
only on the **adapter interface**, never on the concrete Monarch library.

## 5. Authentication

Authentication happens **outside Claude**, in a one-time terminal step. Credentials never pass
through Claude and passwords are never persisted.

**`python -m financial_mcp.login_cli`** offers two paths:

1. **Browser session cookie (recommended, primary).** User copies the `cookie:` header from a
   logged-in Monarch browser tab (DevTools). Sidesteps Cloudflare CAPTCHA gates and SSO issues
   that block programmatic login. Most reliable.
2. **Email + password + MFA (TOTP / email OTP) (fallback).** Works when Cloudflare is not gating.

Either path yields a Monarch **session token**; login requests `trusted_device=True` for a
long-lived session and rejects short-lived tokens. The token and `device-uuid` are stored
encrypted in the macOS Keychain (file fallback for keyring-less environments).

The MCP server runs headless: it loads the stored token on startup and never prompts. When the
token expires (weeks/months), tools return a clear "please re-run `login_cli`" message rather
than failing cryptically. A future opt-in flag may store a TOTP seed for fully automatic
re-login, but that is not in v1 (it weakens MFA by co-locating the second factor).

## 6. SQLite Cache & Freshness

Cache tables: accounts, transactions, balances, investments, holdings, budgets, categories,
merchants, net worth history, cash flow history. Every cached record carries a **last-refresh
timestamp, source, and schema version**.

**Per-type TTLs (tiered freshness)** — each type expires on a cadence matching how fast it
changes. Defaults (all configurable in YAML):

| Data type | Default TTL |
|---|---|
| Transactions, balances | 12 hours |
| Investments, holdings | 12 hours |
| Budgets, net worth history, cash flow history | 24 hours |
| Categories, merchants (reference lists) | 7 days |

On a read, if the type's data is within TTL it is served from cache instantly; if expired, the
adapter refreshes that type from Monarch, updates the cache, then serves it. If **Monarch is
unreachable**, the cache serves whatever it has and annotates the answer with the data's age.
Tools `refresh_cache` (force a pull, all or by type) and `cache_status` (per-type freshness)
are always available. A write operation invalidates the affected cached type so the next read
reflects the change.

## 7. Write Posture & Guardrails

Reads are free; writes are gated; destructive writes are gated hardest. Governed by a single
config value **`write_mode: off | safe | full`**, shipping default **`safe`**.

- **Tier 0 — Reads.** All analytics/query/reference tools. No gate. `write_mode: off` allows only these.
- **Tier 1 — Safe/reversible writes** (the cleanup use case): categorize transactions, set tags,
  update notes, mark reviewed, split a transaction, set/update merchant, set budget amount.
  Enabled at `write_mode: safe` and `full`. Guardrails:
  - **`dry_run` defaults to true** — the tool returns exactly what *would* change; nothing is
    written until Claude re-invokes with `dry_run=false` after the user confirms.
  - **Batch cap** — configurable max records per operation, **default 200**.
  - **Audit log** — every applied change recorded append-only in SQLite (entity id, field,
    old value → new value, timestamp, operation id).
- **Tier 2 — Destructive/irreversible writes**: create/delete transaction, delete goal, merge
  merchants, create/update/delete category, and create/update/delete transaction rules. Enabled
  only at `write_mode: full`. Always require explicit confirmation; never permitted to skip the
  dry-run preview in a bulk call. Rules are Tier 2 because they alter *future* categorization, not
  just existing records.
- **`revert_last_batch`** — walks the audit log backward to undo the most recent Tier 1 batch,
  providing an "oops" button the raw Monarch API lacks.

**Named risk:** enabling writes reintroduces LLM-influence risk (adversarial text in transaction
data nudging Claude toward an unintended mutation). Dry-run-by-default, the batch cap, the audit
log, and keeping the human in the confirm loop are the mitigations.

## 8. MCP Tools

**Reads / analytics:** `get_accounts`, `get_account`, `get_transactions`, `search_transactions`,
`get_transaction_details`, `get_transactions_needing_review`, `get_recurring_transactions`,
`get_transaction_splits`, `get_budget`, `get_cashflow`, `get_networth`, `get_holdings`,
`merchant_summary`, `category_summary`, `monthly_spending`, `yearly_spending`, `income_report`,
`subscription_report`, `investment_summary`, `forecast_cash`.

**Reference reads:** `get_categories`, `get_category_groups`, `get_merchant`, `get_tags`. (Also used
internally by the write tools to resolve names → ids.)

**Cache / ops:** `refresh_cache`, `cache_status`, `health_check`.

**Writes (gated by `write_mode`):**

- *Tier 1 — safe/reversible:* `categorize_transactions`, `set_transaction_tags`,
  `update_transaction_notes`, `mark_transaction_reviewed`, `split_transaction`, `update_merchant`,
  `set_budget_amount`.
- *Tier 2 — destructive/irreversible:* `create_transaction`, `delete_transaction`, `delete_goal`,
  `merge_merchants`, `create_category`, `update_category`, `delete_category`,
  `create_transaction_rule`, `update_transaction_rule`, `delete_transaction_rule`.
- *Undo:* `revert_last_batch`.

Transaction **rules** are Tier 2 on purpose: a bad rule silently mis-categorizes *future*
transactions, so it sits behind `write_mode: full` and never skips the dry-run preview.

## 9. Error Handling

Gracefully handle: expired sessions (→ clear re-login instruction), network failures and Monarch
outages (→ serve stale cache with age note), rate limiting (→ back off, prefer cache), missing
data, and database corruption (→ detect and offer rebuild-from-Monarch). All tool errors return
clear, user-facing messages, never raw stack traces.

## 10. Logging

Structured logging with configurable levels. Never logs credentials, session tokens, account
numbers, or PII; sensitive fields are masked. Local log file only — no telemetry.

## 11. Configuration

One YAML file. Includes: database location, per-type cache TTLs, logging level, `write_mode`,
batch cap, and feature flags. **No secrets** ever live in config — credentials are in the keyring.
A committed `config.example.yaml` documents every option.

## 12. Testing

pytest. Unit tests for analytics (pure functions over known inputs), cache TTL/staleness logic,
and write-guardrail enforcement (dry-run, batch cap, `write_mode` gating). Integration tests use
**mocked Monarch responses** and the **CSV adapter seeded from an anonymized fixture slice**
(copied from the real exports; originals never modified and remain git-ignored). Target ≥90%
coverage on analytics and guardrail logic.

## 13. Data Handling

The real `Transactions_*.csv` and `Balances_*.csv` exports in the parent repo are **read
copy-only**: a small, anonymized slice is copied into `tests/fixtures/`. The original files are
never modified and stay git-ignored. The live SQLite cache DB is git-ignored.

## 14. Documentation

README, INSTALL (incl. Claude Desktop `claude_desktop_config.json` snippet and the one-time
`login_cli` steps), ARCHITECTURE, SECURITY (credential handling, write guardrails, LLM-influence
risk), BACKUP (cache/keyring backup + restore), UPGRADING (library-break recovery via adapter),
CHANGELOG. Diagrams where useful.

## 15. Git Discipline

Standalone git history in `Monarch MCP/`, separate from `teamfrank`. Clean commit after each
milestone: scaffold/config, auth, cache, adapter, analytics, MCP read tools, write tools +
guardrails, tests, docs.

## 16. Definition of Done

Adam can: launch the local server; connect the Claude Desktop app via standard MCP config;
authenticate securely with Monarch (browser-cookie or MFA) with only the session token stored in
Keychain; cache data locally in SQLite; ask complex financial questions and get fast answers from
cache (and stale-with-age answers when Monarch is down); refresh on demand or by TTL; run bulk
transaction cleanup with dry-run confirmation, batch cap, audit log, and revert; all locally with
no recurring cost.
