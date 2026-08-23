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
