# Rule-Update Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gated `update_transaction_rule` MCP tool that edits an existing Monarch transaction rule in place — match text, category target, or merchant-rename target — fail-closed to the "simple" rules it can faithfully round-trip, reversible via `undo_last`.

**Architecture:** Follows the exact write-slice template already shipped for `create_merchant_rule` / `create_category_rule`: pure input-builder + token helpers in `writes.py`, a mutation constant in `queries.py`, an adapter method that verifies by re-read (never trusts the mutation's return), a `DataService` passthrough, a new op-type in the op-log journal reversed by one `undo_last` branch, and a `_guard`-wrapped tool with a write-gate + dry-run→confirm handshake. Monarch's update is a **full replacement**, so the tool reads the rule's current state, applies the delta, and sends the complete input; it refuses any rule carrying components it doesn't model (fail-closed, no silent wipe).

**Tech Stack:** Python 3, pytest, FastMCP, SQLite (cache + journal), aiohttp (live GraphQL). All new tests are offline (fake adapters / `tmp_path`).

**Spec:** `docs/specs/2026-08-26-monarch-mcp-plan4-rule-update-design.md`

## Global Constraints

- **Baseline:** 349 tests pass on branch start (`plan4-rule-update`). Every task ends green; the final task lands at a higher count.
- **Future-only:** every rule write sends `applyToExistingTransactions: false`. Never expose or set it true.
- **Never trust a Monarch rule/V2 mutation's returned boolean** — the update mutation returns only `{ errors }`. Confirm success by re-reading the rule.
- **Fail-closed:** refuse (don't guess) on a missing rule, a non-simple rule, an unresolvable category, or a no-op edit.
- **Operator is preserved, never changed** this slice; actions are never added or removed — only the match `value`, the category target, and the merchant-rename target are editable.
- **No journal schema migration** — reuse the existing `write_journal` table and `record(...)` signature.
- **All git commits end with the trailer:**
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **Test runner:** `.venv/bin/python -m pytest` (the repo venv has aiohttp; bare `python`/`python3` do not).

---

### Task 1: `writes.py` — input builder, token, and simple-rule classifier

**Files:**
- Modify: `financial_mcp/writes.py` (append after `compute_category_rule_token`, ~line 233)
- Test: `tests/test_writes.py` (add to the existing file)

**Interfaces:**
- Consumes: `hashlib` (already imported in `writes.py`).
- Produces (later tasks rely on these exact signatures):
  - `_update_rule_input(rule_id: str, operator: str, value: str, set_category_id: str | None, set_merchant: str | None) -> dict`
  - `compute_rule_update_token(rule_id: str, operator: str, value: str, set_category_id: str | None, set_merchant: str | None) -> str`
  - `rule_complexity(rule: dict) -> list[str]` — human-readable reasons a parsed rule is NOT simple; empty list means simple.
  - `is_simple_rule(rule: dict) -> bool` — `not rule_complexity(rule)`.
  - The parsed-rule dict shape `rule_complexity` reads (produced by Task 3): keys
    `value`, `set_category_id`, `set_merchant`, `uses_original_statement`, `category_ids`,
    `account_ids`, `has_amount_criteria`, `has_original_statement_criteria`,
    `review_status_action`, `send_notification`, `unassign_needs_review`, `has_add_tags`,
    `has_link_goal`, `has_link_savings_goal`. All read with `.get(...)` + falsy defaults, so a
    minimal dict (id/operator/value/set_merchant/set_category_id) classifies as simple.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_writes.py`:

```python
from financial_mcp.writes import (
    _update_rule_input, compute_rule_update_token, rule_complexity, is_simple_rule,
)


def test_update_rule_input_is_create_shape_plus_id_and_preserves_operator():
    inp = _update_rule_input("rule9", "eq", "ubs arena", "cat7", None)
    assert inp["id"] == "rule9"
    # operator is PASSED THROUGH, not hardcoded to "contains"
    assert inp["merchantNameCriteria"] == [{"operator": "eq", "value": "ubs arena"}]
    assert inp["setCategoryAction"] == "cat7"
    assert inp["setMerchantAction"] is None
    assert inp["applyToExistingTransactions"] is False
    assert inp["merchantCriteriaUseOriginalStatement"] is False
    # every other criterion/action is explicit-null
    for k in ("accountIds", "categoryIds", "amountCriteria", "merchantCriteria",
              "originalStatementCriteria", "addTagsAction", "linkGoalAction",
              "linkSavingsGoalAction", "reviewStatusAction", "splitTransactionsAction"):
        assert inp[k] is None


def test_update_rule_input_carries_merchant_rename_as_bare_string():
    inp = _update_rule_input("rule9", "contains", "aplpay ubs", None, "UBS Arena")
    assert inp["setMerchantAction"] == "UBS Arena"
    assert inp["setCategoryAction"] is None


def test_update_token_stable_sensitive_and_distinct_from_create_tokens():
    from financial_mcp.writes import compute_rule_token, compute_category_rule_token
    t = compute_rule_update_token("rule9", "contains", "ubs", "cat7", None)
    assert t == compute_rule_update_token("rule9", "contains", "ubs", "cat7", None)  # stable
    assert t != compute_rule_update_token("rule9", "contains", "ubsX", "cat7", None)  # value
    assert t != compute_rule_update_token("ruleX", "contains", "ubs", "cat7", None)  # id
    assert t != compute_rule_update_token("rule9", "contains", "ubs", "cat8", None)  # category
    assert t != compute_rule_update_token("rule9", "eq", "ubs", "cat7", None)        # operator
    # distinct namespace from the create tokens
    assert t != compute_rule_token("ubs", "cat7")
    assert t != compute_category_rule_token("ubs", "cat7")


def test_is_simple_accepts_the_three_simple_shapes():
    cat_only = {"value": "ubs", "set_category_id": "cat7", "set_merchant": None}
    merch_only = {"value": "amzn", "set_category_id": None, "set_merchant": "Amazon"}
    both = {"value": "ubs", "set_category_id": "cat7", "set_merchant": "UBS Arena"}
    for r in (cat_only, merch_only, both):
        assert is_simple_rule(r) is True
        assert rule_complexity(r) == []


def test_rule_complexity_flags_each_complex_component():
    base = {"value": "ubs", "set_category_id": "cat7", "set_merchant": None}
    assert "matches on amount" in rule_complexity({**base, "has_amount_criteria": True})
    assert "matches on original-statement text" in rule_complexity(
        {**base, "has_original_statement_criteria": True})
    assert "matches on the original statement" in rule_complexity(
        {**base, "uses_original_statement": True})
    assert "matches on category" in rule_complexity({**base, "category_ids": ["c1"]})
    assert "scoped to specific accounts" in rule_complexity({**base, "account_ids": ["a1"]})
    assert "adds tags" in rule_complexity({**base, "has_add_tags": True})
    assert "links a goal" in rule_complexity({**base, "has_link_goal": True})
    assert "links a savings goal" in rule_complexity({**base, "has_link_savings_goal": True})
    assert "sets review status" in rule_complexity({**base, "review_status_action": "reviewed"})
    assert "sends a notification" in rule_complexity({**base, "send_notification": True})
    assert "unassigns needs-review" in rule_complexity({**base, "unassign_needs_review": True})


def test_rule_complexity_flags_missing_merchant_criterion_and_missing_action():
    assert "no merchant-name criterion" in rule_complexity(
        {"value": None, "set_category_id": "cat7"})
    assert "no set-category or set-merchant action" in rule_complexity(
        {"value": "ubs", "set_category_id": None, "set_merchant": None})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_writes.py -k "update_rule or update_token or is_simple or rule_complexity" -v`
Expected: FAIL with `ImportError`/`cannot import name`.

- [ ] **Step 3: Implement the helpers**

Append to `financial_mcp/writes.py`:

```python
def _update_rule_input(
    rule_id: str, operator: str, value: str,
    set_category_id: str | None, set_merchant: str | None,
) -> dict:
    """Build the UpdateTransactionRuleInput for a FUTURE-ONLY simple rule edit.

    This is the create input (mirrors `_rename_rule_input`/`_category_rule_input`)
    PLUS `id`, with two differences: the match `operator` is a parameter (preserving
    the rule's existing operator — NOT hardcoded "contains"), and both set-actions are
    populated from the caller's desired end-state. Every other criterion/action is sent
    explicit-null: correct because the tool only edits rules verified simple (those
    fields were already null), and it matches the DevTools-captured full-replacement payload.
    """
    return {
        "id": rule_id,
        "accountIds": None,
        "categoryIds": None,
        "amountCriteria": None,
        "merchantCriteria": None,
        "originalStatementCriteria": None,
        "merchantCriteriaUseOriginalStatement": False,
        "merchantNameCriteria": [{"operator": operator, "value": value}],
        "setMerchantAction": set_merchant,
        "applyToExistingTransactions": False,
        "addTagsAction": None,
        "linkGoalAction": None,
        "linkSavingsGoalAction": None,
        "reviewStatusAction": None,
        "setCategoryAction": set_category_id,
        "splitTransactionsAction": None,
    }


def compute_rule_update_token(
    rule_id: str, operator: str, value: str,
    set_category_id: str | None, set_merchant: str | None,
) -> str:
    """Content hash of an edit's exact end-state, in a namespace distinct from the
    create tokens so a create preview can't be replayed as an update confirm."""
    key = f"{rule_id}#upd#{operator}:{value}=>cat:{set_category_id}|mer:{set_merchant}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def rule_complexity(rule: dict) -> list[str]:
    """Reasons a parsed rule is NOT simple (empty list == simple, safe to edit).

    Reads the enriched parse produced by the adapter with `.get()` + falsy defaults,
    so a minimal rule dict (id/operator/value/set_category_id/set_merchant) is simple.
    """
    reasons: list[str] = []
    if not rule.get("value"):
        reasons.append("no merchant-name criterion")
    if not (rule.get("set_category_id") or rule.get("set_merchant")):
        reasons.append("no set-category or set-merchant action")
    if rule.get("uses_original_statement"):
        reasons.append("matches on the original statement")
    if rule.get("category_ids"):
        reasons.append("matches on category")
    if rule.get("account_ids"):
        reasons.append("scoped to specific accounts")
    if rule.get("has_amount_criteria"):
        reasons.append("matches on amount")
    if rule.get("has_original_statement_criteria"):
        reasons.append("matches on original-statement text")
    if rule.get("review_status_action"):
        reasons.append("sets review status")
    if rule.get("send_notification"):
        reasons.append("sends a notification")
    if rule.get("unassign_needs_review"):
        reasons.append("unassigns needs-review")
    if rule.get("has_add_tags"):
        reasons.append("adds tags")
    if rule.get("has_link_goal"):
        reasons.append("links a goal")
    if rule.get("has_link_savings_goal"):
        reasons.append("links a savings goal")
    return reasons


def is_simple_rule(rule: dict) -> bool:
    return not rule_complexity(rule)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_writes.py -v`
Expected: PASS (all, including pre-existing writes tests).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/writes.py tests/test_writes.py
git commit -m "$(cat <<'EOF'
feat: rule-update input builder, token, and simple-rule classifier

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `queries.py` — update mutation + enriched rules read

**Files:**
- Modify: `financial_mcp/monarch/queries.py` (`GET_TRANSACTION_RULES` at lines 342-352; add `UPDATE_TRANSACTION_RULE` after `DELETE_TRANSACTION_RULE` at line 371)
- Test: none directly (constants are strings; exercised via Task 3's adapter tests). This task pairs with Task 3.

**Interfaces:**
- Produces: `UPDATE_TRANSACTION_RULE` (str), and an extended `GET_TRANSACTION_RULES` selecting the fields Task 3's parse maps. All added fields were proven selectable by the spike (no 400 risk).

- [ ] **Step 1: Replace `GET_TRANSACTION_RULES` with the enriched selection**

In `financial_mcp/monarch/queries.py`, replace the current `GET_TRANSACTION_RULES` block (lines 342-352) with:

```python
GET_TRANSACTION_RULES = """
  query GetTransactionRules {
    transactionRules {
      id
      order
      merchantCriteria { operator value __typename }
      merchantCriteriaUseOriginalStatement
      categoryIds
      accountIds
      amountCriteria { operator __typename }
      originalStatementCriteria { operator __typename }
      setMerchantAction { id name __typename }
      setCategoryAction { id name __typename }
      reviewStatusAction
      sendNotificationAction
      unassignNeedsReviewByUserAction
      addTagsAction { id __typename }
      linkGoalAction { id __typename }
      linkSavingsGoalAction { id __typename }
      __typename
    }
  }
"""
```

- [ ] **Step 2: Add the update mutation constant**

After the `DELETE_TRANSACTION_RULE` block (ends line 371), append:

```python
UPDATE_TRANSACTION_RULE = """
  mutation Common_UpdateTransactionRuleMutationV2($input: UpdateTransactionRuleInput!) {
    updateTransactionRuleV2(input: $input) {
      errors { message code __typename }
      __typename
    }
  }
"""
```

- [ ] **Step 3: Sanity-check the module imports**

Run: `.venv/bin/python -c "from financial_mcp.monarch.queries import UPDATE_TRANSACTION_RULE, GET_TRANSACTION_RULES; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Run the full suite (must still be green — the enriched read is backward-compatible)**

Run: `.venv/bin/python -m pytest -q`
Expected: 349 passed (Task 3 not yet wired, but existing adapter tests build minimal `transactionRules` responses and the current parse ignores unknown fields).

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/queries.py
git commit -m "$(cat <<'EOF'
feat: UpdateTransactionRuleV2 mutation + enriched rules read query

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `community_adapter.py` — enriched parse + `update_transaction_rule`

**Files:**
- Modify: `financial_mcp/monarch/community_adapter.py` (imports at lines 17-37; `get_transaction_rules` at lines 411-425; add `update_transaction_rule` after `delete_transaction_rule` at line 458)
- Test: `tests/test_merchant_rule_adapter.py` (append)

**Interfaces:**
- Consumes: `UPDATE_TRANSACTION_RULE`, enriched `GET_TRANSACTION_RULES` (Task 2); `_update_rule_input` (Task 1).
- Produces:
  - `CommunityAdapter.get_transaction_rules()` now returns dicts with the enriched keys (Task 1's shape) plus the existing `id`/`operator`/`value`/`set_merchant`/`set_category`.
  - `CommunityAdapter.update_transaction_rule(rule_id, operator, value, set_category_id, set_merchant) -> bool` — `True` only when a post-write re-read confirms the intended end-state.

- [ ] **Step 1: Write the failing adapter tests**

Append to `tests/test_merchant_rule_adapter.py`:

```python
def test_get_rules_parse_surfaces_enriched_fields():
    ad = _seq_adapter([
        {"transactionRules": [{
            "id": "r1", "order": 3,
            "merchantCriteria": [{"operator": "eq", "value": "ubs arena"}],
            "merchantCriteriaUseOriginalStatement": False,
            "categoryIds": None, "accountIds": None,
            "amountCriteria": None, "originalStatementCriteria": None,
            "setMerchantAction": {"id": "m9", "name": "UBS Arena"},
            "setCategoryAction": {"id": "c7", "name": "Entertainment"},
            "reviewStatusAction": None, "sendNotificationAction": False,
            "unassignNeedsReviewByUserAction": False,
            "addTagsAction": None, "linkGoalAction": None, "linkSavingsGoalAction": None,
        }]},
    ])
    (r,) = ad.get_transaction_rules()
    assert r["id"] == "r1" and r["order"] == 3
    assert r["operator"] == "eq" and r["value"] == "ubs arena"
    assert r["set_merchant"] == "UBS Arena"
    assert r["set_category"] == "Entertainment" and r["set_category_id"] == "c7"
    assert r["has_amount_criteria"] is False and r["category_ids"] == []


def test_update_rule_true_when_reread_matches():
    ad = _seq_adapter([
        {"updateTransactionRuleV2": {"errors": []}},                     # mutation
        {"transactionRules": [{                                          # re-read reflects it
            "id": "r1",
            "merchantCriteria": [{"operator": "contains", "value": "ubs"}],
            "setMerchantAction": None,
            "setCategoryAction": {"id": "c7", "name": "Entertainment"}}]},
    ])
    assert ad.update_transaction_rule("r1", "contains", "ubs", "c7", None) is True


def test_update_rule_false_on_error_payload():
    ad = _seq_adapter([
        {"updateTransactionRuleV2": {"errors": [{"message": "bad", "code": "X"}]}},
    ])
    assert ad.update_transaction_rule("r1", "contains", "ubs", "c7", None) is False


def test_update_rule_false_when_reread_does_not_match():
    ad = _seq_adapter([
        {"updateTransactionRuleV2": {"errors": []}},
        {"transactionRules": [{                                          # value did not change
            "id": "r1",
            "merchantCriteria": [{"operator": "contains", "value": "OLD"}],
            "setMerchantAction": None,
            "setCategoryAction": {"id": "c7", "name": "Entertainment"}}]},
    ])
    assert ad.update_transaction_rule("r1", "contains", "ubs", "c7", None) is False


def test_csv_update_rule_raises():
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    import pytest
    ad = CsvAdapter("unused_txns.csv", "unused_balances.csv")
    with pytest.raises(RuntimeError):
        ad.update_transaction_rule("r1", "contains", "ubs", "c7", None)
```

(Note: `test_csv_update_rule_raises` will pass only after Task 4 adds the CSV stub. It is
placed here with its sibling adapter tests; run it in Task 4. Steps 2/4 below scope the run to
the community-adapter tests so this task stays self-contained.)

- [ ] **Step 2: Run the community-adapter tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_merchant_rule_adapter.py -k "enriched or update_rule" -v`
Expected: FAIL — `get_transaction_rules` lacks enriched keys; `update_transaction_rule` doesn't exist.

- [ ] **Step 3: Enrich the parse and add the method**

In `financial_mcp/monarch/community_adapter.py`:

Extend the query imports (lines 17-37) to add `UPDATE_TRANSACTION_RULE`, and the writes import (line 37) to add `_update_rule_input`:

```python
from financial_mcp.monarch.queries import (
    # ... existing names ...
    UPDATE_TRANSACTION_RULE,
)
from financial_mcp.writes import _category_rule_input, _rename_rule_input, _update_rule_input
```

Replace `get_transaction_rules` (lines 411-425) with:

```python
    def get_transaction_rules(self) -> list[dict]:
        data = self._run(self._transport("GetTransactionRules", GET_TRANSACTION_RULES, {}))
        out = []
        for r in data.get("transactionRules") or []:
            crit = (r.get("merchantCriteria") or [{}])[0] if r.get("merchantCriteria") else {}
            sm = r.get("setMerchantAction") or {}
            sc = r.get("setCategoryAction") or {}
            out.append({
                "id": r.get("id"),
                "operator": crit.get("operator"),
                "value": crit.get("value"),
                "set_merchant": sm.get("name"),
                "set_category": sc.get("name"),
                "set_category_id": sc.get("id"),
                "order": r.get("order"),
                "uses_original_statement": bool(r.get("merchantCriteriaUseOriginalStatement")),
                "category_ids": r.get("categoryIds") or [],
                "account_ids": r.get("accountIds") or [],
                "has_amount_criteria": bool(r.get("amountCriteria")),
                "has_original_statement_criteria": bool(r.get("originalStatementCriteria")),
                "review_status_action": r.get("reviewStatusAction"),
                "send_notification": bool(r.get("sendNotificationAction")),
                "unassign_needs_review": bool(r.get("unassignNeedsReviewByUserAction")),
                "has_add_tags": bool(r.get("addTagsAction")),
                "has_link_goal": bool(r.get("linkGoalAction")),
                "has_link_savings_goal": bool(r.get("linkSavingsGoalAction")),
            })
        return out
```

After `delete_transaction_rule` (ends line 458), add:

```python
    def update_transaction_rule(self, rule_id, operator, value,
                                set_category_id, set_merchant) -> bool:
        # updateTransactionRuleV2 returns only {errors}; verify by re-read.
        payload = self._run(self._transport(
            "Common_UpdateTransactionRuleMutationV2", UPDATE_TRANSACTION_RULE,
            {"input": _update_rule_input(rule_id, operator, value, set_category_id, set_merchant)}))
        errors = ((payload.get("updateTransactionRuleV2") or {}).get("errors")) or []
        if errors:
            return False
        rule = next((r for r in self.get_transaction_rules() if r["id"] == rule_id), None)
        if rule is None:
            return False
        return (rule.get("operator") == operator
                and rule.get("value") == value
                and (rule.get("set_category_id") or None) == (set_category_id or None)
                and (rule.get("set_merchant") or None) == (set_merchant or None))
```

- [ ] **Step 4: Run the community-adapter tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_merchant_rule_adapter.py -k "enriched or update_rule and not csv" -v`
Expected: PASS. (The CSV stub test runs in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/community_adapter.py tests/test_merchant_rule_adapter.py
git commit -m "$(cat <<'EOF'
feat: enriched rules parse + community update_transaction_rule (verify by re-read)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Wire the adapter contract — ABC method, CSV stub, fake-sweep

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (add abstractmethod after `delete_transaction_rule`, line 71)
- Modify: `financial_mcp/monarch/csv_adapter.py` (add stub after `delete_transaction_rule`, line 134)
- Modify: `tests/test_mcp_tools.py` (`FakeAdapter` line 19; `CategoryCrudAdapter` line 1315)
- Modify: `tests/test_data_service.py` (`FakeAdapter` line 19; `_StubAdapter` line 180)
- Test: reuse `tests/test_merchant_rule_adapter.py::test_csv_update_rule_raises` (added in Task 3)

**Interfaces:**
- Produces: `MonarchAdapter.update_transaction_rule(rule_id, operator, value, set_category_id, set_merchant) -> bool` as an abstract method; concrete `False`-returning stubs on the four directly-instantiated test fakes so the 349-test baseline still instantiates them; a `RuntimeError` stub on `CsvAdapter`.
- Note: `tests/test_adapter_interface.py::Partial` deliberately implements only `get_transactions` and must stay abstract — do NOT add the method there; adding a new abstractmethod keeps it abstract (its `test_partial_subclass_still_abstract` still passes).

- [ ] **Step 1: Add the abstract method**

In `financial_mcp/monarch/adapter.py`, after `delete_transaction_rule` (line 71):

```python
    @abc.abstractmethod
    def update_transaction_rule(self, rule_id: str, operator: str, value: str,
                                set_category_id: str | None, set_merchant: str | None) -> bool: ...
```

- [ ] **Step 2: Run the suite to watch it go red (proves the sweep is needed)**

Run: `.venv/bin/python -m pytest -q`
Expected: MANY failures — every direct `MonarchAdapter` subclass now fails to instantiate with `TypeError: Can't instantiate abstract class ...`.

- [ ] **Step 3: Add the CSV stub**

In `financial_mcp/monarch/csv_adapter.py`, after `delete_transaction_rule` (line 134):

```python
    def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant) -> bool:
        raise RuntimeError("writes are not available in CSV mode")
```

- [ ] **Step 4: Sweep the four base fakes**

Add this identical stub to each of: `tests/test_mcp_tools.py::FakeAdapter` (after
`delete_transaction_rule`, line 100), `tests/test_mcp_tools.py::CategoryCrudAdapter` (after its
`delete_transaction_rule`, ~line 1394), `tests/test_data_service.py::FakeAdapter` (after its
`delete_transaction_rule`, ~line 107), and `tests/test_data_service.py::_StubAdapter` (after its
`delete_transaction_rule`, ~line 219):

```python
    def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant):
        return False
```

- [ ] **Step 5: Run the suite to verify green again**

Run: `.venv/bin/python -m pytest -q`
Expected: 349 + the Task 1/3 additions pass; specifically confirm `test_csv_update_rule_raises` now passes:
`.venv/bin/python -m pytest tests/test_merchant_rule_adapter.py::test_csv_update_rule_raises -v`

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/monarch/adapter.py financial_mcp/monarch/csv_adapter.py \
        tests/test_mcp_tools.py tests/test_data_service.py
git commit -m "$(cat <<'EOF'
feat: update_transaction_rule adapter contract (ABC + CSV stub + fake sweep)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `data_service.py` — passthrough

**Files:**
- Modify: `financial_mcp/data_service.py` (add after `delete_transaction_rule`, line 146)
- Test: `tests/test_data_service.py` (append)

**Interfaces:**
- Consumes: `MonarchAdapter.update_transaction_rule` (Task 4).
- Produces: `DataService.update_transaction_rule(rule_id, operator, value, set_category_id, set_merchant) -> bool` — a pure passthrough. No cache invalidation: rules are uncached and the edit is future-only (existing cached transactions are untouched), matching `create_merchant_rule`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_data_service.py`:

```python
def test_update_transaction_rule_passthrough(tmp_path):
    class _UpdAdapter(FakeAdapter):
        def __init__(self):
            self.calls = []
        def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant):
            self.calls.append((rule_id, operator, value, set_category_id, set_merchant))
            return True

    clock = FakeClock()
    adapter = _UpdAdapter()
    svc, _ = _svc(tmp_path, clock, adapter)

    assert svc.update_transaction_rule("r1", "contains", "ubs", "c7", None) is True
    assert adapter.calls == [("r1", "contains", "ubs", "c7", None)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_data_service.py::test_update_transaction_rule_passthrough -v`
Expected: FAIL — `DataService` has no `update_transaction_rule`.

- [ ] **Step 3: Implement the passthrough**

In `financial_mcp/data_service.py`, after `delete_transaction_rule` (line 146):

```python
    def update_transaction_rule(self, rule_id: str, operator: str, value: str,
                                set_category_id: str | None, set_merchant: str | None) -> bool:
        return self._adapter.update_transaction_rule(
            rule_id, operator, value, set_category_id, set_merchant)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_data_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/data_service.py tests/test_data_service.py
git commit -m "$(cat <<'EOF'
feat: DataService.update_transaction_rule passthrough

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `undo_journal.py` — `update_rule` op-type + history summary

**Files:**
- Modify: `financial_mcp/cache/undo_journal.py` (`REVERSIBLE_OPS` line 9; `history` branch chain lines 159-170)
- Test: `tests/test_undo_journal.py` (append)

**Interfaces:**
- Produces: `"update_rule"` recognized as reversible (so `last_undoable_batch`/`undoable_rows`
  include it); `history()` renders `"Rule edit: {label_old} → {label_new} ×{n}"` for it.
  The row stores `old_value`/`new_value` as JSON strings (written by Task 7) and
  `label_old`/`label_new` as the human summaries `history` reads.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_undo_journal.py`:

```python
def test_update_rule_is_reversible_and_history_renders(tmp_path):
    import json
    from financial_mcp.cache.undo_journal import UndoJournal, REVERSIBLE_OPS
    assert "update_rule" in REVERSIBLE_OPS

    j = UndoJournal(str(tmp_path / "j.db"))
    old = json.dumps({"operator": "contains", "value": "OLD",
                      "set_category_id": "c7", "set_merchant": None})
    new = json.dumps({"operator": "contains", "value": "NEW",
                      "set_category_id": "c7", "set_merchant": None})
    j.record("b1", "update_rule", "r1", old, new,
             "contains 'OLD' → cat 'Entertainment'", "contains 'NEW' → cat 'Entertainment'")
    j.mark_succeeded("b1", "r1")

    assert j.last_undoable_batch() == "b1"
    (row,) = j.undoable_rows("b1")
    assert row["op_type"] == "update_rule"
    assert json.loads(row["old_value"])["value"] == "OLD"

    (h,) = j.history()
    assert "Rule edit:" in h["summary"]
    assert "OLD" in h["summary"] and "NEW" in h["summary"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_undo_journal.py::test_update_rule_is_reversible_and_history_renders -v`
Expected: FAIL — `"update_rule" not in REVERSIBLE_OPS`.

- [ ] **Step 3: Add the op-type and the history branch**

In `financial_mcp/cache/undo_journal.py`, extend `REVERSIBLE_OPS` (line 9):

```python
REVERSIBLE_OPS = ("recategorize", "rename_category", "delete_category", "rename_merchant", "create_rule", "create_category_rule", "update_rule")
```

In `history()`, add a branch before the final `else` (after the `create_category_rule` branch, line 168):

```python
            elif op_type == "update_rule":
                summary = f"Rule edit: {old_n} → {new_n} ×{n}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_undo_journal.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/cache/undo_journal.py tests/test_undo_journal.py
git commit -m "$(cat <<'EOF'
feat: journal update_rule op-type + history summary

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `mcp_server.py` — the `update_transaction_rule` tool

**Files:**
- Modify: `financial_mcp/mcp_server.py` (add `import json` near line 4; add the tool after `create_category_rule`, line 364)
- Test: `tests/test_mcp_tools.py` (append — new `UpdateRuleAdapter` + tool tests)

**Interfaces:**
- Consumes: `writes.rule_complexity`, `writes.compute_rule_update_token`, `writes.resolve_category`, `writes.WriteError` (Task 1); `svc.transaction_rules()`, `svc.update_transaction_rule(...)` (Task 5); `svc.categories()`; `journal.record/mark_succeeded` (Task 6); `_writes_enabled`, `_guard`, `write_mode`, `uuid`, `json`.
- Produces: the `update_transaction_rule(rule_id, match_text=None, set_category=None, set_merchant=None, confirm_token=None) -> dict` tool. Journals an `"update_rule"` row whose `old_value`/`new_value` are JSON `{operator, value, set_category_id, set_merchant}` — consumed by Task 8's `undo_last` branch.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_tools.py` (near the other rule-tool tests, after line ~2110). This
adds a dedicated adapter with the enriched read shape and update support:

```python
class UpdateRuleAdapter(BulkRecatAdapter):
    """In-memory rules store with the enriched read shape + update support."""

    def __init__(self):
        self.rules: list[dict] = []

    def get_categories(self):
        from financial_mcp.monarch.models import Category
        return [
            Category(id="c7", name="Entertainment", group="Lifestyle"),
            Category(id="c8", name="Shopping", group="Lifestyle"),
        ]

    def get_transaction_rules(self):
        return [dict(r) for r in self.rules]

    def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant):
        name_by_id = {"c7": "Entertainment", "c8": "Shopping"}
        for r in self.rules:
            if r["id"] == rule_id:
                r["operator"] = operator
                r["value"] = value
                r["set_category_id"] = set_category_id
                r["set_category"] = name_by_id.get(set_category_id)
                r["set_merchant"] = set_merchant
                return True
        return False

    def delete_transaction_rule(self, rule_id):
        n = len(self.rules)
        self.rules = [r for r in self.rules if r["id"] != rule_id]
        return len(self.rules) < n


def _simple_cat_rule():
    return {"id": "r1", "operator": "contains", "value": "ubs", "order": 0,
            "set_merchant": None, "set_category": "Entertainment", "set_category_id": "c7"}


def test_update_rule_off_mode_refuses(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="off")
    out = _tool(server, "update_transaction_rule")("r1", match_text="ubs arena")
    assert "error" in out
    assert ad.rules[0]["value"] == "ubs"  # unchanged


def test_update_rule_not_found_refuses(tmp_path):
    ad = UpdateRuleAdapter()
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    out = _tool(server, "update_transaction_rule")("nope", match_text="x")
    assert "error" in out and "nope" in out["error"]


def test_update_rule_refuses_complex_rule(tmp_path):
    ad = UpdateRuleAdapter()
    ad.rules.append({**_simple_cat_rule(), "has_amount_criteria": True})
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    out = _tool(server, "update_transaction_rule")("r1", match_text="ubs arena")
    assert "refused" in out and "amount" in out["refused"]
    assert ad.rules[0]["value"] == "ubs"


def test_update_rule_no_op_refuses(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    out = _tool(server, "update_transaction_rule")("r1", match_text="ubs")  # same value
    assert "refused" in out


def test_update_rule_unresolvable_category_refuses(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    out = _tool(server, "update_transaction_rule")("r1", set_category="Nonexistent")
    assert "error" in out
    assert ad.rules[0]["value"] == "ubs"


def test_update_rule_dry_run_then_confirm(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    tool = _tool(server, "update_transaction_rule")

    preview = tool("r1", match_text="ubs arena")
    assert preview["dry_run"] is True
    assert preview["before"]["match"] == "ubs"
    assert preview["after"]["match"] == "ubs arena"
    assert "future" in preview["message"].lower()
    assert "confirm_token" in preview
    assert ad.rules[0]["value"] == "ubs"  # nothing changed on preview

    out = tool("r1", match_text="ubs arena", confirm_token=preview["confirm_token"])
    assert out["updated"] is True
    assert ad.rules[0]["value"] == "ubs arena"
    assert journal.last_undoable_batch() is not None


def test_update_rule_change_category_full_mode(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="full")
    out = _tool(server, "update_transaction_rule")("r1", set_category="Shopping")
    assert out["updated"] is True
    assert out["after"]["category"] == "Shopping"
    assert ad.rules[0]["set_category_id"] == "c8"


def test_update_rule_failed_write_reports_and_does_not_journal(tmp_path):
    class _FailAdapter(UpdateRuleAdapter):
        def update_transaction_rule(self, *a, **k):
            return False
    ad = _FailAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="full")
    out = _tool(server, "update_transaction_rule")("r1", match_text="ubs arena")
    assert out["updated"] is False
    assert journal.last_undoable_batch() is None  # nothing journaled
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k "update_rule" -v`
Expected: FAIL — no `update_transaction_rule` tool registered.

- [ ] **Step 3: Add the `json` import and the tool**

In `financial_mcp/mcp_server.py`, add near the top imports (after `import uuid`, line 4):

```python
import json
```

After the `create_category_rule` tool (ends line 364), add:

```python
    @server.tool()
    def update_transaction_rule(
        rule_id: str, match_text: str | None = None,
        set_category: str | None = None, set_merchant: str | None = None,
        confirm_token: str | None = None,
    ) -> dict:
        """Edit an EXISTING Monarch transaction rule in place (its id and order are
        preserved). You can change any of: the match text (`match_text`), the category
        it sets (`set_category`, an existing category name), and the merchant it renames
        to (`set_merchant`). Only rules that match on merchant name and do nothing but
        set a category and/or merchant can be edited — a rule that also matches on amount,
        original statement, category, or account, or that adds tags / links goals / etc.,
        is refused (edit it in Monarch → Settings → Rules). Future-only: the edit changes
        how the rule labels transactions imported from now on; it does NOT retroactively
        recategorize or rename past transactions. In 'safe' mode this is two-phase: call
        without confirm_token to preview (nothing changes) and get a confirm_token, then
        call again with it to apply. In 'full' mode it applies directly. Reversible via
        undo_last (a compensating edit back to the old values).
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}
        if not rule_id or not rule_id.strip():
            return {"error": "rule_id must be a non-empty rule id"}

        def _run():
            rule = next((r for r in svc.transaction_rules() if r.get("id") == rule_id), None)
            if rule is None:
                return {"error": f"no rule with id {rule_id}"}
            reasons = writes.rule_complexity(rule)
            if reasons:
                return {"refused": ("this rule is too complex to edit safely — "
                                    + "; ".join(reasons)
                                    + ". Edit it in Monarch → Settings → Rules."),
                        "rule_id": rule_id}

            operator = rule.get("operator")
            cur_value = rule.get("value")
            cur_cat_id = rule.get("set_category_id")
            cur_cat_name = rule.get("set_category")
            cur_merchant = rule.get("set_merchant")

            new_value = match_text.strip() if (match_text and match_text.strip()) else cur_value
            if set_category is not None and set_category.strip():
                try:
                    target = writes.resolve_category(set_category, svc.categories())
                except writes.WriteError as exc:
                    return {"error": str(exc)}
                new_cat_id, new_cat_name = target.id, target.name
            else:
                new_cat_id, new_cat_name = cur_cat_id, cur_cat_name
            new_merchant = (set_merchant.strip()
                            if (set_merchant and set_merchant.strip()) else cur_merchant)

            if (new_value == cur_value and new_cat_id == cur_cat_id
                    and new_merchant == cur_merchant):
                return {"refused": "that edit changes nothing", "rule_id": rule_id}

            before = {"match": cur_value, "category": cur_cat_name, "rename_to": cur_merchant}
            after = {"match": new_value, "category": new_cat_name, "rename_to": new_merchant}
            token = writes.compute_rule_update_token(
                rule_id, operator, new_value, new_cat_id, new_merchant)

            if write_mode == "safe" and (confirm_token is None or confirm_token != token):
                return {
                    "dry_run": True, "rule_id": rule_id, "before": before, "after": after,
                    "message": ("This edits an existing rule in place (its id and order are "
                                "preserved) and applies to FUTURE transactions only — it will "
                                "NOT recategorize or rename past transactions. Confirm to apply "
                                "the edit."),
                    "confirm_token": token, "mode": write_mode,
                }

            ok = svc.update_transaction_rule(
                rule_id, operator, new_value, new_cat_id, new_merchant)
            if not ok:
                return {"updated": False,
                        "reason": "Monarch did not apply the edit (re-read did not reflect it)"}

            old_state = json.dumps({"operator": operator, "value": cur_value,
                                    "set_category_id": cur_cat_id, "set_merchant": cur_merchant})
            new_state = json.dumps({"operator": operator, "value": new_value,
                                    "set_category_id": new_cat_id, "set_merchant": new_merchant})
            label_old = _rule_label(cur_value, cur_cat_name, cur_merchant)
            label_new = _rule_label(new_value, new_cat_name, new_merchant)
            batch_id = uuid.uuid4().hex
            journal.record(batch_id, "update_rule", rule_id, old_state, new_state,
                           label_old, label_new)
            journal.mark_succeeded(batch_id, rule_id)
            return {"updated": True, "rule_id": rule_id, "before": before, "after": after,
                    "applied_to_existing": False, "batch_id": batch_id,
                    "undo_hint": "call undo_last to revert this edit"}

        return _guard(_run)
```

Add the small label helper just above the tool (module-local, near the other rule tools):

```python
    def _rule_label(value, cat_name, merchant):
        parts = [f"contains '{value}'"]
        if cat_name:
            parts.append(f"cat '{cat_name}'")
        if merchant:
            parts.append(f"→ '{merchant}'")
        return " ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k "update_rule" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "$(cat <<'EOF'
feat: update_transaction_rule MCP tool (gated, dry-run, fail-closed to simple rules)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `undo_last` reverses an edit + full round-trip

**Files:**
- Modify: `financial_mcp/mcp_server.py` (`undo_last` dispatch chain lines 984-997; failure chain lines 1002-1019; docstring lines 964-970)
- Test: `tests/test_mcp_tools.py` (append)

**Interfaces:**
- Consumes: the `"update_rule"` journal row (Task 7); `svc.update_transaction_rule` (Task 5); `json`.
- Produces: `undo_last` reverses an `update_rule` batch by re-applying the journaled old state; a failed compensating edit stays retryable (row not marked undone) and reports `{"rule_id": ..., "reason": ...}`.

- [ ] **Step 1: Write the failing round-trip test**

Append to `tests/test_mcp_tools.py`:

```python
def test_update_rule_undo_round_trip(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="full")
    update = _tool(server, "update_transaction_rule")
    undo = _tool(server, "undo_last")

    update("r1", match_text="ubs arena", set_category="Shopping")
    assert ad.rules[0]["value"] == "ubs arena"
    assert ad.rules[0]["set_category_id"] == "c8"

    out = undo()
    assert out["reversed"] == 1 and out["failed"] == 0
    # restored to the original state
    assert ad.rules[0]["value"] == "ubs"
    assert ad.rules[0]["set_category_id"] == "c7"


def test_update_rule_undo_failure_stays_retryable(tmp_path):
    class _FailOnUndoAdapter(UpdateRuleAdapter):
        def __init__(self):
            super().__init__()
            self._allow = True
        def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant):
            if not self._allow:      # the compensating edit (undo) fails
                return False
            return super().update_transaction_rule(
                rule_id, operator, value, set_category_id, set_merchant)

    ad = _FailOnUndoAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="full")
    _tool(server, "update_transaction_rule")("r1", match_text="ubs arena")
    ad._allow = False

    out = _tool(server, "undo_last")()
    assert out["reversed"] == 0 and out["failed"] == 1
    assert out["failures"][0]["rule_id"] == "r1"
    # still retryable: batch not closed
    assert journal.last_undoable_batch() is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k "update_rule_undo" -v`
Expected: FAIL — `undo_last` has no `update_rule` branch (`ok = False` default → reports failed, and the round-trip doesn't restore).

- [ ] **Step 3: Add the undo dispatch + failure branches**

In `financial_mcp/mcp_server.py` `undo_last` `_run()`, extend the op-type dispatch chain — after
the `create_category_rule` branch (line 995), before the `else` (line 996):

```python
                elif r["op_type"] == "update_rule":
                    old = json.loads(r["old_value"])
                    ok = svc.update_transaction_rule(
                        r["target_id"], old["operator"], old["value"],
                        old["set_category_id"], old["set_merchant"])
```

And in the failure-reporting chain — after the `create_category_rule` failure branch (line 1016),
before the final `else` (line 1017):

```python
                    elif r["op_type"] == "update_rule":
                        failures.append({"rule_id": r["target_id"],
                                         "reason": "Monarch did not apply the compensating edit"})
```

Update the `undo_last` docstring (lines 964-970) to mention rule edits — change the leading
sentence to include "rule edit" in the reversible list:

```python
        """Reverse the most recent recategorization, category-rename, category-delete,
        merchant-rename, created auto-categorize rule, created merchant-rename rule, or
        rule edit batch (multi-level: call again to step back to the previous one). Undo
        is a compensating write — it re-sets each item to the value it had when the
        write ran; deletions are reversed by recreating the category; created rules are
        reversed by deleting the rule; rule edits are reversed by re-applying the old
        values. Does not detect edits you made afterward. Refuses in write_mode 'off'.
        """
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -k "update_rule" -v`
Expected: PASS.

- [ ] **Step 5: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (349 baseline + the new tests across Tasks 1, 3, 5, 6, 7, 8).

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "$(cat <<'EOF'
feat: undo_last reverses update_rule via compensating edit (round-trip proven)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 GraphQL constants → Task 2. ✅
- §3.2 `writes.py` helpers (`_update_rule_input`, `compute_rule_update_token`, `is_simple_rule`/`rule_complexity`) → Task 1. ✅
- §3.3 adapter (ABC + community verify-by-reread + CSV raise) → Tasks 3, 4. ✅
- §3.4 DataService passthrough → Task 5. ✅
- §3.5 journal `update_rule` op-type + history → Task 6. ✅
- §3.6 MCP tool (gate, load, simple-guard, delta, no-op, token, dry-run/execute, journal) → Task 7; `undo_last` branch → Task 8. ✅
- §4 undo semantics (compensating edit, verify-by-reread, retryable failure) → Tasks 3, 8. ✅
- §5 refusals (off / not-found / non-simple / unresolvable-category / no-op / failed-reread / CSV) → Tasks 3, 4, 7. ✅
- §6 testing (writes / fake / community / tool / journal-undo / regression fence) → distributed across all tasks. ✅
- §9 live-prove is post-implementation (owner-run), not a coding task. ✅

**2. Placeholder scan:** No TBD/TODO; every code and test step carries full content. ✅

**3. Type consistency:** `update_transaction_rule(rule_id, operator, value, set_category_id, set_merchant) -> bool` is identical across ABC (Task 4), CSV (Task 4), community (Task 3), DataService (Task 5), and the fakes. `compute_rule_update_token`/`_update_rule_input` argument order matches between `writes.py` (Task 1), the community adapter (Task 3), and the tool (Task 7). The journal `old_value` JSON keys (`operator`, `value`, `set_category_id`, `set_merchant`) written in Task 7 match those read in Task 8's `undo_last` branch. The enriched-parse keys produced in Task 3 match those read by `rule_complexity` in Task 1. ✅

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-26-rule-update.md`.
