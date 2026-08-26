# create_category_rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `create_category_rule` MCP tool that creates a Monarch rule to auto-**categorize** FUTURE transactions whose merchant name *contains* a phrase — the categorize twin of the shipped `create_merchant_rule`.

**Architecture:** A near-exact mirror of the `create_merchant_rule` slice. The Monarch write shape was captured live 2026-08-25: a category rule is the SAME `CreateTransactionRuleInput` as a rename rule, but with `setMerchantAction: null` and `setCategoryAction: "<category_id>"` (a bare id string). We reuse the proven write-gate → dry-run/confirm-token → op-log journal → `undo_last`-by-delete template wholesale. Delete and re-read (`delete_transaction_rule`, `get_transaction_rules`) are reused unchanged — including the verify-by-absence fix from the delete-bug session. The adapter's create-and-diff logic is extracted into one private helper so both rule types share it (DRY).

**Tech Stack:** Python 3.14, FastMCP, pytest, sqlite3. Live Monarch access via cookie-auth GraphQL (`CommunityAdapter`).

**Spec:** No separate spec file — this is a bounded mirror of an existing shipped slice. The design authority is the shipped `create_merchant_rule` slice (memory: `plan4-create-merchant-rule-shipped`) and the live DevTools Payload capture of 2026-08-25 confirming `setCategoryAction` is a bare category-id string.

## Global Constraints

- **Future-only, never negotiable:** `applyToExistingTransactions: False` is hardcoded in the input builder, never a tool parameter. Past-transaction cleanup is `recategorize_matching`'s job.
- **Fail-closed category resolution:** the `set_category` argument is an EXISTING category name resolved to its id via `writes.resolve_category`. Never create a category, never fuzzy-match. Unknown/ambiguous name → refuse with the valid list (the `WriteError` message).
- **Write-gate:** every write path calls `_writes_enabled()` first and refuses in `write_mode == "off"`. In `"safe"` mode the dry-run/confirm-token handshake is required; in `"full"` it executes directly.
- **Never trust a Monarch rule/V2 mutation's returned boolean:** creation is verified by a before/after rule-id-set diff; deletion by absence on re-read. Do NOT reintroduce trust in a returned `deleted`/success flag.
- **Additive only:** no changes to the behavior of `create_transaction_rule` / `create_merchant_rule` / `delete_transaction_rule` beyond the DRY extraction in Task 2, which must keep `create_transaction_rule`'s observable behavior byte-identical.
- **No journal schema migration:** `create_category_rule` is a new `op_type` string in the existing generalized op-log. Do not alter the `write_journal` table shape.
- Run the full suite with `python -m pytest -q` from the repo root. Baseline is green before Task 1.

---

### Task 1: writes.py — category-rule input builder + confirm token

**Files:**
- Modify: `financial_mcp/writes.py` (append two functions after `compute_rule_token`, near line 200)
- Test: `tests/test_writes.py` (create if absent; otherwise append)

**Interfaces:**
- Consumes: nothing new (pure functions over primitives).
- Produces:
  - `_category_rule_input(contains: str, category_id: str) -> dict` — the `CreateTransactionRuleInput` for a future-only auto-categorize rule.
  - `compute_category_rule_token(contains: str, category_id: str) -> str` — sha256[:16] content hash for the confirm handshake.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_writes.py` (add `from financial_mcp import writes` at the top if the file is new):

```python
def test_category_rule_input_shape():
    inp = writes._category_rule_input("SHELL OIL", "169483061874802379")
    # Match criterion mirrors the rename rule.
    assert inp["merchantNameCriteria"] == [{"operator": "contains", "value": "SHELL OIL"}]
    # The category action carries a bare id string; merchant action is null.
    assert inp["setCategoryAction"] == "169483061874802379"
    assert inp["setMerchantAction"] is None
    # Future-only is hardcoded.
    assert inp["applyToExistingTransactions"] is False
    # categoryIds (a match criterion, not the action) stays null.
    assert inp["categoryIds"] is None


