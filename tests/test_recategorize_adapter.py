import pytest

from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter


class _Transport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __call__(self, operation, query, variables):
        self.calls.append((operation, query, variables))
        return self.response


def _adapter(response):
    t = _Transport(response)
    a = CommunityAdapter(
        session={"session_id": "s", "csrftoken": "c"},
        service_name="svc",
        transport=t,
    )
    return a, t


def test_recategorize_sends_category_input_and_confirms_applied():
    a, t = _adapter({"updateTransaction": {"transaction": {"id": "tx1", "category": {"id": "cat9"}}}})
    assert a.recategorize_transaction("tx1", "cat9") is True
    op, _query, variables = t.calls[0]
    assert op == "Web_TransactionDrawerUpdateTransaction"
    assert variables["input"] == {"id": "tx1", "category": "cat9"}


def test_recategorize_200_but_no_change_returns_false():
    # Monarch accepted the call but the category did not change to the target.
    a, t = _adapter({"updateTransaction": {"transaction": {"id": "tx1", "category": {"id": "OLD"}}}})
    assert a.recategorize_transaction("tx1", "cat9") is False


def test_csv_adapter_recategorize_refuses():
    csv = CsvAdapter("unused_txns.csv", "unused_balances.csv")
    with pytest.raises(RuntimeError, match="writes are not available in CSV mode"):
        csv.recategorize_transaction("tx1", "cat9")
