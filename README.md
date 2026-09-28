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

> 🔄 **Already installed and want the latest version?** See
> **[UPDATING.md](UPDATING.md)** — dead-simple update steps that keep your
> Monarch sign-in and data intact.

> 💻 **This runs on your computer — so it works where your computer is.** The
> server runs locally on the Mac you install it on, and your Monarch session is
> stored in a secured file on that Mac. Claude can use these tools **only on that
> computer, and only while it's running.** That means the **Claude mobile app
> can't use them** — your phone can't start or reach a program on your Mac.
> Keeping everything local is the whole point (your data never leaves your
> machine), so there's intentionally no hosted version to connect to remotely.

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
- Set the monthly budget for an existing category (this month or ongoing)

**Keep a plan — in your own words.** Your financial plan (north star, goals,
guardrails, levers, and a dated trajectory) gets a durable home on your Mac
that Claude reads back every session, so it survives the chat and travels to
any client:

- Author and revise the plan entirely through conversation — never by hand-
  editing a file — behind the same dry-run → confirm → undo safety flow
- The plan holds your *intent* (targets and rules); actuals always come live
  from Monarch, so advice reasons against both
- Independent of the write gate: the plan is your own local note to self, not
  a change to your Monarch records

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
Desktop app. If anything seems off after setup, run `financial-mcp-doctor`
— a one-command health check of your
Python version, config paths, secure connection to Monarch, and saved session.

## Roadmap

On top of the "maintenance" layer above, an advisory layer is taking shape. Its
first piece has landed: the **plan store** described above is the home for the
parts of your financial picture that don't live in Monarch (north star, goals,
guardrails, levers, targets), so advice can reason against your actual goals.

Planned next, building on it:

- Adherence tools that measure behavior against the plan — how the trajectory is
  tracking, where spending is diverging from the goals
- Goal *editing* and budget *creation/removal* (changing an existing budgeted
  category's monthly amount has landed; adding a budget to an unbudgeted
  category, removing one, and editing goals are still planned)
- Longer-horizon planning tools (e.g. debt glide-paths)

> Any future advisory features are **not financial advice** — they're a way to
> reason about your own numbers, not a substitute for a professional.

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE).