def test_category_rule_token_is_deterministic_and_distinct():
    a = writes.compute_category_rule_token("SHELL OIL", "cat1")
    b = writes.compute_category_rule_token("SHELL OIL", "cat1")
    assert a == b and len(a) == 16
    # Different category id -> different token.
    assert a != writes.compute_category_rule_token("SHELL OIL", "cat2")
    # Distinct namespace from the rename-rule token (no cross-collision).
    assert a != writes.compute_rule_token("SHELL OIL", "cat1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_writes.py -k "category_rule" -v`
Expected: FAIL with `AttributeError: module 'financial_mcp.writes' has no attribute '_category_rule_input'`

- [ ] **Step 3: Write minimal implementation**

Append to `financial_mcp/writes.py` (after `compute_rule_token`):

```python
def _category_rule_input(contains: str, category_id: str) -> dict:
    """Build the CreateTransactionRuleInput for a FUTURE-ONLY auto-categorize rule.

    The categorize twin of `_rename_rule_input`. Captured from the Monarch web
    app 2026-08-25: identical to the rename input except the action fields are
    swapped — `setMerchantAction` is null and `setCategoryAction` is the target
    category's id as a bare string. `applyToExistingTransactions` is always
    False; every other field is explicit-null to match the app.
    """
    return {
        "accountIds": None,
        "categoryIds": None,
        "amountCriteria": None,
        "merchantCriteria": None,
        "originalStatementCriteria": None,
        "merchantCriteriaUseOriginalStatement": False,
        "merchantNameCriteria": [{"operator": "contains", "value": contains}],
        "setMerchantAction": None,
        "applyToExistingTransactions": False,
        "addTagsAction": None,
        "linkGoalAction": None,
        "linkSavingsGoalAction": None,
        "reviewStatusAction": None,
        "setCategoryAction": category_id,
        "splitTransactionsAction": None,
    }


def compute_category_rule_token(contains: str, category_id: str) -> str:
    key = contains + "=>cat:" + category_id
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_writes.py -k "category_rule" -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/writes.py tests/test_writes.py
git commit -m "feat: category-rule input builder + confirm token (writes)"
```

---

### Task 2: Adapter layer — ABC + community create_category_rule (DRY extract) + CSV + data_service

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (add one abstractmethod)
- Modify: `financial_mcp/monarch/community_adapter.py` (extract shared helper; add `create_category_rule`; import `_category_rule_input`)
- Modify: `financial_mcp/monarch/csv_adapter.py` (add raising stub)
- Modify: `financial_mcp/data_service.py` (add passthrough)
- Test: `tests/test_merchant_rule_adapter.py` (append category-rule adapter tests)

**Interfaces:**
- Consumes: `writes._category_rule_input` (Task 1); existing `get_transaction_rules`, `CREATE_TRANSACTION_RULE`.
- Produces:
  - ABC + both adapters: `create_category_rule(self, contains: str, category_id: str) -> tuple[bool, str | None]`
  - `CommunityAdapter._create_rule_from_input(self, rule_input: dict) -> tuple[bool, str | None]` (private shared helper)
  - `DataService.create_category_rule(self, contains: str, category_id: str) -> tuple[bool, str | None]`
  - Reports the same 3 outcomes as `create_transaction_rule`: `(False, None)` Monarch declined; `(True, id)` created + identified; `(True, None)` created but id un-capturable.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_merchant_rule_adapter.py`:

```python
def test_create_category_rule_returns_true_and_new_id():
    ad = _seq_adapter([
        {"transactionRules": [_rule("r1")]},              # before
        {"createTransactionRuleV2": {"errors": []}},      # create
        {"transactionRules": [_rule("r1"), _rule("r2")]}, # after: r2 is new
    ])
    assert ad.create_category_rule("SHELL", "cat123") == (True, "r2")


def test_create_category_rule_returns_false_on_monarch_error():
    ad = _seq_adapter([
        {"transactionRules": []},
        {"createTransactionRuleV2": {"errors": [{"message": "bad", "code": "X"}]}},
    ])
    assert ad.create_category_rule("SHELL", "cat123") == (False, None)


def test_create_category_rule_returns_true_none_on_ambiguous_diff():
    ad = _seq_adapter([
        {"transactionRules": [_rule("r1")]},
        {"createTransactionRuleV2": {"errors": []}},
        {"transactionRules": [_rule("r1")]},   # nothing new appeared
    ])
    assert ad.create_category_rule("SHELL", "cat123") == (True, None)


def test_csv_create_category_rule_raises():
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    import pytest

    ad = CsvAdapter("unused_txns.csv", "unused_balances.csv")
    with pytest.raises(RuntimeError):
        ad.create_category_rule("SHELL", "cat123")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_merchant_rule_adapter.py -k "category_rule" -v`
Expected: FAIL with `AttributeError: 'CommunityAdapter' object has no attribute 'create_category_rule'`

- [ ] **Step 3a: Add the ABC method**

In `financial_mcp/monarch/adapter.py`, directly after the `create_transaction_rule` abstractmethod (around line 65), add:

```python
    @abc.abstractmethod
    def create_category_rule(self, contains: str, category_id: str) -> tuple[bool, str | None]: ...
```

- [ ] **Step 3b: Extract the shared helper and add the community method**

In `financial_mcp/monarch/community_adapter.py`:

First update the import (line 37) from:

```python
from financial_mcp.writes import _rename_rule_input
```

to:

```python
from financial_mcp.writes import _category_rule_input, _rename_rule_input
```

Then replace the existing `create_transaction_rule` method (lines 427-437) with the extracted helper plus two thin builders. The helper is the EXACT before/after-diff body that `create_transaction_rule` used — behavior must stay byte-identical:

```python
    def _create_rule_from_input(self, rule_input: dict) -> tuple[bool, str | None]:
        # Monarch's createTransactionRuleV2 returns NO id, so we learn it by
        # snapshotting the rule-id set before and after and diffing. Success with
        # exactly one new id -> (True, id); created but 0 or >1 new ids ->
        # (True, None) (id un-capturable, caller must not journal an undo).
        before = {r["id"] for r in self.get_transaction_rules()}
        payload = self._run(self._transport(
            "Common_CreateTransactionRuleMutationV2", CREATE_TRANSACTION_RULE,
            {"input": rule_input}))
        errors = ((payload.get("createTransactionRuleV2") or {}).get("errors")) or []
        if errors:
            return (False, None)
        after = self.get_transaction_rules()
        new = [r["id"] for r in after if r["id"] not in before]
        return (True, new[0]) if len(new) == 1 else (True, None)

    def create_transaction_rule(self, contains: str, rename_to: str) -> tuple[bool, str | None]:
        return self._create_rule_from_input(_rename_rule_input(contains, rename_to))

    def create_category_rule(self, contains: str, category_id: str) -> tuple[bool, str | None]:
        return self._create_rule_from_input(_category_rule_input(contains, category_id))
```

- [ ] **Step 3c: Add the CSV raising stub**

In `financial_mcp/monarch/csv_adapter.py`, directly after `create_transaction_rule` (around line 128), add:

```python
    def create_category_rule(self, contains: str, category_id: str) -> tuple[bool, str | None]:
        raise RuntimeError("writes are not available in CSV mode")
```

- [ ] **Step 3d: Add the data_service passthrough**

In `financial_mcp/data_service.py`, directly after `create_merchant_rule` (around line 140), add:

```python
    def create_category_rule(self, contains: str, category_id: str) -> tuple[bool, str | None]:
        return self._adapter.create_category_rule(contains, category_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_merchant_rule_adapter.py -v`
Expected: PASS — the new category-rule tests AND all the pre-existing merchant-rule/delete tests (proving the DRY extraction kept `create_transaction_rule` behavior identical).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/adapter.py financial_mcp/monarch/community_adapter.py financial_mcp/monarch/csv_adapter.py financial_mcp/data_service.py tests/test_merchant_rule_adapter.py
git commit -m "feat: create_category_rule across adapter layer (shared create helper)"
```

---

### Task 3: undo_journal.py — reversible op + history summary

**Files:**
- Modify: `financial_mcp/cache/undo_journal.py` (add op to `REVERSIBLE_OPS`; add history branch)
- Test: `tests/test_undo_journal.py` (append)

**Interfaces:**
- Consumes: existing `record` / `history` / `last_undoable_batch` API — no signature changes.
- Produces: `"create_category_rule"` recognized as a reversible op; a distinct history summary line for it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_undo_journal.py` (follow the file's existing construction pattern for `UndoJournal`; use a `tmp_path` db like the other tests):

```python
def test_create_category_rule_is_reversible_and_summarized(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal, REVERSIBLE_OPS

    assert "create_category_rule" in REVERSIBLE_OPS

    j = UndoJournal(str(tmp_path / "j.db"))
    # Mirror the tool's record call: target=rule_id, old/label_old=contains,
    # new/label_new=category name.
    j.record("b1", "create_category_rule", "rule9", "SHELL", "Gas", "SHELL", "Gas")
    j.mark_succeeded("b1", "rule9")

    assert j.last_undoable_batch() == "b1"
    hist = j.history()
    assert hist[0]["summary"] == "Rule: contains 'SHELL' → category 'Gas' ×1"
    assert hist[0]["undone"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_undo_journal.py -k "create_category_rule" -v`
Expected: FAIL — `assert "create_category_rule" in REVERSIBLE_OPS` fails (op not yet registered).

- [ ] **Step 3a: Register the reversible op**

In `financial_mcp/cache/undo_journal.py`, change line 9 from:

```python
REVERSIBLE_OPS = ("recategorize", "rename_category", "delete_category", "rename_merchant", "create_rule")
```

to:

```python
REVERSIBLE_OPS = ("recategorize", "rename_category", "delete_category", "rename_merchant", "create_rule", "create_category_rule")
```

- [ ] **Step 3b: Add the history summary branch**

In the `history` method, in the `if/elif` chain that builds `summary` (around lines 159-168), add a branch for the new op immediately after the `create_rule` branch:

```python
            elif op_type == "create_category_rule":
                summary = f"Rule: contains '{old_n}' → category '{new_n}' ×{n}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_undo_journal.py -v`
Expected: PASS — the new test plus all existing journal tests.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/cache/undo_journal.py tests/test_undo_journal.py
git commit -m "feat: create_category_rule is a reversible op with its own history summary"
```

---

### Task 4: mcp_server.py — the create_category_rule tool + undo wiring

**Files:**
- Modify: `financial_mcp/mcp_server.py` (add the tool after `create_merchant_rule` ~line 282; add branches to `undo_last` and update `undo_last`/`undo_history` docstrings)
- Test: `tests/test_mcp_tools.py` (extend the `RuleAdapter` fake; add tool tests)

**Interfaces:**
- Consumes: `writes.resolve_category`, `writes.compute_category_rule_token`, `writes.match_transactions`, `svc.categories()`, `svc.create_category_rule`, `svc.delete_transaction_rule`, `svc.transaction_rules()`, `journal.record`/`mark_succeeded`.
- Produces: `create_category_rule(contains, set_category, confirm_token=None)` MCP tool; `undo_last` reverses `create_category_rule` batches by deleting the rule.

- [ ] **Step 1: Write the failing tests**

First — REQUIRED so the full suite can instantiate its test doubles — add a default `create_category_rule` to the BASE `FakeAdapter` in `tests/test_mcp_tools.py` (directly after its `create_transaction_rule` stub, ~line 94), mirroring that stub. Task 2 added `create_category_rule` as an ABC abstractmethod, which makes EVERY `FakeAdapter` subclass abstract-incomplete without this default:

```python
    def create_category_rule(self, contains, category_id):
        return (False, None)
```

Then extend the `RuleAdapter` fake (the class near line 1833) so category rules round-trip. Add this method to `RuleAdapter` (after `create_transaction_rule`) — it OVERRIDES the base default:

```python
    def create_category_rule(self, contains, category_id):
        rule_id = f"rule{self._next_id}"
        self._next_id += 1
        # Store category_id in set_category; the tool's dup-guard compares against
        # the resolved category NAME, so dup-guard tests pre-seed a name explicitly.
        self.rules.append({"id": rule_id, "operator": "contains", "value": contains,
                            "set_merchant": None, "set_category": category_id})
        return (True, rule_id)
```

Also add matching overrides so the declining / uncapturable fakes cover the category path too:

```python
# in DecliningRuleAdapter:
    def create_category_rule(self, contains, category_id):
        return (False, None)

# in UncapturableIdRuleAdapter:
    def create_category_rule(self, contains, category_id):
        return (True, None)
```

Then append the tool tests. `RuleAdapter` inherits `BulkRecatAdapter.get_categories`, which exposes `Shopping` (id `shop`) and two Amazon transactions:

```python
def test_create_category_rule_off_mode_refuses(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="off")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert "error" in out
    assert svc._adapter.rules == []


def test_create_category_rule_blank_and_unknown_category_refuse(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="safe")
    tool = _tool(server, "create_category_rule")
    assert "error" in tool("  ", "Shopping")
    assert "error" in tool("Amazon", " ")
    # Unknown category -> fail-closed refusal, nothing created.
    out = tool("Amazon", "NoSuchCategory")
    assert "error" in out
    assert svc._adapter.rules == []


def test_create_category_rule_dry_run_then_confirm(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="safe")
    tool = _tool(server, "create_category_rule")

    preview = tool("Amazon", "Shopping")
    assert preview["dry_run"] is True
    assert preview["rule"] == {"contains": "Amazon", "set_category": "Shopping",
                               "apply_to_existing": False}
    assert preview["existing_matches"] == 2   # two Amazon txns matched, NOT changed
    assert "future" in preview["message"].lower()
    assert "confirm_token" in preview
    assert svc._adapter.rules == []            # nothing created on preview

    out = tool("Amazon", "Shopping", confirm_token=preview["confirm_token"])
    assert out["created"] is True
    assert out["rule_id"]
    assert len(svc._adapter.rules) == 1
    assert journal.last_undoable_batch() is not None


def test_create_category_rule_full_mode_executes_without_token(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="full")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert out["created"] is True
    assert out["rule_id"]
    assert len(svc._adapter.rules) == 1


def test_create_category_rule_duplicate_guard_refuses(tmp_path):
    adapter = RuleAdapter()
    adapter.rules.append({"id": "rule0", "operator": "contains", "value": "Amazon",
                          "set_merchant": None, "set_category": "Shopping"})
    svc, journal, server = _rule_server(tmp_path, adapter=adapter, write_mode="safe")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert out["existing_rule_id"] == "rule0"
    assert len(svc._adapter.rules) == 1


def test_create_category_rule_declined_reports_created_false(tmp_path):
    svc, journal, server = _rule_server(tmp_path, adapter=DecliningRuleAdapter(),
                                        write_mode="full")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert out["created"] is False
    assert "reason" in out
    assert journal.last_undoable_batch() is None


def test_create_category_rule_uncapturable_id_briefs_user(tmp_path):
    svc, journal, server = _rule_server(tmp_path, adapter=UncapturableIdRuleAdapter(),
                                        write_mode="full")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert out["created"] is True
    assert out["rule_id"] is None
    assert out["undo_available"] is False
    assert out["note"]
    assert journal.last_undoable_batch() is None


def test_undo_last_reverses_create_category_rule(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="full")
    create = _tool(server, "create_category_rule")
    undo = _tool(server, "undo_last")

    out = create("Amazon", "Shopping")
    rule_id = out["rule_id"]
    assert rule_id in [r["id"] for r in svc._adapter.rules]

    result = undo()
    assert result["reversed"] >= 1
    assert rule_id not in [r["id"] for r in svc._adapter.rules]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_tools.py -k "category_rule" -v`
Expected: FAIL — the `create_category_rule` tool is not registered (`_tool` raises / returns None).

- [ ] **Step 3a: Add the tool**

In `financial_mcp/mcp_server.py`, immediately after the `create_merchant_rule` tool's `return _guard(_run)` (around line 282), add:

```python
    @server.tool()
    def create_category_rule(
        contains: str, set_category: str, confirm_token: str | None = None
    ) -> dict:
        """Create a Monarch rule that auto-CATEGORIZES future transactions.

        Whenever a newly-imported transaction's merchant name CONTAINS `contains`,
        Monarch will set its category to `set_category` (an EXISTING category name).
        Future-only: it does NOT recategorize transactions you already have (run
        recategorize_matching first for those — the dry-run tells you how many
        exist). In 'safe' mode this is two-phase: call without confirm_token to
        preview (nothing is created) and get a confirm_token, then call again with
        it to create. In 'full' mode it creates directly. Reversible via undo_last
        (which deletes the rule).
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}
        if not contains or not contains.strip():
            return {"error": "contains must be a non-empty phrase"}
        if not set_category or not set_category.strip():
            return {"error": "set_category must be a non-empty category name"}

        def _run():
            cats = svc.categories()
            try:
                target = writes.resolve_category(set_category, cats)
            except writes.WriteError as exc:
                return {"error": str(exc)}

            # Duplicate guard (best-effort; fails open — same as create_merchant_rule).
            for rule in svc.transaction_rules():
                if ((rule.get("value") or "").strip().lower() == contains.strip().lower()
                        and (rule.get("set_category") or "") == target.name):
                    return {"refused": "a rule for this already exists",
                            "existing_rule_id": rule.get("id")}

            existing = len(writes.match_transactions(svc.transactions(), merchant=contains))
            token = writes.compute_category_rule_token(contains, target.id)

            if write_mode == "safe" and (confirm_token is None or confirm_token != token):
                return {
                    "dry_run": True,
                    "rule": {"contains": contains, "set_category": target.name,
                             "apply_to_existing": False},
                    "existing_matches": existing,
                    "message": (
                        f"This rule will categorize FUTURE transactions whose merchant "
                        f"contains '{contains}' as '{target.name}'. I'm showing you the "
                        f"{existing} existing transactions it matches so you know they "
                        f"will NOT be changed — a new rule only applies to transactions "
                        f"imported from now on. If you also want those {existing} "
                        f"recategorized now, run recategorize_matching first. Confirm to "
                        f"create the rule."),
                    "confirm_token": token,
                    "mode": write_mode,
                    "note": "no rule created yet",
                }

            created, rule_id = svc.create_category_rule(contains, target.id)
            if not created:
                return {"created": False,
                        "reason": "Monarch declined to create the rule"}
            if rule_id is None:
                return {"created": True, "rule_id": None, "undo_available": False,
                        "contains": contains, "set_category": target.name,
                        "applied_to_existing": False,
                        "note": ("Rule created, but I couldn't capture its id to enable "
                                 "one-click undo. If you change your mind, delete it in "
                                 "Monarch → Settings → Rules.")}
            batch_id = uuid.uuid4().hex
            journal.record(batch_id, "create_category_rule", rule_id, contains,
                           target.name, contains, target.name)
            journal.mark_succeeded(batch_id, rule_id)
            return {"created": True, "rule_id": rule_id, "contains": contains,
                    "set_category": target.name, "applied_to_existing": False,
                    "existing_matches": existing,
                    "undo_hint": "call undo_last to delete this rule"}

        return _guard(_run)
```

- [ ] **Step 3b: Wire undo_last to reverse the new op**

In `undo_last`'s reversal loop (around lines 902-914), add a branch for the new op alongside the existing `create_rule` branch (delete is the same reversal):

```python
                elif r["op_type"] == "create_category_rule":
                    ok = svc.delete_transaction_rule(r["target_id"])
```

And in the failure-reason chain (around lines 919-933), add:

```python
                    elif r["op_type"] == "create_category_rule":
                        failures.append({"rule_id": r["target_id"],
                                         "reason": "Monarch did not delete the rule"})
```

- [ ] **Step 3c: Update the docstrings (minor, for honesty)**

In `undo_last`'s docstring, extend the list of reversible ops to mention "created auto-categorize rule". In `undo_history`'s docstring, do the same. These are one-line edits — keep them accurate but brief.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_tools.py -k "category_rule" -v`
Expected: PASS (all 8 new tool tests).

- [ ] **Step 5: Run the FULL suite**

Run: `python -m pytest -q`
Expected: PASS — all pre-existing tests plus the new ones. Report the total count.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: create_category_rule MCP tool + undo_last wiring"
```

---

## Self-Review Notes

- **Spec coverage:** the design (new tool, fail-closed name resolution, future-only, dry-run/confirm, journal+undo-by-delete, DRY create helper) is covered by Tasks 1-4.
- **Type consistency:** `create_category_rule(contains, category_id) -> tuple[bool, str | None]` is identical across ABC, both adapters, and `DataService`. The tool resolves `set_category` (name) → `target.id` before calling the service. `resolve_category` returns a `Category` with `.id` and `.name`.
- **Dup-guard note:** the tool compares an existing rule's `set_category` (a category NAME on the read side) against `target.name` — both names, so the comparison is sound. The in-memory fake stores the id in `set_category`; dup-guard tests therefore pre-seed a rule with the NAME explicitly, mirroring how the merchant dup-guard test pre-seeds `set_merchant`.
- **No migration:** Task 3 only appends to `REVERSIBLE_OPS` and adds one history branch. The `write_journal` table shape is untouched.
