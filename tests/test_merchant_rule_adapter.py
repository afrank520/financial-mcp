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


def _rule(rid):
    return {"id": rid, "merchantCriteria": [], "setMerchantAction": None, "setCategoryAction": None}


def test_delete_rule_true_when_gone_despite_deleted_false():
    # Monarch's deleteTransactionRule returns deleted:false even on a SUCCESSFUL
    # delete (false-negative on the V2 mutation), so we verify by absence: the
    # rule is gone from the re-read → True. Regression test for the live-caught
    # undo bug — trusting `deleted` reported every real delete as a failure.
    ad = _seq_adapter([
        {"deleteTransactionRule": {"deleted": False, "errors": None}},  # Monarch's false-negative
        {"transactionRules": [_rule("r1")]},                            # re-read: r2 is gone
    ])
    assert ad.delete_transaction_rule("r2") is True


def test_delete_rule_false_when_still_present():
    # Delete truly didn't take — the rule is still in the re-read → honest
    # failure (undo_last keeps the row retryable).
    ad = _seq_adapter([
        {"deleteTransactionRule": {"deleted": False, "errors": None}},
        {"transactionRules": [_rule("r1"), _rule("r2")]},               # re-read: r2 still there
    ])
    assert ad.delete_transaction_rule("r2") is False


def test_csv_create_and_delete_rule_raise():
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    import pytest

    ad = CsvAdapter("unused_txns.csv", "unused_balances.csv")
    with pytest.raises(RuntimeError):
        ad.create_transaction_rule("AMZN", "Amazon")
    with pytest.raises(RuntimeError):
        ad.delete_transaction_rule("r2")


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


def test_get_rules_parse_surfaces_enriched_fields():
    ad = _seq_adapter([
        {"transactionRules": [{
            "id": "r1", "order": 3,
            "merchantNameCriteria": [{"operator": "eq", "value": "ubs arena"}],
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
    assert r["has_merchant_criteria"] is False


def test_get_rules_parse_reads_name_criterion_and_flags_curated_merchant():
    # A rule that matches on the CURATED merchant (merchantCriteria) rather than the
    # merchant NAME: the name match is absent (value None) and has_merchant_criteria
    # is True so rule_complexity will fail-close it (full-replacement would wipe it).
    ad = _seq_adapter([
        {"transactionRules": [{
            "id": "r1",
            "merchantCriteria": [{"operator": "eq", "value": "m-123"}],
            "merchantNameCriteria": None,
            "setMerchantAction": None,
            "setCategoryAction": {"id": "c7", "name": "Entertainment"}}]},
    ])
    (r,) = ad.get_transaction_rules()
    assert r["value"] is None and r["operator"] is None
    assert r["has_merchant_criteria"] is True


def test_get_rules_parse_flags_split_action():
    # A rule that also SPLITS the transaction: the full-replacement update sends
    # splitTransactionsAction:None and would wipe it, so it must be surfaced
    # (has_split_action) and later fail-closed by rule_complexity.
    ad = _seq_adapter([
        {"transactionRules": [{
            "id": "r1",
            "merchantNameCriteria": [{"operator": "contains", "value": "acme"}],
            "setMerchantAction": {"id": "m9", "name": "Acme"},
            "setCategoryAction": None,
            "splitTransactionsAction": {"__typename": "SplitTransactionsAction"}}]},
    ])
    (r,) = ad.get_transaction_rules()
    assert r["value"] == "acme"
    assert r["has_split_action"] is True


def test_update_rule_true_when_reread_matches():
    ad = _seq_adapter([
        {"updateTransactionRuleV2": {"errors": []}},                     # mutation
        {"transactionRules": [{                                          # re-read reflects it
            "id": "r1",
            "merchantNameCriteria": [{"operator": "contains", "value": "ubs"}],
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
            "merchantNameCriteria": [{"operator": "contains", "value": "OLD"}],
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
