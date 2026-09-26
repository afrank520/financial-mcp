# Monarch MCP — the plan store (`plan.yaml`), design

**Status:** design, approved to write 2026-09-25
**Branch:** `steward-plan-store`
**Increment:** first sub-project of the advisory / agentic-steward layer

---

## 1. Why this exists

Adam built a financial plan with Fable (the "TeamFrank" plan) over ~2 weeks:
separate steady-state income/expense from the noise, name the fear (HELOC draw
exhaustion → eventual inability to pay bills), engineer margin from payroll
levers (W-4, 401k/403b/529, CC cadence), recommend a HELOC refi + limit
increase, set a checking floor, model the next 12 months, predict extraordinary
income/expense events, then monitor actuals against it. North star: **pay down
as much HELOC as possible without drawing more.**

That plan **lived in a chat and a PDF, and evaporated when the hosted MCP was
shut down** — which is why Adam rebuilt this MCP. The lesson: a plan is worth
only as much as its persistence. The advisory layer needs somewhere durable to
keep the plan so any client (Claude Desktop today, anything tomorrow) can
re-ground in it every session.

This is the **keystone** of the agentic-steward loop (see
`steward-endgame-agentic-loop` memory). The loop's other organs already exist and
are live-proven:

- **Sense organ** — integrity detectors I-1…I-9 + findings ledger + `needs_input` gate.
- **Hands** — recategorize / category CRUD / merchant cleanup / rule CRUD, all dry-run→confirm→undo.
- **Data lenses** — balances, net worth, windowed spending, budget, savings rate, holdings, recurring, net-worth trend.

What's missing is the **plan to measure against**. This spec builds exactly that,
and nothing more.

## 2. Scope

### In scope (v1)
- A seeded, never-clobbered `~/.financial_mcp/plan.yaml` holding the plan.
- A `Plan` module (`financial_mcp/plan.py`) mirroring `financial_mcp/steward/policy.py`.
- **`plan_get`** — read tool returning the parsed plan + validation of unfilled goals.
- **`plan_update`** — gated write tool (dry-run→confirm→undo) so the plan is
  **authored and edited entirely in Claude Desktop conversation**, never by
  hand-editing a file.

### Explicitly out of scope (later sub-projects)
- **Adherence computation** (actuals vs trajectory) — sub-project C.
- **Authoring lenses** (multi-year steady-state income/expense normalization) — sub-project B.
- **The intake recipe** (the ordered interview questions) — a Claude Skill, not
  hardcoded here. The MCP persists whatever plan Claude assembles; it does not
  drive the interview.
- **Scheduling / recurring trigger** — sub-project E (likely Claude Desktop, maybe zero code).

## 3. The lifecycle (the load-bearing requirement)

**Hard requirement (Adam, 2026-09-25):** the plan is *created and edited entirely
through Claude Desktop conversation.* If any step requires Adam to open or edit a
file on disk, the design has failed.

```
CREATION (100% in-conversation, zero file-touching):
  Adam: "Let's build my financial plan."
  Claude: [runs the Fable-style intake — steady-state income/expense, the fear,
           the levers, the glide-path, guardrails, watchlist...]
  Claude: [assembles the full plan doc, calls plan_update(plan=<doc>)]
          → dry-run: returns the proposed plan + a confirm_token; writes NOTHING
  Claude: shows Adam the plan, asks him to confirm
  Adam: "confirm"
  Claude: [calls plan_update(plan=<doc>, confirm_token=<token>)]
          → writes ~/.financial_mcp/plan.yaml atomically; keeps prior version for undo
          → "Saved. Here's your plan."

LOADING (every later session):
  Claude: [calls plan_get] → reads the file → re-grounds in the plan instantly.
  Adam never opens the file.

EDITING (same path as creation):
  Adam: "Move my HELOC target to June 2027."
  Claude: [plan_get to load current] → [plan_update with the whole updated doc]
          → dry-run → confirm → written; previous version retained for undo.
```

The **seed file** is *not* something Adam fills in. It is an empty/example
starter template that `plan_update` overwrites on first author. **The conversation
is the questionnaire.**

## 4. Architecture

```
plan_seed.yaml (in repo)
        │ first load (seed-if-absent, never overwrite)
        ▼
~/.financial_mcp/plan.yaml  ◀── plan_update writes (atomic, +1 undo version) ──┐
        │                                                                       │
        │ plan_get reads                                                        │
        ▼                                                                       │
   Claude Desktop ── authors/edits via conversation ──────────────────────────┘
        │
        └── reasons against LIVE actuals from existing Monarch tools
            (plan holds targets/intent only; never a copy of live balances)
```

Key properties, all mirrored from `policy.py`:
- Seed ships in-repo at `financial_mcp/plan_seed.yaml`.
- On first `load_plan()`, seed is copied to `~/.financial_mcp/plan.yaml`
  (via `config.resolve_user_path` + `config.ensure_parent`). This path is
  **outside the code folder**, so it survives code updates.
