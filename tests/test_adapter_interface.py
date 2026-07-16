import pytest

from financial_mcp.monarch.adapter import MonarchAdapter


def test_cannot_instantiate_abstract_adapter():
    with pytest.raises(TypeError):
        MonarchAdapter()


def test_partial_subclass_still_abstract():
    class Partial(MonarchAdapter):
        def get_transactions(self):
            return []

    with pytest.raises(TypeError):
        Partial()
