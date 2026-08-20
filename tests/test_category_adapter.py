import pytest

from financial_mcp.monarch.community_adapter import CommunityAdapter
from financial_mcp.monarch.csv_adapter import CsvAdapter


def _adapter(responses):
    async def transport(operation, query, variables):
        return responses[operation]
    return CommunityAdapter(
        session={"session_id": "s", "csrftoken": "c"},
        service_name="svc",
        transport=transport,
    )


def test_create_category_returns_new_id():
    a = _adapter({"CreateCategory": {"createCategory": {"category": {"id": "new1", "name": "Vacations"}}}})
    assert a.create_category("Vacations", "g1", "🏷️") == "new1"


def test_create_category_raises_when_monarch_returns_no_id():
    a = _adapter({"CreateCategory": {"createCategory": {"category": {}}}})
    with pytest.raises(RuntimeError):
        a.create_category("Vacations", "g1", "🏷️")


def test_rename_category_confirms_applied_name():
    a = _adapter({"UpdateCategory": {"updateCategory": {"category": {"id": "c1", "name": "New Name"}}}})
    assert a.rename_category("c1", "New Name") is True


def test_rename_category_false_when_name_not_applied():
    a = _adapter({"UpdateCategory": {"updateCategory": {"category": {"id": "c1", "name": "Old Name"}}}})
    assert a.rename_category("c1", "New Name") is False


def test_get_categories_populates_group_id():
    a = _adapter({"GetCategories": {"categories": [
        {"id": "c1", "name": "Groceries",
         "group": {"id": "g1", "name": "Food & Dining", "type": "expense"}}]}})
    cats = a.get_categories()
    assert cats[0].group_id == "g1"


def test_get_categories_maps_icon():
    a = _adapter({"GetCategories": {"categories": [
        {"id": "c1", "name": "Groceries", "icon": "🛒",
         "group": {"id": "g1", "name": "Food & Dining", "type": "expense"}}]}})
    cats = a.get_categories()
    assert cats[0].icon == "🛒"
    assert cats[0].group_id == "g1"


def test_csv_adapter_category_writes_refuse():
    csv = CsvAdapter("unused_txns.csv", "unused_balances.csv")
    with pytest.raises(RuntimeError, match="writes are not available in CSV mode"):
        csv.create_category("X", "g1", "🏷️")
    with pytest.raises(RuntimeError, match="writes are not available in CSV mode"):
        csv.rename_category("c1", "X")


def test_delete_category_returns_true_when_monarch_confirms():
    from financial_mcp.monarch.community_adapter import CommunityAdapter

    async def transport(operation, query, variables):
        assert operation == "Web_DeleteCategory"
        assert variables == {"id": "c1"}
        return {"deleteCategory": {"deleted": True, "errors": None}}

    adapter = CommunityAdapter(session={"session_id": "s", "csrftoken": "c"},
                               service_name="svc", transport=transport)
    assert adapter.delete_category("c1") is True


def test_delete_category_returns_false_when_not_deleted():
    from financial_mcp.monarch.community_adapter import CommunityAdapter

    async def transport(operation, query, variables):
        return {"deleteCategory": {"deleted": False,
                                   "errors": [{"message": "not empty"}]}}

    adapter = CommunityAdapter(session={"session_id": "s", "csrftoken": "c"},
                               service_name="svc", transport=transport)
    assert adapter.delete_category("c1") is False


def test_csv_adapter_delete_category_refuses():
    import pytest
    from financial_mcp.monarch.csv_adapter import CsvAdapter
    adapter = CsvAdapter("unused_txns.csv", "unused_balances.csv")
    with pytest.raises(RuntimeError, match="writes are not available in CSV mode"):
        adapter.delete_category("c1")
