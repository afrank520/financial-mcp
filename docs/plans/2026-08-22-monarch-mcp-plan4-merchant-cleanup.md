# Plan 4 Merchant Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only merchant-cleanup advisory tool and a gated, reversible bulk merchant-rename tool to the Monarch MCP server, following the proven write-gate/dry-run/journal/undo template.

**Architecture:** Merchant id + logo now flow through the `Transaction` model from a small `GET_TRANSACTIONS` query addition. A new confirmed-feasible adapter mutation (`Web_TransactionDrawerUpdateTransaction` with `input.name`) renames the merchant on a transaction; a new read query surfaces existing transaction rules. Two MCP tools sit on top: `merchant_cleanup_suggestions` (read-only, logo/rule-aware, Retail-Sync-aware) and `rename_merchant_matching` (bulk "contains" rename behind the write gate, reversible via the existing op-log `undo_last`). No cache-schema migration.

**Tech Stack:** Python 3.14, FastMCP, pydantic, sqlite3 (op-log), pytest. Live GraphQL over cookie auth (`CommunityAdapter`).

**Spec:** `docs/specs/2026-08-22-monarch-mcp-plan4-merchant-cleanup-design.md`

## Global Constraints

- **Fail-closed:** every write refuses cleanly (never a bare stack trace); over the batch cap it refuses without truncating; blank target name refuses.
- **Write gate:** all mutating tools honor `write_mode` (`off` → refuse; `safe` → dry-run→confirm handshake; `full` → one-shot). Read-only tools never gate.
- **No cache-schema migration:** reuse the `write_journal` op-log; `old_value`/`new_value` hold merchant **name** strings for the new op.
- **Retail Sync awareness:** `rename_merchant_matching` excludes transactions tagged `"Retail Sync"` unless `include_retail_sync=True`; the advisory tool flags Retail-Sync-managed merchants as off-limits. The tag constant is the exact string `"Retail Sync"`.
- **Reversibility:** merchant rename is fully reversible per row (restore the prior name); add `"rename_merchant"` to `REVERSIBLE_OPS`.
- **Backward compatibility:** new `Transaction` fields default to `""`; the existing 292 tests stay green.
- **Adapter parity:** the new write raises in CSV mode like every other write; the new read returns `[]` in CSV mode.

---

### Task 1: Transaction merchant identity plumbing (model + query + parsing)

**Files:**
- Modify: `financial_mcp/monarch/models.py` (Transaction)
- Modify: `financial_mcp/monarch/queries.py` (GET_TRANSACTIONS merchant block; add UPDATE_TRANSACTION_MERCHANT + GET_TRANSACTION_RULES)
- Modify: `financial_mcp/monarch/community_adapter.py` (`_to_transaction`)
- Test: `tests/test_community_adapter.py` (or the existing adapter test module)

**Interfaces:**
- Produces: `Transaction.merchant_id: str`, `Transaction.merchant_logo: str` (both default `""`); `queries.UPDATE_TRANSACTION_MERCHANT`, `queries.GET_TRANSACTION_RULES`.

- [ ] **Step 1: Write the failing test** — merchant id + logo populate from the GraphQL row.

Add to the adapter test module:

```python
def test_to_transaction_populates_merchant_id_and_logo():
    from financial_mcp.monarch.community_adapter import _to_transaction
    d = {
        "id": "t1", "date": "2026-08-01", "amount": -12.5,
        "merchant": {"id": "m1", "name": "Amazon", "logoUrl": "http://x/y.png"},
        "category": {"name": "Shopping"}, "account": {"displayName": "Card"},
        "plaidName": "AMZN Mktp US*2X4", "notes": "", "tags": [],
    }
    t = _to_transaction(d)
    assert t.merchant_id == "m1"
    assert t.merchant_logo == "http://x/y.png"

def test_to_transaction_merchant_id_and_logo_default_empty():
    from financial_mcp.monarch.community_adapter import _to_transaction
    d = {"id": "t2", "date": "2026-08-01", "amount": -1,
         "merchant": {"name": "Corner Store"}, "category": {"name": "Food"},
         "account": {"displayName": "Card"}, "plaidName": "", "notes": "", "tags": []}
    t = _to_transaction(d)
    assert t.merchant_id == ""
    assert t.merchant_logo == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_community_adapter.py -k merchant_id -v`
