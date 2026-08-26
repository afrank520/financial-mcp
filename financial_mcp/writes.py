from __future__ import annotations

import datetime
import hashlib
import re
from decimal import Decimal

from financial_mcp.analytics.spending import search
from financial_mcp.monarch.models import Category, Transaction


class WriteError(Exception):
    """A write cannot proceed (fail-closed). str(exc) is the user-facing message."""


def resolve_category(name: str, categories: list[Category]) -> Category:
    """Exact case-insensitive name -> Category. Fail-closed: never creates, never fuzzy."""
    matches = [c for c in categories if c.name.lower() == name.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        valid = ", ".join(sorted(c.name for c in categories))
        raise WriteError(
            f"No category named {name!r}. Pick an existing category: {valid}"
        )
    groups = "; ".join(f"{c.name} (group: {c.group})" for c in matches)
    raise WriteError(
        f"Category name {name!r} is ambiguous — {len(matches)} match: {groups}. "
        "Disambiguate before writing."
    )


def compute_confirm_token(transaction_ids: list[str], target_category_id: str) -> str:
    """Deterministic content hash of the exact change-set. Order-independent."""
    key = "|".join(sorted(transaction_ids)) + "->" + target_category_id
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def match_transactions(
    txns: list[Transaction],
    *,
    merchant: str | None = None,
    category: str | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    start: datetime.date | None = None,
    end: datetime.date | None = None,
) -> list[Transaction]:
    """Bulk selection: optional date window then the proven search() filter."""
    result = txns
    if start is not None:
        result = [t for t in result if t.date >= start]
    if end is not None:
        result = [t for t in result if t.date <= end]
    return search(
        result, merchant=merchant, category=category,
        min_amount=min_amount, max_amount=max_amount,
    )


DEFAULT_CATEGORY_ICON = "🏷️"


def resolve_group(name: str, categories: list[Category]) -> tuple[str, str]:
    """Exact case-insensitive group name -> (group_id, group_name). Fail-closed
    on miss OR ambiguity (same case-insensitive name mapping to more than one
    distinct group_id). Multiple categories sharing the SAME group_id is the
    normal case and still resolves fine."""
    by_name: dict[str, dict[str, str]] = {}
    for c in categories:
        if c.group and c.group_id:
            by_name.setdefault(c.group.lower(), {})[c.group_id] = c.group
    matches = by_name.get(name.lower())
    if not matches:
        valid = ", ".join(sorted({c.group for c in categories if c.group}))
        raise WriteError(
            f"No category group named {name!r}. Pick an existing group: {valid}"
        )
    if len(matches) > 1:
        ids = "; ".join(f"{gname!r} (group_id: {gid})" for gid, gname in matches.items())
        raise WriteError(
            f"Category group name {name!r} is ambiguous — it maps to {len(matches)} "
            f"distinct group ids: {ids}. Disambiguate before writing."
        )
    ((group_id, group_name),) = matches.items()
    return group_id, group_name


def check_name_available(name: str, categories: list[Category]) -> None:
    """Fail-closed: raise if a category by this name already exists (case-insensitive)."""
    if any(c.name.lower() == name.lower() for c in categories):
        raise WriteError(
            f"A category named {name!r} already exists. Pick a different name."
        )


def count_in_category(name: str, transactions: list[Transaction]) -> int:
    """Case-insensitive count of transactions currently in a category (by name)."""
    return sum(1 for t in transactions if t.category.lower() == name.lower())


def compute_delete_token(category_id: str, transaction_count: int) -> str:
    """Deterministic content hash of a delete op. Binds the category id and its
    transaction count so a category that stopped being empty re-previews."""
    key = f"{category_id}:delete:{transaction_count}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


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
