from financial_mcp.monarch import queries


def test_get_accounts_query_shape():
    q = queries.GET_ACCOUNTS
    assert "query GetAccounts" in q
    # fields the _to_account mapper reads
    assert "displayName" in q
    assert "currentBalance" in q


def test_get_transactions_query_shape():
    q = queries.GET_TRANSACTIONS
    assert "allTransactions" in q
    assert "totalCount" in q
    assert "results(offset: $offset, limit: $limit" in q
    # fields the _to_transaction mapper reads
    for field in ("amount", "date", "plaidName", "notes", "merchant", "category", "account", "tags"):
        assert field in q