Expected: FAIL (`Transaction` has no field `merchant_id`).

- [ ] **Step 3: Add the model fields**

In `models.py`, inside `class Transaction`, after `owner: str`:

```python
    merchant_id: str = ""
    merchant_logo: str = ""
```

- [ ] **Step 4: Populate them in `_to_transaction`**

In `community_adapter.py::_to_transaction`, replace the merchant line and the `return Transaction(...)` so it reads the merchant dict once and passes the new fields:

```python
def _to_transaction(d: dict) -> Transaction:
    date = datetime.date.fromisoformat(d["date"])
    m = d.get("merchant") or {}
    merchant = m.get("name", "")
    amount = Decimal(str(d["amount"]))
    original = d.get("plaidName") or ""
    tid = d.get("id") or Transaction.synth_id(date, merchant, amount, original)
    return Transaction(
        id=str(tid),
        date=date,
        merchant=merchant,
        category=(d.get("category") or {}).get("name", ""),
        account=(d.get("account") or {}).get("displayName", ""),
        original_statement=original,
        notes=d.get("notes") or "",
        amount=amount,
        tags=[t["name"] for t in (d.get("tags") or [])],
        owner=d.get("owner") or "",
        merchant_id=m.get("id") or "",
        merchant_logo=m.get("logoUrl") or "",
    )
```

- [ ] **Step 5: Add `logoUrl` to the GET_TRANSACTIONS merchant selection**

In `queries.py`, in the `merchant { ... }` block of `GET_TRANSACTIONS` (currently `name`, `id`, `transactionsCount`, `__typename`), add `logoUrl`:

```graphql
    merchant {
      name
      id
      transactionsCount
      logoUrl
      __typename
    }
```

- [ ] **Step 6: Add the new mutation + rules query constants**

Append to `queries.py`:

```python
UPDATE_TRANSACTION_MERCHANT = """
mutation Web_TransactionDrawerUpdateTransaction($input: UpdateTransactionMutationInput!) {
  updateTransaction(input: $input) {
    transaction {
      id
      merchant { id name __typename }
      __typename
    }
    __typename
  }
}
"""

GET_TRANSACTION_RULES = """
  query GetTransactionRules {
    transactionRules {
      id
      merchantCriteria { operator value __typename }
      setMerchantAction { id name __typename }
      setCategoryAction { id name __typename }
      __typename
    }
  }
"""
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_community_adapter.py -k merchant -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add financial_mcp/monarch/models.py financial_mcp/monarch/queries.py financial_mcp/monarch/community_adapter.py tests/test_community_adapter.py
git commit -m "feat: merchant id + logo on Transaction; merchant-rename mutation + rules query"
```

---

### Task 2: Adapter merchant-rename write + rules read

**Files:**
- Modify: `financial_mcp/monarch/adapter.py` (ABC)
- Modify: `financial_mcp/monarch/community_adapter.py`
- Modify: `financial_mcp/monarch/csv_adapter.py`
- Test: `tests/test_merchant_adapter.py` (new file, matching the per-feature convention of `test_recategorize_adapter.py` / `test_category_adapter.py`)

**Interfaces:**
- Consumes: `queries.UPDATE_TRANSACTION_MERCHANT`, `queries.GET_TRANSACTION_RULES` (Task 1).
- Produces: `MonarchAdapter.rename_transaction_merchant(transaction_id: str, merchant_name: str) -> str | None` (resulting merchant id on success, `None` on failure); `MonarchAdapter.get_transaction_rules() -> list[dict]` where each rule is `{"id", "operator", "value", "set_merchant", "set_category"}`.

- [ ] **Step 1: Write the failing tests**

Add to the adapter test module (mirror the existing `recategorize_transaction` test that fakes `transport`):

