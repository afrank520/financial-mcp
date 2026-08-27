# Monarch MCP

A local [Model Context Protocol](https://modelcontextprotocol.io) server that
lets Claude **read and safely maintain your [Monarch Money](https://www.monarchmoney.com)
data** — accounts, transactions, budgets, investments — right from the Claude
Desktop app. It runs entirely on your own Mac. Nothing is hosted, and your data
never leaves your machine except to talk to Monarch's own API.

> **Unofficial.** This is an independent tool, not affiliated with or endorsed
> by Monarch Money. It uses your logged-in browser session because Monarch has
> no public API. Use at your own risk.

> 👉 **New here, or not a developer?** Start with **[QUICKSTART.md](QUICKSTART.md)** —
> a hold-your-hand, zero-to-running walkthrough for the Claude Desktop app (and a
> short copy-paste path for Claude Code). The rest of this README is the overview;
> [`docs/INSTALL.md`](docs/INSTALL.md) is the concise technical reference.

## What it does

**See your money.** Ask Claude questions and get penny-accurate answers computed
on the server (not hand-summed in chat):

- Account balances and net worth, with balance history and point-in-time lookups
- Spending and cash flow over any date range, by category or merchant
- Budget status (income vs. expense vs. transfer), category and group roll-ups
- Savings rate and a where-it-went reconciliation
- Investment holdings with gain/loss, and recurring/subscription totals
- Net-worth trend over time

**Maintain your money — safely.** A guarded write layer that can clean up and
organize your data, with a real safety model behind every change:

- Recategorize transactions (one or in bulk)
- Create, rename, and delete categories
- Consolidate messy merchant names and rename merchants in bulk
- Create, update, and delete transaction rules (merchant renames and
  auto-categorization on future imports)

## The safety model

Writing to real financial records is treated as a first-class risk, not an
afterthought:

- **Write gate.** Three modes, set in config: `off` (read-only — can never
  mutate anything), `safe` (reversible writes only), or `full` (adds
  destructive operations such as deleting a category). Set `write_mode: "off"`
  for a strictly read-only install.
- **Dry-run → confirm.** Every write previews exactly what it will change and
  returns a confirmation token. Nothing is written until you confirm that exact
  change-set.
- **Undo.** Writes are journaled locally and reversible with a single
  `undo_last` — via compensating changes, not guesswork.
- **Fail-closed.** Ambiguous names, unresolved categories, or rules too complex
  to edit safely are refused with a clear message rather than guessed at.
- **Batch caps.** Bulk operations are bounded so a wrong filter can't run away.

## Where your credentials live

Your Monarch session is stored in a file only your user account can read
(`~/.financial_mcp/session.json`, permissions `600`) — the same way the GitHub
CLI stores its token. This protects against accidental sharing and other users
on your computer. It is **not** encrypted at rest; rely on your disk encryption
(e.g. FileVault) to protect a lost or stolen machine.

For OS-managed encryption at rest, set `credential_store: keyring` in your
config (macOS may prompt once to allow access). To move an existing keyring
session into the file store, run `financial-mcp-login --migrate-from-keyring`.

## Getting started

See **[docs/INSTALL.md](docs/INSTALL.md)** — install, log in (a browser window
opens; sign in normally), point it at your config, and wire it into the Claude
Desktop app.

## Roadmap

v1 is the "maintenance" layer above. Planned next, as a separate advisory layer
built on top of it:

- A `financial_context` resource — a home for the parts of your financial
  picture that don't live in Monarch (house rules, levers, targets) so advice
  can reason against your actual goals
- Goal and budget *editing* (v1 reads budgets but does not write them)
- Longer-horizon planning tools (e.g. debt glide-paths)

> Any future advisory features are **not financial advice** — they're a way to
> reason about your own numbers, not a substitute for a professional.

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE).
