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


def test_update_rule_input_is_create_shape_plus_id_and_preserves_operator():
    from financial_mcp.writes import _update_rule_input
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
    from financial_mcp.writes import _update_rule_input
    inp = _update_rule_input("rule9", "contains", "aplpay ubs", None, "UBS Arena")
    assert inp["setMerchantAction"] == "UBS Arena"
    assert inp["setCategoryAction"] is None


def test_update_token_stable_sensitive_and_distinct_from_create_tokens():
    from financial_mcp.writes import compute_rule_update_token, compute_rule_token, compute_category_rule_token
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
    from financial_mcp.writes import is_simple_rule, rule_complexity
    cat_only = {"value": "ubs", "set_category_id": "cat7", "set_merchant": None}
    merch_only = {"value": "amzn", "set_category_id": None, "set_merchant": "Amazon"}
    both = {"value": "ubs", "set_category_id": "cat7", "set_merchant": "UBS Arena"}
    for r in (cat_only, merch_only, both):
        assert is_simple_rule(r) is True
        assert rule_complexity(r) == []


def test_rule_complexity_flags_each_complex_component():
    from financial_mcp.writes import rule_complexity
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
    assert "matches on a specific merchant" in rule_complexity({**base, "has_merchant_criteria": True})
    assert "splits transactions" in rule_complexity({**base, "has_split_action": True})


def test_rule_complexity_flags_missing_merchant_criterion_and_missing_action():
    from financial_mcp.writes import rule_complexity
    assert "no merchant-name criterion" in rule_complexity(
        {"value": None, "set_category_id": "cat7"})
    assert "no set-category or set-merchant action" in rule_complexity(
        {"value": "ubs", "set_category_id": None, "set_merchant": None})


def test_normalize_budget_amount_quantizes_to_cents():
    assert writes.normalize_budget_amount(250) == Decimal("250.00")
    assert writes.normalize_budget_amount(19.9) == Decimal("19.90")
    assert writes.normalize_budget_amount("12.005") == Decimal("12.00")  # rounds to cents


def test_normalize_budget_amount_rejects_negative():
    with pytest.raises(writes.WriteError) as exc:
        writes.normalize_budget_amount(-5)
    assert "non-negative" in str(exc.value)


def test_normalize_budget_amount_rejects_non_numeric():
    for bad in ("abc", None, "", object()):
        with pytest.raises(writes.WriteError):
            writes.normalize_budget_amount(bad)


def test_normalize_budget_amount_rejects_non_finite():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(writes.WriteError):
            writes.normalize_budget_amount(bad)


def test_parse_budget_month_accepts_year_month():
    assert writes.parse_budget_month("2026-09") == datetime.date(2026, 9, 1)


def test_parse_budget_month_accepts_full_date_and_normalizes_to_first():
    assert writes.parse_budget_month("2026-09-17") == datetime.date(2026, 9, 1)


def test_parse_budget_month_rejects_garbage():
    with pytest.raises(writes.WriteError):
        writes.parse_budget_month("September")


def test_compute_budget_token_is_stable_and_scope_sensitive():
    a = writes.compute_budget_token("c1", "2026-09-01", Decimal("250.00"), False)
    b = writes.compute_budget_token("c1", "2026-09-01", Decimal("250.00"), False)
    assert a == b and len(a) == 16
    # flipping scope changes the token (anti-replay across scopes)
    assert writes.compute_budget_token("c1", "2026-09-01", Decimal("250.00"), True) != a
    # different amount changes the token
    assert writes.compute_budget_token("c1", "2026-09-01", Decimal("300.00"), False) != a