```python
def _fake_adapter(monkeypatch, response):
    from financial_mcp.monarch.community_adapter import CommunityAdapter
    async def fake_transport(op, query, variables):
        return response
    return CommunityAdapter({"session_id": "s", "csrftoken": "c"}, transport=fake_transport)

def test_rename_merchant_returns_id_on_success(monkeypatch):
    ad = _fake_adapter(monkeypatch, {
        "updateTransaction": {"transaction": {"id": "t1", "merchant": {"id": "m9", "name": "Amazon"}}}
    })
    assert ad.rename_transaction_merchant("t1", "Amazon") == "m9"

def test_rename_merchant_returns_none_when_name_not_applied(monkeypatch):
    ad = _fake_adapter(monkeypatch, {
        "updateTransaction": {"transaction": {"id": "t1", "merchant": {"id": "m9", "name": "Other"}}}
    })
    assert ad.rename_transaction_merchant("t1", "Amazon") is None

def test_get_transaction_rules_flattens_shape(monkeypatch):
    ad = _fake_adapter(monkeypatch, {"transactionRules": [
        {"id": "r1", "merchantCriteria": [{"operator": "contains", "value": "amzn"}],
         "setMerchantAction": {"id": "m9", "name": "Amazon"}, "setCategoryAction": None},
        {"id": "r2", "merchantCriteria": [], "setMerchantAction": None,
         "setCategoryAction": {"id": "c1", "name": "Food"}},
    ]})
    rules = ad.get_transaction_rules()
    assert rules[0] == {"id": "r1", "operator": "contains", "value": "amzn",
                        "set_merchant": "Amazon", "set_category": None}
    assert rules[1] == {"id": "r2", "operator": None, "value": None,
                        "set_merchant": None, "set_category": "Food"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_merchant_adapter.py -k "rename_merchant or transaction_rules" -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Add abstract methods to the ABC**

In `adapter.py`, after the `delete_category` abstractmethod:

```python
    @abc.abstractmethod
    def rename_transaction_merchant(self, transaction_id: str, merchant_name: str) -> str | None: ...

    @abc.abstractmethod
    def get_transaction_rules(self) -> list[dict]: ...
```

- [ ] **Step 4: Implement in `community_adapter.py`**

Import the two new constants in the `from financial_mcp.monarch.queries import (...)` block, then add after `delete_category`:

```python
    def rename_transaction_merchant(self, transaction_id: str, merchant_name: str) -> str | None:
        data = self._run(
            self._transport(
                "Web_TransactionDrawerUpdateTransaction", UPDATE_TRANSACTION_MERCHANT,
                {"input": {"id": transaction_id, "name": merchant_name}},
            )
        )
        txn = (data.get("updateTransaction") or {}).get("transaction") or {}
        merch = txn.get("merchant") or {}
        return merch.get("id") if merch.get("name") == merchant_name else None

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
            })
        return out
```

- [ ] **Step 5: Implement CSV stubs**

In `csv_adapter.py`, after `delete_category`:

```python
    def rename_transaction_merchant(self, transaction_id: str, merchant_name: str) -> str | None:
        raise RuntimeError("writes are not available in CSV mode")

    def get_transaction_rules(self) -> list[dict]:
        return []
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_merchant_adapter.py -k "rename_merchant or transaction_rules" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add financial_mcp/monarch/adapter.py financial_mcp/monarch/community_adapter.py financial_mcp/monarch/csv_adapter.py tests/test_merchant_adapter.py
git commit -m "feat: adapter rename_transaction_merchant + get_transaction_rules"
```

---

### Task 3: DataService merchant-rename + rules passthrough

**Files:**
- Modify: `financial_mcp/data_service.py`
- Test: `tests/test_data_service.py`

**Interfaces:**
- Consumes: adapter `rename_transaction_merchant`, `get_transaction_rules` (Task 2).
- Produces: `DataService.rename_merchant(transaction_id: str, merchant_name: str) -> str | None` (invalidates the `transactions` cache); `DataService.transaction_rules() -> list[dict]` (uncached passthrough).

- [ ] **Step 1: Write the failing test** (mirror the existing `recategorize` service test with a fake adapter + spy cache):

```python
def test_rename_merchant_invalidates_transactions_cache():
    # fake adapter returns an id; spy that cache.invalidate("transactions") was called
    ...  # follow the existing test_recategorize_* pattern in this file
    assert svc.rename_merchant("t1", "Amazon") == "m9"
    assert "transactions" in spy_invalidated

def test_transaction_rules_passthrough():
    assert svc.transaction_rules() == [{"id": "r1", "operator": "contains", "value": "amzn",
                                        "set_merchant": "Amazon", "set_category": None}]
