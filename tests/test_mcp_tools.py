import datetime
from decimal import Decimal

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.config import CacheTTLs
from financial_mcp.data_service import DataService
from financial_mcp.mcp_server import build_server
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Cashflow, Transaction


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

    def get_balance_history(self):
        return []

    def get_cashflow(self, start, end):
        return Cashflow(
            start=start, end=end,
            income=Decimal("0"), expense=Decimal("0"),
            savings=Decimal("0"), savings_rate_monarch=Decimal("0"),
        )

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
    result = _tool(server, "get_accounts")()
    accts = result["data"]
    assert accts[0]["name"] == "Checking"
    assert accts[0]["latest_balance"] == "100"


def test_get_accounts_envelope_carries_freshness(tmp_path):
    server = build_server(_service(tmp_path))
    result = _tool(server, "get_accounts")()
    # Envelope keys Claude uses to reason about staleness.
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    assert result["count"] == 1
    assert result["stale"] is False  # just fetched
    assert isinstance(result["as_of"], str)


def test_category_summary_tool(tmp_path):
    server = build_server(_service(tmp_path))
    result = _tool(server, "category_summary")()
    assert result["data"][0]["category"] == "Groceries"
    assert "as_of" in result


def test_get_transactions_limit(tmp_path):
    server = build_server(_service(tmp_path))
    result = _tool(server, "get_transactions")(limit=1)
    txns = result["data"]
    assert result["count"] == 1
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
    result = _tool(server, "get_transactions")(limit=100000)
    assert len(result["data"]) == MAX_TXN_ROWS


def test_get_transactions_returns_most_recent_first(tmp_path):
    server = build_server(_many_service(tmp_path))
    result = _tool(server, "get_transactions")(limit=3)
    dates = [t["date"] for t in result["data"]]
    assert dates == sorted(dates, reverse=True)


def test_search_transactions_default_capped(tmp_path):
    from financial_mcp.mcp_server import DEFAULT_TXN_ROWS

    server = build_server(_many_service(tmp_path))
    # 1200 matches, but the default view must be bounded.
    result = _tool(server, "search_transactions")(merchant="amazon")
    assert len(result["data"]) == DEFAULT_TXN_ROWS


def test_search_transactions_caps_at_max_rows(tmp_path):
    from financial_mcp.mcp_server import MAX_TXN_ROWS

    server = build_server(_many_service(tmp_path))
    result = _tool(server, "search_transactions")(merchant="amazon", limit=100000)
    assert len(result["data"]) == MAX_TXN_ROWS


def test_search_transactions_most_recent_first(tmp_path):
    server = build_server(_many_service(tmp_path))
    result = _tool(server, "search_transactions")(merchant="amazon", limit=5)
    dates = [r["date"] for r in result["data"]]
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


class WindowAdapter(FakeAdapter):
    """Two Amazon txns inside Jul 2026 — one charge, one refund."""

    def get_transactions(self):
        return [
            Transaction(
                id=Transaction.synth_id(
                    datetime.date(2026, 7, 20), "Amazon", Decimal("-100.00"), "AMZN"
                ),
                date=datetime.date(2026, 7, 20),
                merchant="Amazon",
                category="Shopping",
                account="Checking",
                original_statement="AMZN",
                notes="",
                amount=Decimal("-100.00"),
                tags=[],
                owner="A",
            ),
            Transaction(
                id=Transaction.synth_id(
                    datetime.date(2026, 7, 21), "Amazon", Decimal("30.00"), "AMZN"
                ),
                date=datetime.date(2026, 7, 21),
                merchant="Amazon",
                category="Shopping",
                account="Checking",
                original_statement="AMZN",
                notes="",
                amount=Decimal("30.00"),
                tags=[],
                owner="A",
            ),
        ]


def _window_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(WindowAdapter(), cache)


def test_spending_summary_envelope_and_math(tmp_path):
    server = build_server(_window_service(tmp_path))
    result = _tool(server, "spending_summary")(
        start_date="2026-07-19", end_date="2026-07-22", merchant="amazon"
    )
    # Freshness envelope keys present.
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    assert result["count"] == 2
    data = result["data"]
    # Money serialized as strings, computed exactly.
    assert data["total"] == "70.00"  # 100.00 charge - 30.00 refund
    assert data["gross_charges"] == "100.00"
    assert data["refunds"] == "30.00"
    assert data["charge_count"] == 1
    assert data["refund_count"] == 1


def test_spending_summary_bad_start_date(tmp_path):
    server = build_server(_window_service(tmp_path))
    result = _tool(server, "spending_summary")(start_date="July 19", end_date="2026-07-22")
    assert result == {"error": "start_date must be YYYY-MM-DD"}


def test_spending_summary_bad_end_date(tmp_path):
    server = build_server(_window_service(tmp_path))
    result = _tool(server, "spending_summary")(start_date="2026-07-19", end_date="nope")
    assert result == {"error": "end_date must be YYYY-MM-DD"}


def test_spending_summary_start_after_end(tmp_path):
    server = build_server(_window_service(tmp_path))
    result = _tool(server, "spending_summary")(start_date="2026-07-25", end_date="2026-07-22")
    assert result == {"error": "start_date must not be after end_date"}


def test_spending_summary_returns_error_on_expired_session(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class ExpiredAdapter(FakeAdapter):
        def get_transactions(self):
            raise SessionExpiredError("expired")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(ExpiredAdapter(), cache))
    result = _tool(server, "spending_summary")(start_date="2026-07-19", end_date="2026-07-22")
    assert "financial-mcp-login" in result["error"]


class BalanceHistoryAdapter(FakeAdapter):
    """HELOC with two dated snapshots for as-of / delta tools."""

    def get_balance_history(self):
        return [
            BalanceSnapshot(account_id="heloc", display_name="HELOC", date=datetime.date(2026, 5, 31),
                            balance=Decimal("-224600.00"), type_name="loan", include_in_net_worth=True),
            BalanceSnapshot(account_id="heloc", display_name="HELOC", date=datetime.date(2026, 6, 28),
                            balance=Decimal("-202143.55"), type_name="loan", include_in_net_worth=True),
        ]


def _bh_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(BalanceHistoryAdapter(), cache)


def test_balance_as_of_tool_carry_forward(tmp_path):
    server = build_server(_bh_service(tmp_path))
    result = _tool(server, "balance_as_of")(date="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    heloc = next(r for r in result["data"] if r["display_name"] == "HELOC")
    assert heloc["balance"] == "-202143.55"      # Decimal serialized as string
    assert heloc["snapshot_date"] == "2026-06-28"
    assert heloc["is_carry_forward"] is True


def test_balance_as_of_tool_rejects_bad_date(tmp_path):
    server = build_server(_bh_service(tmp_path))
    result = _tool(server, "balance_as_of")(date="not-a-date")
    assert result == {"error": "date must be YYYY-MM-DD"}


def test_balance_delta_tool_positive_paydown(tmp_path):
    server = build_server(_bh_service(tmp_path))
    result = _tool(server, "balance_delta")(start="2026-05-31", end="2026-06-28")
    heloc = next(r for r in result["data"] if r["display_name"] == "HELOC")
    assert heloc["delta"] == "22456.45"          # paydown surfaces positive, as string
    assert heloc["start"]["balance"] == "-224600.00"   # nested Decimals also stringified
    assert heloc["end"]["balance"] == "-202143.55"


def test_balance_delta_tool_rejects_reversed_dates(tmp_path):
    server = build_server(_bh_service(tmp_path))
    result = _tool(server, "balance_delta")(start="2026-06-28", end="2026-05-31")
    assert result == {"error": "start must not be after end"}