- An existing file is **never overwritten** by the seed loader (only by an
  explicit, confirmed `plan_update`).
- The plan stores **targets and intent only.** Every **actual** comes live from
  the existing Monarch read tools at reason-time — the plan is never a snapshot
  of balances.

## 5. The `Plan` module (`financial_mcp/plan.py`)

Mirrors `financial_mcp/steward/policy.py` almost line-for-line:

```
_SEED_PATH = Path(__file__).parent / "plan_seed.yaml"

class Plan:
    def __init__(self, raw: dict) -> None: self.raw = raw or {}

    @classmethod
    def from_seed(cls) -> "Plan": ...        # for tests / defaults

    # typed accessors for the load-bearing bits the future loop reads:
    @property
    def north_star(self) -> str | None: ...
    @property
    def goals(self) -> list[dict]: ...
    @property
    def trajectory(self) -> list[dict]: ...

    def validate(self) -> dict:
        # report unfilled goals so a future adherence detector can emit ONE
        # needs_input instead of guessing (same pattern as Policy.validate()).
        # "unfilled" = None or empty container.
        return {"valid": <bool>, "missing_required": [<hints>]}

def _plan_path() -> str: return config.resolve_user_path("plan.yaml")

def load_plan() -> Plan:
    # seed-if-absent, else read; malformed YAML fails loud.

def save_plan(raw: dict) -> None:
    # atomic write (temp-file + os.replace), keep ONE prior version for undo.
```

`validate()` required-goal set (v1): `north_star`, at least one `goals` entry
with a `target` + `target_date`, and a non-empty `trajectory`. Exact hints
finalized during implementation; the shape matches `Policy._REQUIRED_GOALS`.

## 6. The schema — 8 sections, seeded with the real TeamFrank plan as worked example

Seeded so a new user sees a *real* filled example (not an empty abstraction), but
with generic-enough keys that it reads as a template.

**Privacy of the committed seed (approved 2026-09-25):** the seed ships in the
public open-core repo, so it uses the *structure* of the TeamFrank plan with
**placeholder numbers** (e.g. `paydown_target: <amount>`, generic dates) — never
Adam's real balances or figures. Real numbers land only in
`~/.financial_mcp/plan.yaml` on Adam's own machine, via `plan_update`
conversation. The seed is a shape-accurate worked example, not a disclosure.

Sections:

| Section | Holds | Example (from TeamFrank) |
|---|---|---|
| `north_star` | one prose sentence | "Pay down as much HELOC as possible without drawing more." |
| `baseline` | steady-state income, steady-state expense, the margin between | monthly steady income / expense / net margin |
| `goals` | list: `id`, `metric`, `target`, `target_date`, `tracks` (account/what) | HELOC → `paydown_target` by 2027-06; checking floor |
| `guardrails` | tripwires | "checking < $6K = pause & look"; "draws must be named"; "surprise > $5K = a decision" |
| `levers` | margin adjustments + effect notes | W-4 calibration, 401k/403b/529 pauses, HELOC refi + limit ↑, CC cadence |
| `trajectory` | dated checkpoints a metric is expected to hit — the glide-path | HELOC balance expected per month → ~$202K by 2027-06 |
| `watchlist` | predicted extraordinary income/expense events to stress-test | RSU vests, bonuses, large one-off expenses |
| `narrative` | free prose: framing/strategy YAML can't hold | "transactions are noise; checkpoints are signal" |

### 6.1 Overlap with `policy.yaml` — deliberate, documented
`policy.yaml` already holds some of these (cash_floor, `draws_must_be_named`, RSU
levers). We seed all 8 sections **and** annotate the overlapping fields in the
seed:

> `# policy.yaml is the source of truth for the live threshold detectors read;
> this plan field records the plan's INTENT.`

So `policy.yaml.cash.cash_floor` is the number I-detectors enforce; the plan's
guardrail records *why* $6K and what to do when breached. No code couples them in
v1; the comment prevents silent drift. (A later slice may make the plan reference
policy values directly — out of scope now.)

## 7. The tools

### 7.1 `plan_get` (read)
```
@server.tool()
def plan_get() -> dict:
    """Return the full plan (north_star, baseline, goals, guardrails, levers,
    trajectory, watchlist, narrative) plus which goals are still unfilled.
    Read this at the START of any planning or steward conversation to re-ground
    in the plan before reasoning against live data."""
    return _guard(lambda: {"plan": load_plan().raw, "validation": load_plan().validate()})
```
Docstring steers Claude to load it first. No gating — it's a read.

### 7.2 `plan_update` (gated write, full-replace)
Reuses the **exact** dry-run→confirm→undo pattern of the Monarch write tools
(`create_merchant_rule` et al.): a content-hash `confirm_token`.

