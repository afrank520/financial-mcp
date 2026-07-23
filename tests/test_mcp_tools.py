import datetime
from decimal import Decimal

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.config import CacheTTLs
from financial_mcp.data_service import DataService
from financial_mcp.mcp_server import build_server
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, Transaction


class FakeAdapter(MonarchAdapter):
    def get_transactions(self):
        d = datetime.date(2026, 1, 2)
        return [
            Transaction(
                id=Transaction.synth_id(d, "Costco", Decimal("-5.00"), "COSTCO"),
                date=d,
                merchant="Costco",
                category="Groceries",
                account="Checking",
                original_statement="COSTCO",
                notes="",
                amount=Decimal("-5.00"),
                tags=[],
                owner="A",
            )
        ]

    def get_balances(self):
        return [Balance(date=datetime.date(2026, 1, 2), balance=Decimal("100"), account="Checking")]

    def get_accounts(self):
        return [
            Account(name="Checking", latest_balance=Decimal("100"), as_of=datetime.date(2026, 1, 2))
        ]

    def source_name(self):
        return "fake"


def _service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(FakeAdapter(), cache)


def _tool(server, name):
    # FastMCP stores registered tools; fetch the underlying callable.
    return server._tool_manager._tools[name].fn


def test_health_check(tmp_path):
    server = build_server(_service(tmp_path))
    assert _tool(server, "health_check")()["status"] == "ok"


def test_get_accounts_tool(tmp_path):
    server = build_server(_service(tmp_path))
    accts = _tool(server, "get_accounts")()
    assert accts[0]["name"] == "Checking"
    assert accts[0]["latest_balance"] == "100"


def test_category_summary_tool(tmp_path):
    server = build_server(_service(tmp_path))
    rows = _tool(server, "category_summary")()
    assert rows[0]["category"] == "Groceries"


def test_get_transactions_limit(tmp_path):
    server = build_server(_service(tmp_path))
    txns = _tool(server, "get_transactions")(limit=1)
    assert len(txns) == 1
    assert txns[0]["merchant"] == "Costco"
    # Money must be serialized as string, not float
    assert isinstance(txns[0]["amount"], str)


def test_cache_status_tool(tmp_path):
    server = build_server(_service(tmp_path))
    _tool(server, "get_transactions")()  # populate
    statuses = {s["data_type"]: s for s in _tool(server, "cache_status")()}
    assert statuses["transactions"]["count"] == 1


class ManyTxnAdapter(FakeAdapter):
    """Returns more transactions than the result-size cap allows."""

    def get_transactions(self):
        base = datetime.date(2026, 1, 1)
        out = []
        for i in range(1200):
            d = base + datetime.timedelta(days=i)
            out.append(
                Transaction(
                    id=Transaction.synth_id(d, "Amazon", Decimal("-5.00"), "AMZN"),
                    date=d,
                    merchant="Amazon",
                    category="Shopping",
                    account="Checking",
                    original_statement="AMZN",
                    notes="",
                    amount=Decimal("-5.00"),
                    tags=[],
                    owner="A",
                )
            )
        return out


def _many_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(ManyTxnAdapter(), cache)


def test_get_transactions_caps_at_max_rows(tmp_path):
    from financial_mcp.mcp_server import MAX_TXN_ROWS

    server = build_server(_many_service(tmp_path))
    # An LLM asking for a huge limit must not exceed the hard ceiling.
    txns = _tool(server, "get_transactions")(limit=100000)
    assert len(txns) == MAX_TXN_ROWS


def test_get_transactions_returns_most_recent_first(tmp_path):
    server = build_server(_many_service(tmp_path))
    txns = _tool(server, "get_transactions")(limit=3)
    dates = [t["date"] for t in txns]
    assert dates == sorted(dates, reverse=True)


def test_search_transactions_default_capped(tmp_path):
    from financial_mcp.mcp_server import DEFAULT_TXN_ROWS

    server = build_server(_many_service(tmp_path))
    # 1200 matches, but the default view must be bounded.
    rows = _tool(server, "search_transactions")(merchant="amazon")
    assert len(rows) == DEFAULT_TXN_ROWS


def test_search_transactions_caps_at_max_rows(tmp_path):
    from financial_mcp.mcp_server import MAX_TXN_ROWS

    server = build_server(_many_service(tmp_path))
    rows = _tool(server, "search_transactions")(merchant="amazon", limit=100000)
    assert len(rows) == MAX_TXN_ROWS


def test_search_transactions_most_recent_first(tmp_path):
    server = build_server(_many_service(tmp_path))
    rows = _tool(server, "search_transactions")(merchant="amazon", limit=5)
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates, reverse=True)


def test_get_accounts_returns_error_on_expired_session(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class ExpiredAdapter(FakeAdapter):
        def get_accounts(self):
            raise SessionExpiredError("expired")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(ExpiredAdapter(), cache))
    result = _tool(server, "get_accounts")()
    assert isinstance(result, dict)
    assert "financial-mcp-login" in result["error"]


def test_get_accounts_returns_error_on_cloudflare_block(tmp_path):
    from financial_mcp.monarch.community_adapter import CloudflareBlockedError

    class BlockedAdapter(FakeAdapter):
        def get_accounts(self):
            raise CloudflareBlockedError("blocked")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(BlockedAdapter(), cache))
    result = _tool(server, "get_accounts")()
    assert isinstance(result, dict)
    assert "financial-mcp-login" in result["error"]


def test_get_transactions_returns_error_on_expired_session(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class ExpiredAdapter(FakeAdapter):
        def get_transactions(self):
            raise SessionExpiredError("expired")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(ExpiredAdapter(), cache))
    result = _tool(server, "get_transactions")()
    assert isinstance(result, dict)
    assert "financial-mcp-login" in result["error"]


def test_category_summary_returns_error_on_cloudflare_block(tmp_path):
    from financial_mcp.monarch.community_adapter import CloudflareBlockedError

    class BlockedAdapter(FakeAdapter):
        def get_transactions(self):
            raise CloudflareBlockedError("blocked")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(BlockedAdapter(), cache))
    result = _tool(server, "category_summary")()
    assert isinstance(result, dict)
    assert "financial-mcp-login" in result["error"]
