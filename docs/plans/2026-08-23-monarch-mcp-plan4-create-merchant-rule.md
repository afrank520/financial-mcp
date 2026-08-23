# Plan 4 Create-Merchant-Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one gated, reversible MCP tool `create_merchant_rule` that creates a Monarch transaction rule renaming the merchant on FUTURE matching transactions, following the proven write-gate/dry-run/journal/undo template.

**Architecture:** Two new GraphQL mutation constants (`createTransactionRuleV2`, `deleteTransactionRule`, both captured live 2026-08-23) plus a pure input-builder feed a new adapter pair `create_transaction_rule`/`delete_transaction_rule`. Because the create mutation returns no rule id, the adapter learns the new id by diffing the rule-id set before/after and returns a `(created, rule_id)` tuple so the tool can report honestly. A thin DataService passthrough, a new op-log op type `create_rule` (no schema migration), and the `create_merchant_rule` tool (gate → dry-run→confirm → journal → `undo_last` deletes the rule) complete it. FUTURE-ONLY: the rule is always created with `applyToExistingTransactions=false`; "fix past + prevent future" is achieved by composing `rename_merchant_matching` (shipped) then this tool.

**Tech Stack:** Python 3.14, FastMCP, pydantic, sqlite3 (op-log), pytest. Live GraphQL over cookie auth (`CommunityAdapter`).

**Spec:** `docs/specs/2026-08-23-monarch-mcp-plan4-create-merchant-rule-design.md`

## Global Constraints