```

(Use the same fake-adapter/cache scaffolding already present in `tests/test_data_service.py` for `recategorize`; do not invent a new harness.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_data_service.py -k "rename_merchant or transaction_rules" -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement the methods**

In `data_service.py`, after `delete_category`:

```python
    def rename_merchant(self, transaction_id: str, merchant_name: str) -> str | None:
        result = self._adapter.rename_transaction_merchant(transaction_id, merchant_name)
        # The merchant name rides on cached transactions — force a re-pull next read.
        self._cache.invalidate("transactions")
        return result

    def transaction_rules(self) -> list[dict]:
        return self._adapter.get_transaction_rules()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_data_service.py -k "rename_merchant or transaction_rules" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/data_service.py tests/test_data_service.py
git commit -m "feat: DataService.rename_merchant + transaction_rules"
```

---

### Task 4: Op-log recognizes the rename_merchant op

**Files:**
- Modify: `financial_mcp/cache/undo_journal.py`
- Test: `tests/test_undo_journal.py`

**Interfaces:**
- Produces: `"rename_merchant"` recognized by `REVERSIBLE_OPS`, `last_undoable_batch`, `undoable_rows`, `pending_dependents`; `history()` renders a merchant summary line.

- [ ] **Step 1: Write the failing tests**

```python
def test_rename_merchant_is_reversible(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "c.db"))
    j.record("b1", "rename_merchant", "t1", "AMZN Mktp US*2X4", "Amazon",
             "AMZN Mktp US*2X4", "Amazon")
    j.mark_succeeded("b1", "t1")
    assert j.last_undoable_batch() == "b1"
    rows = j.undoable_rows("b1")
    assert rows[0]["op_type"] == "rename_merchant"
    assert rows[0]["old_value"] == "AMZN Mktp US*2X4"

