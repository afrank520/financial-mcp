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
    # NOTE: get_transaction_rules() was enriched in the rule-update slice (Task 3,
    # 2026-08-26) to surface additional fields (order, category_ids, etc. — see
    # tests/test_merchant_rule_adapter.py::test_get_rules_parse_surfaces_enriched_fields
    # for the full contract). This test predates that change; updated to the enriched
    # shape rather than removed, since it still usefully covers the two-rule
    # (rename-rule vs category-rule) flattening case.
    # The merchant-NAME match is read from merchantNameCriteria (a list), the field
    # our create/update tools write to — NOT merchantCriteria (curated-merchant match).
    ad = _fake_adapter(monkeypatch, {"transactionRules": [
        {"id": "r1", "merchantNameCriteria": [{"operator": "contains", "value": "amzn"}],
         "setMerchantAction": {"id": "m9", "name": "Amazon"}, "setCategoryAction": None},
        {"id": "r2", "merchantNameCriteria": [], "setMerchantAction": None,
         "setCategoryAction": {"id": "c1", "name": "Food"}},
    ]})
    rules = ad.get_transaction_rules()
    enriched_defaults = {
        "order": None, "uses_original_statement": False, "category_ids": [], "account_ids": [],
        "has_amount_criteria": False, "has_original_statement_criteria": False,
        "review_status_action": None, "send_notification": False, "unassign_needs_review": False,
        "has_add_tags": False, "has_link_goal": False, "has_link_savings_goal": False,
        "has_merchant_criteria": False, "has_split_action": False,
    }
    assert rules[0] == {"id": "r1", "operator": "contains", "value": "amzn",
                        "set_merchant": "Amazon", "set_category": None, "set_category_id": None,
                        **enriched_defaults}
    assert rules[1] == {"id": "r2", "operator": None, "value": None,
                        "set_merchant": None, "set_category": "Food", "set_category_id": "c1",
                        **enriched_defaults}