- **FUTURE-ONLY (never negotiable):** every rule is created with `applyToExistingTransactions: false`. The flag is never exposed. Past-transaction fixing is `rename_merchant_matching`'s job (composition), keeping undo clean.
- **Fail-closed on safety:** the write refuses cleanly (never a bare stack trace) when `write_mode == "off"`, when `contains` or `rename_to` is blank/whitespace, and on token mismatch (re-preview, never execute).
- **Honest reporting of the three create outcomes** (adapter returns `(created, rule_id)`): `(False, None)` → `{"created": false, ...}`; `(True, id)` → normal success + `undo_hint`, journaled; `(True, None)` → `{"created": true, "undo_available": false, "note": ...}`, NOT journaled. The `undo_available`/`note` fields appear ONLY on the `(True, None)` path — the normal success stays uncluttered.
- **Duplicate guard fails OPEN:** it may miss a rule created via `merchantNameCriteria` if the read surfaces only `merchantCriteria`; that is acceptable because a duplicate rule is non-destructive and reversible. The guard is a courtesy, never a safety invariant.
- **No cache-schema migration:** reuse the op-log; add `"create_rule"` to `REVERSIBLE_OPS`. `old_value`/`new_value` hold `contains`/`rename_to`; `target_id` holds the rule id.
- **Reversibility:** undo of a create is a delete of the journaled rule id.
- **Match operator:** `contains` only (matches the shipped rename tool's substring semantics).
- **Adapter parity:** both new writes raise `RuntimeError("writes are not available in CSV mode")` in CSV mode.
- **Backward compatibility / regression fence:** the two new abstract adapter methods require an R1-style sweep — every directly-instantiated fake adapter in the suite gets minimal implementations so the current baseline (314 tests on `main` at slice start) never goes red on instantiation.
- **Security:** the create/delete mutation shapes were captured live from DevTools request Payloads only (never "Copy as cURL"); no credentials are embedded anywhere in the code or tests.

---

### Task 1: GraphQL rule mutations + pure write helpers

**Files:**
- Modify: `financial_mcp/monarch/queries.py` (add `CREATE_TRANSACTION_RULE`, `DELETE_TRANSACTION_RULE`)
- Modify: `financial_mcp/writes.py` (add `_rename_rule_input`, `compute_rule_token`)
- Test: `tests/test_writes.py`

**Interfaces:**
- Produces: `queries.CREATE_TRANSACTION_RULE`, `queries.DELETE_TRANSACTION_RULE`; `writes._rename_rule_input(contains: str, rename_to: str) -> dict`; `writes.compute_rule_token(contains: str, rename_to: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_writes.py`:

```python
def test_rename_rule_input_shape():
    from financial_mcp.writes import _rename_rule_input
    inp = _rename_rule_input("AMZN", "Amazon")
    assert inp["merchantNameCriteria"] == [{"operator": "contains", "value": "AMZN"}]
    assert inp["setMerchantAction"] == "Amazon"
    assert inp["applyToExistingTransactions"] is False
    # every other field is present and null (mirrors the captured payload)
    for k in ("accountIds", "categoryIds", "amountCriteria", "merchantCriteria",
              "originalStatementCriteria", "addTagsAction", "linkGoalAction",
              "linkSavingsGoalAction", "reviewStatusAction", "setCategoryAction",
              "splitTransactionsAction"):
        assert inp[k] is None
    assert inp["merchantCriteriaUseOriginalStatement"] is False

def test_compute_rule_token_stable_and_sensitive():
    from financial_mcp.writes import compute_rule_token
    a = compute_rule_token("AMZN", "Amazon")
    assert a == compute_rule_token("AMZN", "Amazon") and len(a) == 16
    assert compute_rule_token("AMZN", "Amazon") != compute_rule_token("AMZN", "Amazon Inc")
    assert compute_rule_token("AMZN", "Amazon") != compute_rule_token("AMZ", "Amazon")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_writes.py -k "rename_rule_input or rule_token" -v`
Expected: FAIL (functions not defined).

- [ ] **Step 3: Add the mutation constants**

Append to `queries.py` (beside the existing `GET_TRANSACTION_RULES`):

```python
CREATE_TRANSACTION_RULE = """
  mutation Common_CreateTransactionRuleMutationV2($input: CreateTransactionRuleInput!) {
    createTransactionRuleV2(input: $input) {
      errors { message code __typename }
      __typename
    }
  }
"""

DELETE_TRANSACTION_RULE = """
  mutation Common_DeleteTransactionRule($id: ID!) {
    deleteTransactionRule(id: $id) {
      deleted
      errors { message code __typename }
      __typename
    }
  }
"""
```

- [ ] **Step 4: Add the pure helpers**

Add to `writes.py` (`hashlib` is already imported for `compute_merchant_token`):

```python
def _rename_rule_input(contains: str, rename_to: str) -> dict:
    """Build the CreateTransactionRuleInput for a FUTURE-ONLY merchant-rename rule.

    Mirrors the exact payload captured from the Monarch web app 2026-08-23: only
    the match criterion, the rename action, and the (always-false) apply flag are
    populated; every other field is sent explicit-null to match the app and avoid
    a "non-null field missing" 400.
    """
    return {
        "accountIds": None,
        "categoryIds": None,
        "amountCriteria": None,
        "merchantCriteria": None,
        "originalStatementCriteria": None,
        "merchantCriteriaUseOriginalStatement": False,
        "merchantNameCriteria": [{"operator": "contains", "value": contains}],
        "setMerchantAction": rename_to,
        "applyToExistingTransactions": False,
        "addTagsAction": None,
        "linkGoalAction": None,
        "linkSavingsGoalAction": None,
        "reviewStatusAction": None,
        "setCategoryAction": None,
        "splitTransactionsAction": None,
    }


def compute_rule_token(contains: str, rename_to: str) -> str:
    key = contains + "->" + rename_to
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_writes.py -k "rename_rule_input or rule_token" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/monarch/queries.py financial_mcp/writes.py tests/test_writes.py
git commit -m "feat: create/delete transaction-rule mutations + rename-rule input builder + rule token"
```

---

### Task 2: Adapter create/delete rule (tuple contract + CSV parity + fake sweep)

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (ABC)
- Modify: `financial_mcp/monarch/community_adapter.py`
- Modify: `financial_mcp/monarch/csv_adapter.py`
- Modify: `tests/test_mcp_tools.py`, `tests/test_data_service.py` (R1 fake sweep — add minimal impls to every directly-instantiated fake adapter so the baseline stays green)
- Test: `tests/test_merchant_rule_adapter.py` (new file, matching the per-feature convention of `test_merchant_adapter.py`)

**Interfaces:**
- Consumes: `queries.CREATE_TRANSACTION_RULE`, `queries.DELETE_TRANSACTION_RULE` (Task 1); `writes._rename_rule_input` (Task 1); the existing `get_transaction_rules()` (shipped).
- Produces: `MonarchAdapter.create_transaction_rule(contains: str, rename_to: str) -> tuple[bool, str | None]` and `MonarchAdapter.delete_transaction_rule(rule_id: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_merchant_rule_adapter.py` (mirror the faked-`transport` pattern used in `tests/test_merchant_adapter.py`; `get_transaction_rules` runs its own transport call, so drive the sequence of responses):

```python
from financial_mcp.monarch.community_adapter import CommunityAdapter


def _seq_adapter(responses):
    """Return an adapter whose transport yields queued responses in order."""
    q = list(responses)
    async def fake_transport(op, query, variables):
        return q.pop(0)
    return CommunityAdapter({"session_id": "s", "csrftoken": "c"}, transport=fake_transport)


def test_create_rule_returns_true_and_new_id():
    ad = _seq_adapter([
        {"transactionRules": [{"id": "r1", "merchantCriteria": [], "setMerchantAction": None, "setCategoryAction": None}]},   # before
        {"createTransactionRuleV2": {"errors": []}},                                                                          # create
        {"transactionRules": [                                                                                                # after
            {"id": "r1", "merchantCriteria": [], "setMerchantAction": None, "setCategoryAction": None},
            {"id": "r2", "merchantCriteria": [], "setMerchantAction": None, "setCategoryAction": None}]},
    ])
    assert ad.create_transaction_rule("AMZN", "Amazon") == (True, "r2")


def test_create_rule_returns_false_on_monarch_error():
    ad = _seq_adapter([
        {"transactionRules": []},
        {"createTransactionRuleV2": {"errors": [{"message": "bad", "code": "X"}]}},
    ])
    assert ad.create_transaction_rule("AMZN", "Amazon") == (False, None)


def test_create_rule_returns_true_none_on_ambiguous_diff():
    # created (errors empty) but zero or many new ids → id un-capturable
    ad = _seq_adapter([
        {"transactionRules": [{"id": "r1", "merchantCriteria": [], "setMerchantAction": None, "setCategoryAction": None}]},
        {"createTransactionRuleV2": {"errors": []}},
        {"transactionRules": [{"id": "r1", "merchantCriteria": [], "setMerchantAction": None, "setCategoryAction": None}]},  # nothing new appeared
    ])
    assert ad.create_transaction_rule("AMZN", "Amazon") == (True, None)


def test_delete_rule_true_and_false():
    assert _seq_adapter([{"deleteTransactionRule": {"deleted": True, "errors": []}}]).delete_transaction_rule("r2") is True
    assert _seq_adapter([{"deleteTransactionRule": {"deleted": False, "errors": []}}]).delete_transaction_rule("r2") is False
    assert _seq_adapter([{"deleteTransactionRule": {"deleted": True, "errors": [{"message": "e"}]}}]).delete_transaction_rule("r2") is False


def test_csv_create_and_delete_rule_raise(tmp_path):
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    import pytest
    ad = CsvAdapter(str(tmp_path))  # match the existing CsvAdapter constructor in the suite
    with pytest.raises(RuntimeError):
        ad.create_transaction_rule("AMZN", "Amazon")
    with pytest.raises(RuntimeError):
        ad.delete_transaction_rule("r2")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_merchant_rule_adapter.py -v`
Expected: FAIL (`AttributeError` / abstractmethod not implemented).

- [ ] **Step 3: Add abstract methods to the ABC**

In `adapter.py`, after `get_transaction_rules`:

```python
    @abc.abstractmethod
    def create_transaction_rule(self, contains: str, rename_to: str) -> tuple[bool, str | None]: ...

    @abc.abstractmethod
    def delete_transaction_rule(self, rule_id: str) -> bool: ...
```

- [ ] **Step 4: Implement in `community_adapter.py`**

Import `CREATE_TRANSACTION_RULE`, `DELETE_TRANSACTION_RULE` in the queries import block and `from financial_mcp.writes import _rename_rule_input` (module-level; `writes` has no adapter import, so no cycle). Add after `get_transaction_rules`:

```python
    def create_transaction_rule(self, contains: str, rename_to: str) -> tuple[bool, str | None]:
        before = {r["id"] for r in self.get_transaction_rules()}
        payload = self._run(self._transport(
            "Common_CreateTransactionRuleMutationV2", CREATE_TRANSACTION_RULE,
            {"input": _rename_rule_input(contains, rename_to)}))
        errors = ((payload.get("createTransactionRuleV2") or {}).get("errors")) or []
        if errors:
            return (False, None)
        after = self.get_transaction_rules()
        new = [r["id"] for r in after if r["id"] not in before]
        return (True, new[0]) if len(new) == 1 else (True, None)

    def delete_transaction_rule(self, rule_id: str) -> bool:
        data = self._run(self._transport(
            "Common_DeleteTransactionRule", DELETE_TRANSACTION_RULE, {"id": rule_id}))
        res = data.get("deleteTransactionRule") or {}
        return bool(res.get("deleted")) and not (res.get("errors") or [])
```

- [ ] **Step 5: Implement CSV stubs**

In `csv_adapter.py`, after `get_transaction_rules`:

```python
    def create_transaction_rule(self, contains: str, rename_to: str) -> tuple[bool, str | None]:
        raise RuntimeError("writes are not available in CSV mode")

    def delete_transaction_rule(self, rule_id: str) -> bool:
        raise RuntimeError("writes are not available in CSV mode")
```

- [ ] **Step 6: R1 fake sweep (keep the baseline green)**

The two new abstractmethods break instantiation of every concrete fake. Add minimal implementations to each directly-instantiated fake adapter in the suite (the same set swept for the merchant-cleanup slice — the SDD pre-flight scan enumerates the current exact set; at minimum the fakes in `tests/test_mcp_tools.py` and `tests/test_data_service.py`):

```python
    def create_transaction_rule(self, contains, rename_to):
        return (False, None)

    def delete_transaction_rule(self, rule_id):
        return False
```

(Tasks 3 and 5 use their own local fake adapters with an in-memory rules list where working behavior is needed — leave these shared fakes minimal.)

- [ ] **Step 7: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_merchant_rule_adapter.py -v` → PASS
Run: `.venv/bin/pytest` → all green (no regressions from the abstractmethod additions).

- [ ] **Step 8: Commit**

```bash
git add financial_mcp/monarch/adapter.py financial_mcp/monarch/community_adapter.py financial_mcp/monarch/csv_adapter.py tests/test_merchant_rule_adapter.py tests/test_mcp_tools.py tests/test_data_service.py
git commit -m "feat: adapter create_transaction_rule/(delete) with (created, id) tuple + CSV parity + fake sweep"
```

---

### Task 3: DataService rule create/delete passthrough

**Files:**
- Modify: `financial_mcp/data_service.py`
- Test: `tests/test_data_service.py`

**Interfaces:**
- Consumes: adapter `create_transaction_rule`, `delete_transaction_rule` (Task 2).
- Produces: `DataService.create_merchant_rule(contains: str, rename_to: str) -> tuple[bool, str | None]`; `DataService.delete_transaction_rule(rule_id: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_data_service.py` a small local fake adapter with an in-memory rules list (do not touch the shared minimal fakes from Task 2):

```python
def _rule_svc():
    # Build a DataService over a local fake adapter whose rules list is mutable.
    # Follow the existing DataService construction pattern in this file (adapter + cache).
    class _RuleAdapter(FakeAdapter):  # FakeAdapter already defined in this module
        def __init__(self):
            super().__init__()
            self._rules = []
        def get_transaction_rules(self):
            return list(self._rules)
        def create_transaction_rule(self, contains, rename_to):
            rid = f"r{len(self._rules) + 1}"
            self._rules.append({"id": rid, "operator": "contains", "value": contains,
                                "set_merchant": rename_to, "set_category": None})
            return (True, rid)
        def delete_transaction_rule(self, rule_id):
            n = len(self._rules)
            self._rules = [r for r in self._rules if r["id"] != rule_id]
            return len(self._rules) < n
    return _build_service(_RuleAdapter())  # reuse this file's service builder helper

def test_create_merchant_rule_passthrough():
    svc = _rule_svc()
    assert svc.create_merchant_rule("AMZN", "Amazon") == (True, "r1")

def test_delete_transaction_rule_passthrough():
    svc = _rule_svc()
    svc.create_merchant_rule("AMZN", "Amazon")
    assert svc.delete_transaction_rule("r1") is True
    assert svc.delete_transaction_rule("nope") is False
```

(If this file has no `_build_service` helper, construct the `DataService` the same way the existing `recategorize`/`transaction_rules` service tests do — reuse, don't reinvent.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_data_service.py -k "create_merchant_rule or delete_transaction_rule" -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement the methods**

In `data_service.py`, after `transaction_rules`:

```python
    def create_merchant_rule(self, contains: str, rename_to: str) -> tuple[bool, str | None]:
        return self._adapter.create_transaction_rule(contains, rename_to)

    def delete_transaction_rule(self, rule_id: str) -> bool:
        return self._adapter.delete_transaction_rule(rule_id)
```

(`transaction_rules()` is an uncached passthrough today, so there is no rules cache to invalidate; match that current behavior.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_data_service.py -k "create_merchant_rule or delete_transaction_rule" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/data_service.py tests/test_data_service.py
git commit -m "feat: DataService.create_merchant_rule + delete_transaction_rule passthrough"
```

---

### Task 4: Op-log recognizes the create_rule op

**Files:**
- Modify: `financial_mcp/cache/undo_journal.py`
- Test: `tests/test_undo_journal.py`

**Interfaces:**
- Produces: `"create_rule"` recognized by `REVERSIBLE_OPS`, `last_undoable_batch`, `undoable_rows`; `history()` renders a rule summary line.

- [ ] **Step 1: Write the failing tests**

```python
def test_create_rule_is_reversible(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "c.db"))
    j.record("b1", "create_rule", "r2", "AMZN", "Amazon", "AMZN", "Amazon")
    j.mark_succeeded("b1", "r2")
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows[0]["op_type"] == "create_rule"
    assert rows[0]["target_id"] == "r2"
    assert rows[0]["old_value"] == "AMZN"

def test_history_renders_rule_summary(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "c.db"))
    j.record("b1", "create_rule", "r2", "AMZN", "Amazon", "AMZN", "Amazon")
    j.mark_succeeded("b1", "r2")
    h = j.history()
    assert "AMZN" in h[0]["summary"] and "Amazon" in h[0]["summary"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_undo_journal.py -k "create_rule or rule_summary" -v`
Expected: FAIL (op not reversible; summary has no create_rule branch).

- [ ] **Step 3: Add the op to REVERSIBLE_OPS**

In `undo_journal.py`:

```python
REVERSIBLE_OPS = ("recategorize", "rename_category", "delete_category", "rename_merchant", "create_rule")
```

- [ ] **Step 4: Add the history summary branch**

In `history()`, extend the summary `if/elif` chain (before the generic `else`; `old_n`/`new_n`/`n` are the existing most-common `(op_type, old, new)` vars):

```python
            elif op_type == "create_rule":
                summary = f"Rule: contains '{old_n}' → '{new_n}' ×{n}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_undo_journal.py -k "create_rule or rule_summary" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/cache/undo_journal.py tests/test_undo_journal.py
git commit -m "feat: op-log recognizes create_rule (reversible + history)"
```

---

### Task 5: `create_merchant_rule` tool + undo_last branch

**Files:**
- Modify: `financial_mcp/mcp_server.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `svc.create_merchant_rule`, `svc.delete_transaction_rule`, `svc.transaction_rules`, `svc.transactions`, `writes.match_transactions`, `writes.compute_rule_token`, `journal`, `write_mode`, `_guard`, `_writes_enabled`, `uuid`.
- Produces: MCP tool `create_merchant_rule(contains: str, rename_to: str, confirm_token: str | None = None) -> dict`; `undo_last` reverses `create_rule` rows.

- [ ] **Step 1: Write the failing tests** (mirror the existing `rename_merchant_matching` tool tests; the SDD implementer translates the fixture names below to the module's real harness — `_bulk_server(tmp_path, write_mode=...)` + `_tool(server, "create_merchant_rule")` — and uses a fake `svc` with an in-memory rules list so create/delete/rules round-trip):

```python
def test_create_merchant_rule_off_mode_refuses(server_off):
    assert "error" in server_off.create_merchant_rule("AMZN", "Amazon")

def test_create_merchant_rule_blank_refuses(server_safe):
    assert "error" in server_safe.create_merchant_rule("  ", "Amazon")
    assert "error" in server_safe.create_merchant_rule("AMZN", " ")

def test_create_merchant_rule_dry_run_then_confirm(server_safe):
    pv = server_safe.create_merchant_rule("AMZN", "Amazon")
    assert pv["dry_run"] is True
    assert pv["rule"] == {"contains": "AMZN", "rename_to": "Amazon", "apply_to_existing": False}
    assert "existing_matches" in pv and pv["message"] and "confirm_token" in pv
    out = server_safe.create_merchant_rule("AMZN", "Amazon", confirm_token=pv["confirm_token"])
    assert out["created"] is True and out["rule_id"] and "undo_available" not in out

def test_create_merchant_rule_full_mode_executes_without_token(server_full):
    out = server_full.create_merchant_rule("AMZN", "Amazon")
    assert out["created"] is True and out["rule_id"]

def test_create_merchant_rule_duplicate_guard_refuses(server_safe_with_existing_rule):
    # svc.transaction_rules() already returns a rule value="AMZN", set_merchant="Amazon"
    out = server_safe_with_existing_rule.create_merchant_rule("AMZN", "Amazon")
    assert "existing_rule_id" in out

def test_create_merchant_rule_declined_reports_created_false(server_safe_declines):
    # fake svc.create_merchant_rule returns (False, None)
    out = server_safe_declines.create_merchant_rule("AMZN", "Amazon",
                                                    confirm_token="ignored")  # full mode or valid token path
    assert out["created"] is False and "reason" in out

def test_create_merchant_rule_uncapturable_id_briefs_user(server_full_uncapturable):
    # fake svc.create_merchant_rule returns (True, None)
    out = server_full_uncapturable.create_merchant_rule("AMZN", "Amazon")
    assert out["created"] is True and out["undo_available"] is False and out["note"]

def test_undo_last_reverses_create_rule(server_full):
    server_full.create_merchant_rule("AMZN", "Amazon")
    undo = server_full.undo_last()
    assert undo["reversed"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k "create_merchant_rule or reverses_create_rule" -v`
Expected: FAIL (tool not defined).

- [ ] **Step 3: Implement `create_merchant_rule`**

In `mcp_server.py`, after `merchant_cleanup_suggestions`, add:

```python
    @server.tool()
    def create_merchant_rule(
        contains: str, rename_to: str, confirm_token: str | None = None
    ) -> dict:
        """Create a Monarch rule that renames the merchant on FUTURE transactions.

        Whenever a newly-imported transaction's merchant name CONTAINS `contains`,
        Monarch will rename its merchant to `rename_to`. This is future-only: it
        does NOT touch transactions you already have (run rename_merchant_matching
        first for those — the dry-run tells you how many exist). In 'safe' mode
        this is two-phase: call without confirm_token to preview (nothing is
        created) and get a confirm_token, then call again with it to create. In
        'full' mode it creates directly. Reversible via undo_last (which deletes
        the rule).
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}
        if not contains or not contains.strip():
            return {"error": "contains must be a non-empty phrase"}
        if not rename_to or not rename_to.strip():
            return {"error": "rename_to must be a non-empty name"}

        def _run():
            # Duplicate guard (best-effort; fails open — see spec §6).
            for rule in svc.transaction_rules():
                if ((rule.get("value") or "").strip().lower() == contains.strip().lower()
                        and (rule.get("set_merchant") or "") == rename_to):
                    return {"refused": "a rule for this already exists",
                            "existing_rule_id": rule.get("id")}

            existing = len(writes.match_transactions(svc.transactions(), merchant=contains))
            token = writes.compute_rule_token(contains, rename_to)

            if write_mode == "safe" and (confirm_token is None or confirm_token != token):
                return {
                    "dry_run": True,
                    "rule": {"contains": contains, "rename_to": rename_to,
                             "apply_to_existing": False},
                    "existing_matches": existing,
                    "message": (
                        f"This rule will rename FUTURE transactions whose merchant "
                        f"contains '{contains}' to '{rename_to}'. I'm showing you the "
                        f"{existing} existing transactions it matches so you know they "
                        f"will NOT be changed — a new rule only applies to transactions "
                        f"imported from now on. If you also want those {existing} cleaned "
                        f"up now, run rename_merchant_matching first. Confirm to create "
                        f"the rule."),
                    "confirm_token": token,
                    "mode": write_mode,
                    "note": "no rule created yet",
                }

            created, rule_id = svc.create_merchant_rule(contains, rename_to)
            if not created:
                return {"created": False,
                        "reason": "Monarch declined to create the rule"}
            if rule_id is None:
                return {"created": True, "rule_id": None, "undo_available": False,
                        "contains": contains, "rename_to": rename_to,
                        "applied_to_existing": False,
                        "note": ("Rule created, but I couldn't capture its id to enable "
                                 "one-click undo. If you change your mind, delete it in "
                                 "Monarch → Settings → Rules.")}
            batch_id = uuid.uuid4().hex
            journal.record(batch_id, "create_rule", rule_id, contains, rename_to,
                           contains, rename_to)
            journal.mark_succeeded(batch_id, rule_id)
            return {"created": True, "rule_id": rule_id, "contains": contains,
                    "rename_to": rename_to, "applied_to_existing": False,
                    "existing_matches": existing,
                    "undo_hint": "call undo_last to delete this rule"}

        return _guard(_run)
```

- [ ] **Step 4: Extend `undo_last` to reverse create_rule**

In `undo_last`'s `_run`, add to the dispatch `if/elif` (after the `rename_merchant` branch, before the `else`):

```python
                elif r["op_type"] == "create_rule":
                    ok = svc.delete_transaction_rule(r["target_id"])
```

and add a matching failure branch in the failure `if/elif` chain (before its `else`):

```python
                    elif r["op_type"] == "create_rule":
                        failures.append({"transaction_id": r["target_id"],
                                         "reason": "Monarch did not delete the rule"})
```

Also update the `undo_last` docstring first line to mention created rules (it currently lists recategorization, category-rename, category-delete, and merchant-rename batches).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k "create_merchant_rule or reverses_create_rule" -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest`
Expected: all green (314 prior + the new tests).

- [ ] **Step 7: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: create_merchant_rule tool (future-only, gated, honest-degradation) + undo_last branch"
```

---

## Self-Review

- **Spec coverage:** §4.1 mutations + `compute_rule_token` → Task 1; §4.2 adapter tuple contract + CSV parity → Task 2; §4.3 DataService → Task 3; §4.4 `_rename_rule_input` → Task 1 (co-located with the mutation it feeds); §4.5 op-log → Task 4; §4.6 tool (gate, validate, dup guard, match count, dry-run→confirm, three-case execute) + undo_last → Task 5; §5 undo semantics → Tasks 4 & 5; §6 refusals (fail-closed safety / fail-open dup guard) → Task 5; §7 testing distributed across every task; §9 live-proving is post-merge (not a plan task).
- **Type consistency:** `create_transaction_rule` / `svc.create_merchant_rule` return `tuple[bool, str | None]` everywhere (Tasks 2, 3, 5); `delete_transaction_rule` returns `bool` (Tasks 2, 3, 5, and the `ok = svc.delete_transaction_rule(...)` in undo_last); `compute_rule_token` / `_rename_rule_input` signatures match between producer (Task 1) and consumers (Tasks 2, 5).
- **Placeholder scan:** Task 3 and Task 5 test bodies reference the existing service-builder / `_bulk_server`+`_tool` harness rather than re-inlining it — deliberate (per the merchant-cleanup slice's R2 ruling, that harness exists and must be reused); all production code is fully specified. Fixture names in Task 5 (`server_safe`, `server_full`, `server_off`, `server_safe_with_existing_rule`, `server_safe_declines`, `server_full_uncapturable`) are illustrative — the implementer wires each to `_bulk_server` with the stated fake-`svc` behavior.
- **FUTURE-ONLY invariant:** `applyToExistingTransactions: False` is hard-coded in `_rename_rule_input` (Task 1) and the flag never reaches the tool surface (Task 5) — no path can set it true.
- **Honest-degradation invariant:** the `(True, None)` briefing fields (`undo_available`, `note`) are emitted only on that branch (Task 5); the normal `(True, id)` success carries neither. Asserted by `test_create_merchant_rule_dry_run_then_confirm` (`"undo_available" not in out`) and `test_create_merchant_rule_uncapturable_id_briefs_user`.

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
**2. Inline Execution** — execute in this session with checkpoints.

Which approach?
