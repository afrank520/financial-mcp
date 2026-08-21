from __future__ import annotations

import datetime
import hashlib
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