def test_history_renders_merchant_summary(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal
    j = UndoJournal(str(tmp_path / "c.db"))
    j.record("b1", "rename_merchant", "t1", "AMZN*1", "Amazon", "AMZN*1", "Amazon")
    j.mark_succeeded("b1", "t1")
    h = j.history()
    assert "(merchant)" in h[0]["summary"]
    assert "Amazon" in h[0]["summary"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_undo_journal.py -k "rename_merchant or merchant_summary" -v`
Expected: FAIL (op not reversible; summary has no merchant branch).

- [ ] **Step 3: Add the op to REVERSIBLE_OPS**

In `undo_journal.py`:

```python
REVERSIBLE_OPS = ("recategorize", "rename_category", "delete_category", "rename_merchant")
```

- [ ] **Step 4: Add the history summary branch**

In `history()`, extend the summary `if/elif` chain (before the generic `else`):

```python
            elif op_type == "rename_merchant":
                summary = f"{old_n}→{new_n} (merchant) ×{n}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_undo_journal.py -k "rename_merchant or merchant_summary" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/cache/undo_journal.py tests/test_undo_journal.py
git commit -m "feat: op-log recognizes rename_merchant (reversible + history)"
```

---

### Task 5: writes.py merchant helpers (token, inventory, suggestions)

**Files:**
- Modify: `financial_mcp/writes.py`
- Test: `tests/test_writes.py` (or a new `tests/test_merchant_cleanup.py`)

**Interfaces:**
- Consumes: `Transaction` (with `merchant_id`, `merchant_logo`, `tags`).
- Produces:
  - `RETAIL_SYNC_TAG = "Retail Sync"`
  - `compute_merchant_token(transaction_ids: list[str], target_merchant: str) -> str`
  - `build_merchant_inventory(transactions: list[Transaction]) -> list[dict]` — rows `{"id","name","logo": bool,"txn_count": int,"total_amount": str,"retail_sync": bool,"sample_ids": list[str]}`
  - `suggest_consolidations(inventory: list[dict], rules: list[dict]) -> list[dict]` — rows `{"merchant","txn_count","suggested_target": str|None,"already_ruled": bool}`

- [ ] **Step 1: Write the failing tests**

```python
def test_compute_merchant_token_stable_and_order_independent():
    from financial_mcp.writes import compute_merchant_token
    a = compute_merchant_token(["t2", "t1"], "Amazon")
    b = compute_merchant_token(["t1", "t2"], "Amazon")
    assert a == b and len(a) == 16
    assert compute_merchant_token(["t1", "t2"], "Target") != a

def _txn(**kw):
    from decimal import Decimal
    import datetime
    base = dict(id="t", date=datetime.date(2026, 8, 1), merchant="M", category="C",
                account="A", original_statement="", notes="", amount=Decimal("-1"),
                tags=[], owner="", merchant_id="", merchant_logo="")
    base.update(kw)
    from financial_mcp.monarch.models import Transaction
    return Transaction(**base)

def test_build_merchant_inventory_groups_and_flags():
    from financial_mcp.writes import build_merchant_inventory
    txns = [
        _txn(id="t1", merchant="Amazon", merchant_id="m1", merchant_logo="u"),
        _txn(id="t2", merchant="Amazon", merchant_id="m1", merchant_logo="u"),
        _txn(id="t3", merchant="AMZN Mktp US*2X4", merchant_id="m2", merchant_logo=""),
        _txn(id="t4", merchant="Costco", merchant_id="m3", merchant_logo="u",
             tags=["Retail Sync"]),
    ]
    inv = {r["id"]: r for r in build_merchant_inventory(txns)}
    assert inv["m1"]["txn_count"] == 2 and inv["m1"]["logo"] is True
    assert inv["m2"]["logo"] is False
    assert inv["m3"]["retail_sync"] is True

def test_suggest_consolidations_matches_uncurated_to_curated():
    from financial_mcp.writes import build_merchant_inventory, suggest_consolidations
    txns = [
        _txn(id="t1", merchant="Amazon", merchant_id="m1", merchant_logo="u"),
        _txn(id="t3", merchant="AMZN Mktp US*2X4", merchant_id="m2", merchant_logo=""),
        _txn(id="t5", merchant="Amazon.com*RT5", merchant_id="m4", merchant_logo=""),
    ]
    inv = build_merchant_inventory(txns)
    sugg = {s["merchant"]: s for s in suggest_consolidations(inv, rules=[])}
    # Both uncurated Amazon variants should target the curated "Amazon".
    assert sugg["Amazon.com*RT5"]["suggested_target"] == "Amazon"

def test_suggest_consolidations_marks_already_ruled_and_excludes_retail_sync():
    from financial_mcp.writes import build_merchant_inventory, suggest_consolidations
    txns = [
        _txn(id="t1", merchant="Amazon", merchant_id="m1", merchant_logo="u"),
        _txn(id="t3", merchant="AMZN Mktp US*2X4", merchant_id="m2", merchant_logo=""),
        _txn(id="t4", merchant="Junk Co", merchant_id="m5", merchant_logo="",
             tags=["Retail Sync"]),
    ]
    inv = build_merchant_inventory(txns)
    rules = [{"id": "r1", "operator": "contains", "value": "amzn mktp",
              "set_merchant": "Amazon", "set_category": None}]
    sugg = {s["merchant"]: s for s in suggest_consolidations(inv, rules)}
    assert sugg["AMZN Mktp US*2X4"]["already_ruled"] is True
    assert "Junk Co" not in sugg  # retail-sync-managed → excluded from suggestions
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_writes.py -k "merchant" -v`
Expected: FAIL (functions not defined).

- [ ] **Step 3: Implement the helpers**

Add to `writes.py` (add `import hashlib`, `import re` if not present; `Decimal` is already used):

```python
RETAIL_SYNC_TAG = "Retail Sync"


def compute_merchant_token(transaction_ids: list[str], target_merchant: str) -> str:
    key = "|".join(sorted(transaction_ids)) + "->" + target_merchant
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def build_merchant_inventory(transactions) -> list[dict]:
    rows: dict[str, dict] = {}
    for t in transactions:
        key = t.merchant_id or t.merchant
        r = rows.get(key)
        if r is None:
            r = {"id": t.merchant_id or "", "name": t.merchant, "logo": bool(t.merchant_logo),
                 "txn_count": 0, "_total": Decimal("0"), "retail_sync": False, "sample_ids": []}
            rows[key] = r
        r["txn_count"] += 1
        r["_total"] += t.amount
        if RETAIL_SYNC_TAG in (t.tags or []):
            r["retail_sync"] = True
        if len(r["sample_ids"]) < 5:
            r["sample_ids"].append(t.id)
    out = []
    for r in rows.values():
        r["total_amount"] = str(r.pop("_total"))
        out.append(r)
    out.sort(key=lambda r: r["txn_count"], reverse=True)
    return out


def _normalize_merchant(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[*#].*$", "", s)          # drop id suffixes after * or #
    s = re.sub(r"\d[\d/.\-]*", " ", s)     # drop numeric / date-ish tokens
    s = re.sub(r"[^a-z ]", " ", s)         # keep letters only
    return re.sub(r"\s+", " ", s).strip()


def suggest_consolidations(inventory: list[dict], rules: list[dict]) -> list[dict]:
    curated = [(r, _normalize_merchant(r["name"])) for r in inventory
               if r["logo"] and not r["retail_sync"]]
    rule_values = [(rl.get("value") or "").lower() for rl in rules if rl.get("value")]
    suggestions = []
    for r in inventory:
        if r["logo"] or r["retail_sync"]:
            continue  # curated or extension-managed → not a suggestion
        stem = _normalize_merchant(r["name"])
        target = None
        best_len = 0
        for crow, cstem in curated:
            if not cstem or cstem == stem:
                continue
            if (cstem in stem or stem.startswith(cstem)) and len(cstem) > best_len:
                target, best_len = crow["name"], len(cstem)
        already = any(v and v in r["name"].lower() for v in rule_values)
        suggestions.append({"merchant": r["name"], "txn_count": r["txn_count"],
                            "suggested_target": target, "already_ruled": already})
    return suggestions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_writes.py -k "merchant" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/writes.py tests/test_writes.py
git commit -m "feat: merchant token + inventory + consolidation suggestions helpers"
```

---

### Task 6: `rename_merchant_matching` tool + undo_last branch

**Files:**
- Modify: `financial_mcp/mcp_server.py`
- Test: `tests/test_mcp_tools.py` (the module holding the recategorize_matching / undo_last tool tests)

**Interfaces:**
- Consumes: `svc.rename_merchant`, `writes.match_transactions`, `writes.compute_merchant_token`, `writes.RETAIL_SYNC_TAG`, `journal`, `batch_cap`, `write_mode`, `_guard`, `_writes_enabled`.
- Produces: MCP tool `rename_merchant_matching(...) -> dict`; `undo_last` reverses `rename_merchant` rows.

- [ ] **Step 1: Write the failing tests** (mirror the existing `recategorize_matching` tool tests; the module already builds a server with a fake `svc` + real `journal` on `tmp_path`):

```python
def test_rename_merchant_matching_off_mode_refuses(server_off):
    out = server_off.rename_merchant_matching("Amazon", merchant="AMZN")
    assert "error" in out

def test_rename_merchant_matching_dry_run_then_confirm(server_safe):
    pv = server_safe.rename_merchant_matching("Amazon", merchant="AMZN")
    assert pv["dry_run"] is True and pv["would_change"] >= 1
    out = server_safe.rename_merchant_matching("Amazon", merchant="AMZN",
                                               confirm_token=pv["confirm_token"])
    assert out["changed"] == pv["would_change"] and out["to"] == "Amazon"

def test_rename_merchant_matching_excludes_retail_sync_by_default(server_safe_with_retail_sync):
    pv = server_safe_with_retail_sync.rename_merchant_matching("Amazon", merchant="AMZN")
    assert pv["skipped_retail_sync"] >= 1
    # opt-in includes them
    pv2 = server_safe_with_retail_sync.rename_merchant_matching(
        "Amazon", merchant="AMZN", include_retail_sync=True)
    assert pv2["would_change"] > pv["would_change"]

def test_rename_merchant_matching_blank_target_refuses(server_safe):
    assert "error" in server_safe.rename_merchant_matching("  ", merchant="AMZN")

def test_rename_merchant_matching_over_cap_refuses(server_safe_small_cap):
    out = server_safe_small_cap.rename_merchant_matching("Amazon", merchant="AMZN")
    assert "error" in out and "cap" in out["error"]

def test_undo_last_reverses_rename_merchant(server_full):
    server_full.rename_merchant_matching("Amazon", merchant="AMZN")
    undo = server_full.undo_last()
    assert undo["reversed"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k "rename_merchant" -v`
Expected: FAIL (tool not defined).

- [ ] **Step 3: Implement `rename_merchant_matching`**

In `mcp_server.py`, directly after the `recategorize_matching` tool, add (structurally a clone; note the target is a free-text name, and the Retail-Sync partition):

```python
    @server.tool()
    def rename_merchant_matching(
        to_merchant: str,
        merchant: str | None = None,
        category: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        start: str | None = None,
        end: str | None = None,
        include_retail_sync: bool = False,
        confirm_token: str | None = None,
    ) -> dict:
        """Bulk-rename the merchant on every transaction matching a filter.

        Filter mirrors search_transactions (merchant/category substrings — the
        `merchant` filter IS the "contains" match — plus amount range and an
        optional YYYY-MM-DD date window). In 'safe' mode this is two-phase:
        call without confirm_token to preview (nothing mutates) and get a
        confirm_token, then call again with it to execute. In 'full' mode it
        executes directly. Transactions tagged "Retail Sync" are excluded unless
        include_retail_sync=True (renaming them requires updating the Retail Sync
        extension's Retailers setting). Over the batch cap it refuses (no
        truncation). Reversible via undo_last.
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}
        if not to_merchant or not to_merchant.strip():
            return {"error": "to_merchant must be a non-empty name"}

        s = e = None
        if start is not None:
            try:
                s = datetime.date.fromisoformat(start)
            except ValueError:
                return {"error": "start must be YYYY-MM-DD"}
        if end is not None:
            try:
                e = datetime.date.fromisoformat(end)
            except ValueError:
                return {"error": "end must be YYYY-MM-DD"}
        if s and e and s > e:
            return {"error": "start must not be after end"}

        def _run():
            matched = writes.match_transactions(
                svc.transactions(), merchant=merchant, category=category,
                min_amount=None if min_amount is None else Decimal(str(min_amount)),
                max_amount=None if max_amount is None else Decimal(str(max_amount)),
                start=s, end=e,
            )
            skipped = 0
            if not include_retail_sync:
                kept = [t for t in matched if writes.RETAIL_SYNC_TAG not in (t.tags or [])]
                skipped = len(matched) - len(kept)
                matched = kept
            if not matched:
                return {"would_change": 0, "skipped_retail_sync": skipped,
                        "message": "no transactions matched"}
            if len(matched) > batch_cap:
                return {"error": f"{len(matched)} transactions match, over the batch cap "
                                 f"of {batch_cap}. Narrow the filter or raise batch_cap."}
            token = writes.compute_merchant_token([t.id for t in matched], to_merchant)

            def _preview():
                sample = [{"transaction_id": t.id, "date": t.date.isoformat(),
                           "current_merchant": t.merchant, "amount": str(t.amount),
                           "to_merchant": to_merchant} for t in matched[:20]]
                return {"dry_run": True, "would_change": len(matched),
                        "skipped_retail_sync": skipped, "to_merchant": to_merchant,
                        "sample": sample, "confirm_token": token, "mode": write_mode}

            if write_mode == "safe":
                if confirm_token is None or confirm_token != token:
                    return _preview()
            batch_id = uuid.uuid4().hex
            changed = 0
            failures = []
            resulting_merchant_id = None
            for t in matched:
                journal.record(batch_id, "rename_merchant", t.id, t.merchant,
                               to_merchant, t.merchant, to_merchant)
                rid = svc.rename_merchant(t.id, to_merchant)
                if rid:
                    journal.mark_succeeded(batch_id, t.id)
                    changed += 1
                    resulting_merchant_id = resulting_merchant_id or rid
                else:
                    failures.append({"transaction_id": t.id,
                                     "reason": "Monarch did not apply the merchant rename"})
            return {"changed": changed, "failed": len(failures), "failures": failures,
                    "skipped_retail_sync": skipped, "batch_id": batch_id,
                    "to": to_merchant, "resulting_merchant_id": resulting_merchant_id,
                    "undo_hint": "call undo_last to reverse"}

        return _guard(_run)
```

- [ ] **Step 4: Extend `undo_last` to reverse rename_merchant**

In `undo_last`'s `_run`, add a branch to the dispatch `if/elif` (after the `rename_category` branch, before the `else`):

```python
                elif r["op_type"] == "rename_merchant":
                    ok = bool(svc.rename_merchant(r["target_id"], r["old_value"]))
```

and add a matching failure branch in the failure `if/elif` chain (before its `else`):

```python
                    elif r["op_type"] == "rename_merchant":
                        failures.append({"transaction_id": r["target_id"],
                                         "reason": "Monarch did not apply the merchant rename"})
```

Also update the `undo_last` docstring first line to mention merchant renames.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k "rename_merchant" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: rename_merchant_matching tool (gated, retail-sync-aware) + undo_last branch"
```

---

### Task 7: `merchant_cleanup_suggestions` read-only tool

**Files:**
- Modify: `financial_mcp/mcp_server.py`
- Test: `tests/test_mcp_tools.py` (or the module holding read-tool tests)

**Interfaces:**
- Consumes: `svc.transactions`, `svc.transaction_rules`, `writes.build_merchant_inventory`, `writes.suggest_consolidations`.
- Produces: MCP tool `merchant_cleanup_suggestions(limit: int = 50, include_curated: bool = False) -> dict`.

- [ ] **Step 1: Write the failing test:**

```python
def test_merchant_cleanup_suggestions_surfaces_uncurated(server_read):
    # fake svc.transactions() returns a curated "Amazon" + uncurated "AMZN Mktp US*2X4"
    out = server_read.merchant_cleanup_suggestions()
    assert out["uncurated_count"] >= 1
    names = [s["merchant"] for s in out["suggestions"]]
    assert "AMZN Mktp US*2X4" in names

def test_merchant_cleanup_suggestions_flags_retail_sync(server_read_with_retail_sync):
    out = server_read_with_retail_sync.merchant_cleanup_suggestions()
    assert out["retail_sync_managed"]  # non-empty list of names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k "merchant_cleanup" -v`
Expected: FAIL (tool not defined).

- [ ] **Step 3: Implement the tool**

In `mcp_server.py`, near the other read tools (e.g. after `merchant_summary`):

```python
    @server.tool()
    def merchant_cleanup_suggestions(limit: int = 50, include_curated: bool = False) -> dict:
        """Advisory (read-only): find merchants worth cleaning up.

        Builds a merchant inventory from your transactions, flags the ones with
        no logo (uncurated / auto-created by import), and — for each — suggests a
        likely canonical merchant to consolidate into, noting whether one of your
        existing transaction rules already covers it. Merchants managed by the
        Retail Sync extension are listed separately and NOT suggested for rename
        (leave those to the extension). Nothing is mutated.
        """
        inv = writes.build_merchant_inventory(svc.transactions())
        rules = svc.transaction_rules()
        suggestions = writes.suggest_consolidations(inv, rules)
        retail = [r["name"] for r in inv if r["retail_sync"]]
        result = {
            "uncurated_count": sum(1 for r in inv if not r["logo"] and not r["retail_sync"]),
            "suggestions": suggestions[:limit],
            "retail_sync_managed": retail,
            "note": "read-only advisory; use rename_merchant_matching to act on a suggestion",
        }
        if include_curated:
            result["curated"] = [r for r in inv if r["logo"]][:limit]
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k "merchant_cleanup" -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all green (292 prior + the new tests).

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/mcp_server.py tests/test_mcp_tools.py
git commit -m "feat: merchant_cleanup_suggestions read-only advisory tool"
```

---

## Self-Review

- **Spec coverage:** §4.1 data plumbing → Task 1; §4.2 adapter → Task 2; §4.3 helpers → Task 5; §4.4 op-log → Task 4; §4.5 tools → Tasks 6 (rename + undo_last) & 7 (advisory), with DataService in Task 3; §3 Retail Sync default-exclude → Task 6; §5 reversibility → Tasks 4 & 6; §6 refusals → Task 6. All sections covered.
- **Type consistency:** `rename_transaction_merchant` / `svc.rename_merchant` return `str | None` everywhere (Tasks 2, 3, 6, and the `bool(...)` wrap in undo_last); `get_transaction_rules` / `svc.transaction_rules` return `list[dict]` with the fixed key set (Tasks 2, 3, 5, 7); inventory/suggestion dict shapes match between Task 5 (producer) and Task 7 (consumer).
- **Placeholder scan:** Task 3's test bodies reference "follow the existing pattern" rather than inlining the fake-adapter/cache scaffold — this is deliberate (that harness already exists in the repo and must be reused, not reinvented); all production code is fully specified.
- **Retail Sync tag:** exact literal `"Retail Sync"` centralized as `writes.RETAIL_SYNC_TAG` and reused (Tasks 5, 6).

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
**2. Inline Execution** — execute in this session with checkpoints.

Which approach?
