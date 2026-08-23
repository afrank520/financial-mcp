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


def test_resolve_group_same_group_multiple_categories_still_resolves():
    # Guard against over-triggering: two categories in the SAME group (same
    # group_id) is the normal case, not ambiguity. _cats() already has
    # Groceries + Restaurants both under "Food & Dining" / "g1".
    assert writes.resolve_group("Food & Dining", _cats()) == ("g1", "Food & Dining")


def test_resolve_group_ambiguous_same_name_different_ids_refuses():
    cats = [
        Category(id="c1", name="Groceries", group="Travel", group_id="g10"),
        Category(id="c2", name="Flights", group="travel", group_id="g20"),
    ]
    with pytest.raises(writes.WriteError) as exc:
        writes.resolve_group("Travel", cats)
    msg = str(exc.value)
    assert "Travel" in msg or "travel" in msg
    assert "g10" in msg and "g20" in msg


def test_check_name_available_raises_when_taken():
    with pytest.raises(writes.WriteError):
        writes.check_name_available("groceries", _cats())


def test_check_name_available_ok_when_free():
    writes.check_name_available("Vacations", _cats())  # no raise


def test_count_in_category_counts_case_insensitively():
    from financial_mcp.writes import count_in_category
    txns = [
        _txn("a", "Amazon", "Groceries", "-10", datetime.date(2026, 6, 3)),
        _txn("b", "Walmart", "groceries", "-20", datetime.date(2026, 6, 4)),
        _txn("c", "Olive Garden", "Restaurants", "-30", datetime.date(2026, 6, 5)),
    ]
    assert count_in_category("GROCERIES", txns) == 2
    assert count_in_category("Restaurants", txns) == 1
    assert count_in_category("Travel", txns) == 0


def test_compute_delete_token_stable_and_count_sensitive():
    from financial_mcp.writes import compute_delete_token
    a = compute_delete_token("c1", 0)
    assert a == compute_delete_token("c1", 0)      # stable
    assert a != compute_delete_token("c1", 1)      # changes when count changes
    assert a != compute_delete_token("c2", 0)      # changes when id changes
    assert len(a) == 16


def test_compute_merchant_token_stable_and_order_independent():
    from financial_mcp.writes import compute_merchant_token
    a = compute_merchant_token(["t2", "t1"], "Amazon")
    b = compute_merchant_token(["t1", "t2"], "Amazon")
    assert a == b and len(a) == 16
    assert compute_merchant_token(["t1", "t2"], "Target") != a


def _merchant_txn(**kw):
    base = dict(id="t", date=datetime.date(2026, 8, 1), merchant="M", category="C",
                account="A", original_statement="", notes="", amount=Decimal("-1"),
                tags=[], owner="", merchant_id="", merchant_logo="")
    base.update(kw)
    return Transaction(**base)


def test_build_merchant_inventory_groups_and_flags():
    from financial_mcp.writes import build_merchant_inventory
    txns = [
        _merchant_txn(id="t1", merchant="Amazon", merchant_id="m1", merchant_logo="u"),
        _merchant_txn(id="t2", merchant="Amazon", merchant_id="m1", merchant_logo="u"),
        _merchant_txn(id="t3", merchant="AMZN Mktp US*2X4", merchant_id="m2", merchant_logo=""),
        _merchant_txn(id="t4", merchant="Costco", merchant_id="m3", merchant_logo="u",
                      tags=["Retail Sync"]),
    ]
    inv = {r["id"]: r for r in build_merchant_inventory(txns)}
    assert inv["m1"]["txn_count"] == 2 and inv["m1"]["logo"] is True
    assert inv["m2"]["logo"] is False
    assert inv["m3"]["retail_sync"] is True


def test_suggest_consolidations_matches_uncurated_to_curated():
    from financial_mcp.writes import build_merchant_inventory, suggest_consolidations
    txns = [
        _merchant_txn(id="t1", merchant="Amazon", merchant_id="m1", merchant_logo="u"),
        _merchant_txn(id="t3", merchant="AMZN Mktp US*2X4", merchant_id="m2", merchant_logo=""),
        _merchant_txn(id="t5", merchant="Amazon.com*RT5", merchant_id="m4", merchant_logo=""),
    ]
    inv = build_merchant_inventory(txns)
    sugg = {s["merchant"]: s for s in suggest_consolidations(inv, rules=[])}
    # Both uncurated Amazon variants should target the curated "Amazon".
    assert sugg["Amazon.com*RT5"]["suggested_target"] == "Amazon"


def test_suggest_consolidations_marks_already_ruled_and_excludes_retail_sync():
    from financial_mcp.writes import build_merchant_inventory, suggest_consolidations
    txns = [
        _merchant_txn(id="t1", merchant="Amazon", merchant_id="m1", merchant_logo="u"),
        _merchant_txn(id="t3", merchant="AMZN Mktp US*2X4", merchant_id="m2", merchant_logo=""),
        _merchant_txn(id="t4", merchant="Junk Co", merchant_id="m5", merchant_logo="",
                      tags=["Retail Sync"]),
    ]
    inv = build_merchant_inventory(txns)
    rules = [{"id": "r1", "operator": "contains", "value": "amzn mktp",
              "set_merchant": "Amazon", "set_category": None}]
    sugg = {s["merchant"]: s for s in suggest_consolidations(inv, rules)}
    assert sugg["AMZN Mktp US*2X4"]["already_ruled"] is True
    assert "Junk Co" not in sugg  # retail-sync-managed → excluded from suggestions
