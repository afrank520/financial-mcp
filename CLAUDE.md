# Monarch MCP — agent guide

This repo is a **local MCP server** that lets Claude read and safely maintain a
user's [Monarch Money](https://www.monarchmoney.com) data. It runs entirely on
the user's own Mac; nothing is hosted.

## If the user asks you to set this up

Run the installer and follow its prompts — it does everything (virtualenv,
install, sign-in browser, config, and connecting Claude Desktop):

```bash
./install.sh
```

That is the whole setup. Do **not** hand-roll the steps unless `install.sh`
fails; if it does, the equivalent manual steps are in [`docs/INSTALL.md`](docs/INSTALL.md),
and the beginner walkthrough is [`QUICKSTART.md`](QUICKSTART.md).

`install.sh` is safe to re-run — it's idempotent, reuses an existing `.venv`,
and never overwrites `config.yaml` or the saved Monarch login (on re-run it only
offers to flip `source` to `live` if it isn't). If the environment is broken, run
`./install.sh --fresh` to rebuild `.venv` from scratch while preserving config and
login.

Key facts to get right if you ever do it by hand:
- Always work in `.venv` (`python3 -m venv .venv`). The `financial-mcp` command
  lands at `.venv/bin/financial-mcp` — that **absolute path** is what Claude
  Desktop's config `command` must point to (a bare `financial-mcp` won't resolve).
- Run `python3 -m playwright install chromium` — the browser sign-in needs it.
- Set `source: live` in `config.yaml`. Leave `write_mode: "off"` (read-only)
  unless the user explicitly wants writes.
- Monarch sign-in uses **email + password**, never the Google button (blocked
  for automated login).

## If the user asks what it can do

It answers finance questions computed on-device (balances, net worth, spending
by date range / category / merchant, budgets, savings rate, holdings, recurring,
net-worth trend) and — only when `write_mode` is `safe`/`full` — can recategorize
transactions, manage categories, consolidate merchants, and manage transaction
rules, each behind a dry-run → confirm → undo safety flow.

## Working in this codebase

- Tests: `.venv/bin/python -m pytest` (or `pytest` with the venv active).
- Architecture and conventions live in the source under `financial_mcp/`; the
  adapters (`CommunityAdapter` live, `CsvAdapter` offline) sit behind the tools
  in `financial_mcp/mcp_server.py`.
- This is Apache-2.0 open-core; the planned advisory layer is a same-repo module,
  not a fork.
