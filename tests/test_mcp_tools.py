import datetime
from decimal import Decimal

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.config import CacheTTLs
from financial_mcp.data_service import DataService
from financial_mcp.mcp_server import build_server
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import (
    Account,
    Balance,
    BalanceSnapshot,
    Cashflow,
    NetWorthSnapshot,
    Transaction,
)


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

    def get_networth_history(self):
        return []

    def get_categories(self):
        from financial_mcp.monarch.models import Category

        return [Category(id="c1", name="Groceries", group="Food & Dining")]

    def get_budgets(self, start, end):
        return []

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
    data = result["data"]
    groc = next(r for r in data["categories"] if r["category"] == "Groceries")
    assert groc["group"] == "Food & Dining"   # requires FakeAdapter.get_categories() below
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


class SavingsAdapter(FakeAdapter):
    """Cashflow + two dated snapshots per account for savings_summary."""

    def get_cashflow(self, start, end):
        return Cashflow(
            start=start, end=end,
            income=Decimal("45976.73"), expense=Decimal("-34059.93"),
            savings=Decimal("11916.80"), savings_rate_monarch=Decimal("0.2592"),
        )

    def get_balance_history(self):
        def two(account_id, name, type_name, s, e):
            return [
                BalanceSnapshot(account_id=account_id, display_name=name, date=datetime.date(2026, 6, 1),
                                balance=Decimal(s), type_name=type_name, include_in_net_worth=True),
                BalanceSnapshot(account_id=account_id, display_name=name, date=datetime.date(2026, 6, 30),
                                balance=Decimal(e), type_name=type_name, include_in_net_worth=True),
            ]
        return (
            two("chk", "Checking", "depository", "0.00", "3000.00")
            + two("heloc", "HELOC", "loan", "-100000.00", "-89385.00")   # +10615 paydown
            + two("cc", "Credit Card", "credit", "0.00", "-500.00")       # -500
            + two("401k", "401k", "brokerage", "0.00", "8000.00")         # shown separately
        )


def _sav_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(SavingsAdapter(), cache)


def test_savings_summary_envelope_and_reconciliation(tmp_path):
    server = build_server(_sav_service(tmp_path))
    result = _tool(server, "savings_summary")(start="2026-06-01", end="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    data = result["data"]
    assert data["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    # money serialized as strings
    assert data["cashflow"]["income"] == "45976.73"
    assert data["cashflow"]["savings_rate"] == "0.2592"
    assert data["cashflow"]["rate_is_floored"] is False
    # 3000 - 500 + 10615 = 13115.00 ; gap = 13115.00 - 11916.80 = 1198.20
    assert data["net_worth_delta_cash_debt"] == "13115.00"
    assert data["reconciliation_gap"] == "1198.20"
    cd = {r["account_id"] for r in data["where_it_went"]["cash_and_debt"]}
    inv = {r["account_id"] for r in data["where_it_went"]["investments_shown_separately"]}
    assert cd == {"chk", "cc", "heloc"}
    assert inv == {"401k"}
    # nested Decimals inside list rows are stringified
    heloc = next(r for r in data["where_it_went"]["cash_and_debt"] if r["account_id"] == "heloc")
    assert heloc["delta"] == "10615.00"
    assert heloc["end"]["balance"] == "-89385.00"
    assert "take-home" in data["methodology"]


class FlooredSavingsAdapter(FakeAdapter):
    """A bad month: spending exceeds take-home income, so savings is negative
    and Monarch floors the rate to 0. No balance history (empty) — the true
    signed negative rate and rate_is_floored flag must still survive JSON
    transport end-to-end through the tool."""

    def get_cashflow(self, start, end):
        return Cashflow(
            start=start, end=end,
            income=Decimal("100000.00"), expense=Decimal("-131086.46"),
            savings=Decimal("-31086.46"), savings_rate_monarch=Decimal("0"),
        )

    def get_balance_history(self):
        return []


def _floored_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(FlooredSavingsAdapter(), cache)


def test_savings_summary_floored_negative_rate_survives_transport(tmp_path):
    server = build_server(_floored_service(tmp_path))
    result = _tool(server, "savings_summary")(start="2026-01-01", end="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    data = result["data"]
    cf = data["cashflow"]
    # Monarch floors negative rates to 0; we surface the true signed rate + flag.
    # -31086.46 / 100000.00 = -0.3108646 -> -0.3109 (ROUND_HALF_UP, 4dp)
    assert cf["savings"] == "-31086.46"
    assert cf["savings_rate"] == "-0.3109"
    assert cf["savings_rate_monarch"] == "0"
    assert cf["rate_is_floored"] is True
    # Empty balance history: nothing to sum, gap = 0 - savings = 31086.46
    assert data["where_it_went"]["cash_and_debt"] == []
    assert data["where_it_went"]["investments_shown_separately"] == []
    assert data["net_worth_delta_cash_debt"] == "0"
    assert data["reconciliation_gap"] == "31086.46"


def test_savings_summary_bad_start_date(tmp_path):
    server = build_server(_sav_service(tmp_path))
    result = _tool(server, "savings_summary")(start="June 1", end="2026-06-30")
    assert result == {"error": "start must be YYYY-MM-DD"}


def test_savings_summary_bad_end_date(tmp_path):
    server = build_server(_sav_service(tmp_path))
    result = _tool(server, "savings_summary")(start="2026-06-01", end="nope")
    assert result == {"error": "end must be YYYY-MM-DD"}


def test_savings_summary_start_after_end(tmp_path):
    server = build_server(_sav_service(tmp_path))
    result = _tool(server, "savings_summary")(start="2026-06-30", end="2026-06-01")
    assert result == {"error": "start must not be after end"}


def test_savings_summary_error_on_expired_session(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class ExpiredAdapter(SavingsAdapter):
        def get_cashflow(self, start, end):
            raise SessionExpiredError("expired")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(ExpiredAdapter(), cache))
    result = _tool(server, "savings_summary")(start="2026-06-01", end="2026-06-30")
    assert "financial-mcp-login" in result["error"]


class NetWorthAdapter(FakeAdapter):
    def get_networth_history(self):
        return [
            NetWorthSnapshot(date=datetime.date(2026, 1, 31), net_worth=Decimal("1250000.00")),
            NetWorthSnapshot(date=datetime.date(2026, 6, 30), net_worth=Decimal("1300000.00")),
        ]


def _nw_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(NetWorthAdapter(), cache)


def test_networth_trend_envelope_and_change(tmp_path):
    server = build_server(_nw_service(tmp_path))
    result = _tool(server, "networth_trend")(start="2026-01-01", end="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    data = result["data"]
    assert data["start_point"]["net_worth"] == "1250000.00"   # stringified
    assert data["end_point"]["net_worth"] == "1300000.00"
    assert data["change"]["net_worth"] == "50000.00"
    assert result["count"] == 2


def test_networth_trend_bad_date(tmp_path):
    server = build_server(_nw_service(tmp_path))
    assert _tool(server, "networth_trend")(start="Jan 1", end="2026-06-30") == {
        "error": "start must be YYYY-MM-DD"
    }


def test_networth_trend_start_after_end(tmp_path):
    server = build_server(_nw_service(tmp_path))
    assert _tool(server, "networth_trend")(start="2026-06-30", end="2026-01-01") == {
        "error": "start must not be after end"
    }
