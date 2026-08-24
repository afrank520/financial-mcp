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