```
@server.tool()
def plan_update(plan: dict, confirm_token: str | None = None) -> dict:
    """Create or replace the financial plan. Full-replace: send the ENTIRE plan
    document (after plan_get you already hold the current one, so edit-in-place =
    re-send the whole doc). Two-phase: call without confirm_token to preview
    (writes NOTHING, returns the proposed plan + a confirm_token); call again with
    the matching token to write. Reversible via undo_last (restores the prior
    version)."""
```

Behavior:
1. **Validate shape** — must be a dict; unknown top-level keys rejected (fail
   closed, so a typo can't silently create a junk section); each known section
   type-checked (lists are lists, `north_star` is a string, etc.). Malformed →
   `{"error": ...}`, nothing written.
2. **Dry-run** — compute `token = hash(canonical(plan))`. If `confirm_token` is
   absent or ≠ token: return `{"dry_run": True, "plan": <normalized>,
   "confirm_token": token, "diff_summary": <what changes vs current>,
   "note": "nothing written yet"}`.
3. **Commit** — token matches: atomic write via `save_plan`; retain the single
   prior version for undo; return `{"saved": True, "undo_hint": "call undo_last
   to restore the previous plan"}`.

### 7.3 Undo
`plan_update` records into the existing op-log/journal so **`undo_last`** restores
the prior `plan.yaml` (a compensating file-restore), consistent with how the other
write tools register undo. If integrating a file-restore op into the existing
journal proves heavier than a self-contained `.plan.bak` swap, v1 may use a
dedicated single-slot backup that `undo_last` (or a `plan_undo`) restores —
decided at implementation time; the **user-visible contract is "one level of
undo."**

## 8. The `write_mode` decision (approved)

`plan_update` is **NOT** behind the `write_mode: off/safe/full` gate.

Rationale: `write_mode` gates tools that **mutate the user's Monarch records** (an
external service). `plan.yaml` is the user's **own local intent on their own
disk** — no external service is touched. Coupling them would force a user to
enable Monarch-mutation powers just to write down a goal. `plan_update` keeps its
**own** dry-run→confirm→undo safety, independent of `write_mode`.

Consequence: `plan_get` / `plan_update` register in `build_server`
**unconditionally** (like the core read tools), not inside the optional `steward=`
block and not inside the `write_mode` gate. They are always available.

## 9. Error handling & failure posture

- **Malformed YAML on load** → fail loud (surface the parse error), never
  silently return an empty plan (an empty plan would read as "no plan," masking a
  corrupted file).
- **Absent file** → seed cleanly on first `load_plan()`.
- **Existing file** → never overwritten by the seed path; only by a confirmed
  `plan_update`.
- **`plan_update` write failure** (disk error mid-write) → atomic temp+replace
  means the old file is never left half-written; return an error, prior plan intact.
- **Never** print to stdout (would corrupt MCP stdio) — return values only.

## 10. Testing (TDD)

Mirrors `tests/test_steward_policy.py` + the write-tool tests
(`tests/test_steward_mcp_tools.py`, `test_mcp_tools.py`):

**Module (`test_plan.py`):**
- seed-on-absent writes the file; content matches seed.
- existing file is **never** overwritten by `load_plan()`.
- accessors (`north_star`, `goals`, `trajectory`) read seeded values.
- `validate()` reports unfilled goals; a fully-filled plan validates clean.
- `save_plan` writes atomically and retains exactly one prior version.
- malformed YAML raises (does not return empty).

**Tools (in the MCP-tools test file):**
- `plan_get` returns `{plan, validation}` and is registered unconditionally
  (present even when no steward and `write_mode: off`).
- `plan_update` dry-run returns the proposed plan + `confirm_token`, writes NOTHING.
- `plan_update` with matching token writes; with wrong/absent token does not.
- unknown top-level key / wrong-typed section → `{"error": ...}`, nothing written.
- `undo_last` (or `plan_undo`) restores the prior plan after an update.
- `plan_update` works with `write_mode: off` (proves it is not gated).

## 11. Boundaries & non-goals (restated)

- The MCP **persists** the plan; it does **not** drive the intake interview (that
  recipe is a future Claude Skill).
- The MCP does **not** compute adherence in v1 (sub-project C); `validate()` only
  reports *unfilled* goals, not *missed* ones.
- No multi-year steady-state lens here (sub-project B).
- No scheduler (sub-project E).
- The plan holds **intent/targets only** — never a stored copy of live balances.

## 12. Open items for implementation

1. Exact `validate()` required-goal set + hint strings (finalize against the seed).
2. Undo mechanism: fold into existing journal vs. dedicated single-slot backup
   (§7.3) — pick the lighter correct option; the contract is one level of undo.
3. `diff_summary` in the dry-run: how rich (full field-level diff vs. section-level
   "goals changed, trajectory changed") — start section-level, enrich only if useful.
