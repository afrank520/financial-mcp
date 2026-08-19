import datetime
from decimal import Decimal

import pytest

from financial_mcp import writes
from financial_mcp.monarch.models import Category, Transaction


def _cat(id, name, group=None):
    return Category(id=id, name=name, group=group)


def _txn(id, merchant, category, amount, date):
    return Transaction(
        id=id, date=date, merchant=merchant, category=category, account="Checking",
        original_statement=merchant, notes="", amount=Decimal(amount), tags=[], owner="me",
    )


CATS = [_cat("1", "Shopping", "Discretionary"), _cat("2", "Groceries", "Food")]


def test_resolve_category_exact_case_insensitive():
    assert writes.resolve_category("shopping", CATS).id == "1"


def test_resolve_category_no_match_refuses_with_valid_list():
    with pytest.raises(writes.WriteError) as exc:
        writes.resolve_category("Restaurants", CATS)
    msg = str(exc.value)
    assert "Restaurants" in msg and "Shopping" in msg and "Groceries" in msg


def test_resolve_category_ambiguous_refuses_with_groups():
    cats = [_cat("1", "Auto", "Transport"), _cat("2", "Auto", "Insurance")]
    with pytest.raises(writes.WriteError) as exc:
        writes.resolve_category("Auto", cats)
    m = str(exc.value)
    assert "Transport" in m and "Insurance" in m


def test_confirm_token_is_deterministic_and_order_independent():
    a = writes.compute_confirm_token(["t2", "t1"], "cat9")
    b = writes.compute_confirm_token(["t1", "t2"], "cat9")
    assert a == b


def test_confirm_token_changes_with_set_or_target():
    base = writes.compute_confirm_token(["t1", "t2"], "cat9")
    assert base != writes.compute_confirm_token(["t1", "t2", "t3"], "cat9")
    assert base != writes.compute_confirm_token(["t1", "t2"], "cat8")


def test_match_transactions_reuses_search_and_date_window():
    txns = [
        _txn("a", "Amazon", "Uncategorized", "-10", datetime.date(2026, 6, 3)),
        _txn("b", "Amazon", "Uncategorized", "-99", datetime.date(2026, 6, 4)),
        _txn("c", "Whole Foods", "Groceries", "-20", datetime.date(2026, 6, 5)),
        _txn("d", "Amazon", "Uncategorized", "-10", datetime.date(2026, 7, 1)),
    ]
    out = writes.match_transactions(
        txns, merchant="amazon", max_amount=Decimal("-5"),
        start=datetime.date(2026, 6, 1), end=datetime.date(2026, 6, 30),
    )
    assert {t.id for t in out} == {"a", "b"}


def _cats():
    return [
        Category(id="c1", name="Groceries", group="Food & Dining", group_id="g1"),
        Category(id="c2", name="Restaurants", group="Food & Dining", group_id="g1"),
        Category(id="c3", name="Gas", group="Auto & Transport", group_id="g2"),
    ]


def test_resolve_group_hit_is_case_insensitive():
    assert writes.resolve_group("food & dining", _cats()) == ("g1", "Food & Dining")


def test_resolve_group_miss_lists_valid_groups():
    with pytest.raises(writes.WriteError) as exc:
        writes.resolve_group("Nope", _cats())
    assert "Auto & Transport" in str(exc.value)
    assert "Food & Dining" in str(exc.value)


def test_check_name_available_raises_when_taken():
    with pytest.raises(writes.WriteError):
        writes.check_name_available("groceries", _cats())


def test_check_name_available_ok_when_free():
    writes.check_name_available("Vacations", _cats())  # no raise
