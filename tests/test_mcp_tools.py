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

    def get_holdings(self):
        return []

    def get_recurring(self, start, end):
        return []

    def recategorize_transaction(self, transaction_id, category_id):
        # tests that exercise writes override/inspect this; default records the call
        self.recategorized = getattr(self, "recategorized", [])
        self.recategorized.append((transaction_id, category_id))
        return True

    def create_category(self, name, group_id, icon):
        raise NotImplementedError("not exercised by these tests")

    def rename_category(self, category_id, new_name):
        raise NotImplementedError("not exercised by these tests")

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


class BudgetAdapter(FakeAdapter):
    def get_budgets(self, start, end):
        from financial_mcp.monarch.models import BudgetItem
        return [
            BudgetItem(category_id="c1", budgeted=Decimal("500.00"),
                       actual=Decimal("-620.00"), remaining=Decimal("-120.00")),   # expense, over
            BudgetItem(category_id="i1", budgeted=Decimal("45000.00"),
                       actual=Decimal("45836.00"), remaining=Decimal("-836.00")),  # income, favorable
        ]

    def get_categories(self):
        from financial_mcp.monarch.models import Category
        return [
            Category(id="c1", name="Groceries", group="Food & Dining", group_type="expense"),
            Category(id="i1", name="Paychecks", group="Income", group_type="income"),
        ]


def _budget_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(BudgetAdapter(), cache)


def test_budget_status_envelope_and_flags(tmp_path):
    server = build_server(_budget_service(tmp_path))
    result = _tool(server, "budget_status")(start="2026-06-01", end="2026-06-30")
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    data = result["data"]
    # totals split by Monarch type; stringified Decimals (proves _jsonify recursed)
    assert set(data["totals"]) <= {"expense", "income", "transfer", "unknown"}
    assert data["totals"]["expense"]["remaining"] == "-120.00"
    assert data["totals"]["expense"]["over_budget"] is True
    assert isinstance(data["totals"]["expense"]["budgeted"], str)
    # income bucket uses favorable, never over_budget
    assert data["totals"]["income"]["favorable"] is True
    assert "over_budget" not in data["totals"]["income"]
    # expense row keeps over_budget; income row uses favorable
    groc = next(r for r in data["categories"] if r["category"] == "Groceries")
    assert groc["type"] == "expense" and groc["over_budget"] is True
    pay = next(r for r in data["categories"] if r["category"] == "Paychecks")
    assert pay["type"] == "income" and "over_budget" not in pay and pay["favorable"] is True
    # methodology travels with the result
    assert data["methodology"].startswith("Totals are split by Monarch's own")


def test_budget_status_bad_date(tmp_path):
    server = build_server(_budget_service(tmp_path))
    assert _tool(server, "budget_status")(start="June", end="2026-06-30") == {
        "error": "start must be YYYY-MM-DD"
    }


def test_budget_status_start_after_end(tmp_path):
    server = build_server(_budget_service(tmp_path))
    assert _tool(server, "budget_status")(start="2026-06-30", end="2026-06-01") == {
        "error": "start must not be after end"
    }


def test_budget_status_error_on_expired_session(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class ExpiredAdapter(BudgetAdapter):
        def get_budgets(self, start, end):
            raise SessionExpiredError("expired")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(ExpiredAdapter(), cache))
    result = _tool(server, "budget_status")(start="2026-06-01", end="2026-06-30")
    assert "financial-mcp-login" in result["error"]


def test_holdings_summary_tool_envelope_and_totals(tmp_path):
    from financial_mcp.monarch.models import Holding

    class _HAdapter(FakeAdapter):
        def get_holdings(self):
            return [
                Holding(account="Brokerage", ticker="VTI", name="VTI",
                        quantity=Decimal("2"), price=Decimal("100"), value=Decimal("200"),
                        cost_basis=Decimal("150"),
                        day_change_dollars=Decimal("3"), day_change_percent=Decimal("1.5")),
                Holding(account="IRA", ticker=None, name="Manual",
                        quantity=Decimal("1"), price=Decimal("50"), value=Decimal("50"),
                        cost_basis=None),
            ]

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(_HAdapter(), cache)
    server = build_server(svc)
    data = _tool(server, "holdings_summary")()
    assert data["count"] == 2
    body = data["data"]
    assert body["positions"][0]["ticker"] == "VTI"        # biggest value first
    # Decimals arrive as strings
    assert body["totals"]["value"] == "250"
    assert body["totals"]["cost_basis"] == "150"
    assert body["totals"]["gain_loss"] == "50"
    assert isinstance(body["positions"][0]["value"], str)
    assert body["positions"][1]["gain_loss"] is None      # basis-less, key present, null
    assert "methodology" in body


def test_recurring_summary_tool_envelope(tmp_path):
    from financial_mcp.monarch.models import RecurringItem

    class _RAdapter(FakeAdapter):
        def get_recurring(self, start, end):
            return [
                RecurringItem(date=datetime.date(2026, 6, 20), merchant="Spotify",
                              amount=Decimal("-9.99"), frequency="monthly",
                              account="Card", category="Subs", is_past=False),
                RecurringItem(date=datetime.date(2026, 6, 3), merchant="Netflix",
                              amount=Decimal("-15.99"), frequency="monthly",
                              account="Card", category="Subs", is_past=True),
            ]

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(_RAdapter(), cache)
    server = build_server(svc)
    data = _tool(server, "recurring_summary")("2026-06-01", "2026-06-30")
    assert data["count"] == 2
    body = data["data"]
    assert [i["merchant"] for i in body["items"]] == ["Netflix", "Spotify"]  # date asc
    assert body["total_expected"] == "-25.98"        # Decimal -> string
    assert isinstance(body["items"][0]["amount"], str)
    assert "methodology" in body


def test_recurring_summary_reports_live_freshness(tmp_path):
    """recurring_summary must report honest live freshness (as_of=now, never
    stale), NOT the borrowed/unread `transactions` cache type's freshness.

    Regression guard: the transactions cache is deliberately left unpopulated
    (fresh SqliteCache, never call get_transactions/get_accounts). Under the
    old buggy code, _envelope("transactions", ...) would see no cache entry
    and return as_of=None, stale=True. The fix must return a real as_of and
    stale=False regardless of transactions cache state.
    """
    from financial_mcp.monarch.models import RecurringItem

    class _RAdapter(FakeAdapter):
        def get_recurring(self, start, end):
            return [
                RecurringItem(date=datetime.date(2026, 6, 20), merchant="Spotify",
                              amount=Decimal("-9.99"), frequency="monthly",
                              account="Card", category="Subs", is_past=False),
            ]

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(_RAdapter(), cache)
    server = build_server(svc)

    # Do NOT populate the transactions cache before calling recurring_summary.
    data = _tool(server, "recurring_summary")("2026-06-01", "2026-06-30")

    assert data["stale"] is False
    assert data["age_hours"] == 0.0
    assert data["as_of"] is not None
    assert "T" in data["as_of"]


def test_recurring_summary_tool_rejects_bad_dates(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(FakeAdapter(), cache)
    server = build_server(svc)
    assert _tool(server, "recurring_summary")("nope", "2026-06-30") == {"error": "start must be YYYY-MM-DD"}
    assert _tool(server, "recurring_summary")("2026-06-30", "2026-06-01")["error"] == "start must not be after end"


# ---------------------------------------------------------------------------
# recategorize_transaction (single-transaction write tool)
# ---------------------------------------------------------------------------


class RecatAdapter(FakeAdapter):
    """One known-id transaction currently 'Uncategorized', plus a target
    category ('Shopping') distinct from its current one — needed to exercise
    real category resolution (fail-closed) and old-category lookup."""

    def get_transactions(self):
        d = datetime.date(2026, 1, 2)
        return [
            Transaction(
                id="tx1",
                date=d,
                merchant="Costco",
                category="Uncategorized",
                account="Checking",
                original_statement="COSTCO",
                notes="",
                amount=Decimal("-5.00"),
                tags=[],
                owner="A",
            )
        ]

    def get_categories(self):
        from financial_mcp.monarch.models import Category

        return [
            Category(id="uncat", name="Uncategorized", group="Other"),
            Category(id="shop", name="Shopping", group="Discretionary"),
        ]


def _recat_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(RecatAdapter(), cache)


def test_recategorize_off_mode_refuses(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal

    svc = _recat_service(tmp_path)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode="off", batch_cap=200, journal=journal)
    tool = _tool(server, "recategorize_transaction")
    out = tool(transaction_id="tx1", category="Shopping")
    assert "disabled" in out["error"]
    # Refuses before any resolution/network call — adapter never recorded a write.
    assert not getattr(svc._adapter, "recategorized", [])


def test_recategorize_safe_mode_one_shot_records_and_invalidates(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal

    svc = _recat_service(tmp_path)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode="safe", batch_cap=200, journal=journal)
    tool = _tool(server, "recategorize_transaction")

    out = tool(transaction_id="tx1", category="Shopping")

    assert out["changed"] == 1
    assert out["from"] == "Uncategorized"
    assert out["to"] == "Shopping"
    assert "batch_id" in out and "undo_hint" in out
    assert svc._adapter.recategorized == [("tx1", "shop")]
    assert journal.last_undoable_batch() == out["batch_id"]


def test_recategorize_bad_category_refuses_with_valid_list(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal

    svc = _recat_service(tmp_path)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode="safe", batch_cap=200, journal=journal)
    tool = _tool(server, "recategorize_transaction")

    out = tool(transaction_id="tx1", category="Nonexistent")

    assert "Nonexistent" in out["error"]
    assert "Shopping" in out["error"]
    # Fail-closed: no write attempted.
    assert not getattr(svc._adapter, "recategorized", [])


# ---------------------------------------------------------------------------
# recategorize_matching (bulk write tool) — dry-run/confirm handshake, batch
# cap, honest partial-failure result.
# ---------------------------------------------------------------------------


class BulkRecatAdapter(FakeAdapter):
    """Two known-id Amazon transactions currently 'Uncategorized', plus a
    target category ('Shopping') distinct from the current one."""

    def get_transactions(self):
        return [
            Transaction(
                id="tx1",
                date=datetime.date(2026, 1, 2),
                merchant="Amazon",
                category="Uncategorized",
                account="Checking",
                original_statement="AMZN",
                notes="",
                amount=Decimal("-10.00"),
                tags=[],
                owner="A",
            ),
            Transaction(
                id="tx2",
                date=datetime.date(2026, 1, 3),
                merchant="Amazon",
                category="Uncategorized",
                account="Checking",
                original_statement="AMZN",
                notes="",
                amount=Decimal("-20.00"),
                tags=[],
                owner="A",
            ),
        ]

    def get_categories(self):
        from financial_mcp.monarch.models import Category

        return [
            Category(id="uncat", name="Uncategorized", group="Other"),
            Category(id="shop", name="Shopping", group="Discretionary"),
        ]


def _bulk_recat_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(BulkRecatAdapter(), cache)


def _bulk_server(tmp_path, write_mode="safe", batch_cap=200):
    from financial_mcp.cache.undo_journal import UndoJournal

    svc = _bulk_recat_service(tmp_path)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode=write_mode, batch_cap=batch_cap, journal=journal)
    return svc, journal, server


def test_bulk_off_mode_refuses(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="off")
    tool = _tool(server, "recategorize_matching")
    out = tool(to_category="Shopping", merchant="Amazon")
    assert "disabled" in out["error"]
    assert not getattr(svc._adapter, "recategorized", [])


def test_bulk_safe_dryrun_previews_without_mutating(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "recategorize_matching")

    out = tool(to_category="Shopping", merchant="Amazon")

    assert out["dry_run"] is True
    assert out["would_change"] == 2
    assert out["resolved_category"] == {"id": "shop", "name": "Shopping", "group": "Discretionary"}
    assert len(out["sample"]) == 2
    assert {row["current_category"] for row in out["sample"]} == {"Uncategorized"}
    assert "confirm_token" in out
    assert out["mode"] == "safe"
    # Nothing mutated on a dry-run preview.
    assert not getattr(svc._adapter, "recategorized", [])


def test_bulk_confirm_token_executes_and_journals(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "recategorize_matching")

    preview = tool(to_category="Shopping", merchant="Amazon")
    out = tool(to_category="Shopping", merchant="Amazon", confirm_token=preview["confirm_token"])

    assert out["changed"] == 2
    assert out["failed"] == 0
    assert out["to"] == "Shopping"
    assert "batch_id" in out
    assert journal.last_undoable_batch() == out["batch_id"]
    assert sorted(svc._adapter.recategorized) == [("tx1", "shop"), ("tx2", "shop")]


def test_bulk_stale_token_refused_and_repreviewed(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "recategorize_matching")

    out = tool(to_category="Shopping", merchant="Amazon", confirm_token="deadbeefdeadbeef")

    assert out.get("dry_run") is True  # stale/wrong token -> re-preview, not execute
    assert not getattr(svc._adapter, "recategorized", [])


def test_bulk_full_mode_executes_directly(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="full")
    tool = _tool(server, "recategorize_matching")

    out = tool(to_category="Shopping", merchant="Amazon")

    assert out["changed"] == 2 and out["failed"] == 0
    assert journal.last_undoable_batch() == out["batch_id"]


def test_bulk_over_cap_refuses(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe", batch_cap=1)
    tool = _tool(server, "recategorize_matching")

    out = tool(to_category="Shopping", merchant="Amazon")

    assert "error" in out
    assert "over the batch cap" in out["error"]
    assert not getattr(svc._adapter, "recategorized", [])


def test_bulk_empty_match(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "recategorize_matching")

    out = tool(to_category="Shopping", merchant="ZZZ-nothing")

    assert out == {"would_change": 0, "message": "no transactions matched"}
    assert "confirm_token" not in out


def test_bulk_bad_to_category_fails_closed(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "recategorize_matching")

    out = tool(to_category="Nonexistent", merchant="Amazon")

    assert "Nonexistent" in out["error"]
    assert "Shopping" in out["error"]
    assert not getattr(svc._adapter, "recategorized", [])


def test_bulk_bad_start_date(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "recategorize_matching")
    out = tool(to_category="Shopping", merchant="Amazon", start="nope")
    assert out == {"error": "start must be YYYY-MM-DD"}


def test_bulk_bad_end_date(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "recategorize_matching")
    out = tool(to_category="Shopping", merchant="Amazon", end="nope")
    assert out == {"error": "end must be YYYY-MM-DD"}


def test_bulk_start_after_end(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "recategorize_matching")
    out = tool(to_category="Shopping", merchant="Amazon", start="2026-01-05", end="2026-01-01")
    assert out == {"error": "start must not be after end"}


class PartialFailBulkAdapter(BulkRecatAdapter):
    """recategorize_transaction returns False for tx2 — simulates Monarch
    accepting the call but not applying the category for one transaction,
    to exercise the not-applied branch of the bulk executor."""

    def recategorize_transaction(self, transaction_id, category_id):
        self.recategorized = getattr(self, "recategorized", [])
        self.recategorized.append((transaction_id, category_id))
        return transaction_id != "tx2"


def test_bulk_partial_failure_reports_honestly_and_only_succeeded_undoable(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(PartialFailBulkAdapter(), cache)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode="full", batch_cap=200, journal=journal)
    tool = _tool(server, "recategorize_matching")

    out = tool(to_category="Shopping", merchant="Amazon")

    assert out["changed"] == 1
    assert out["failed"] == 1
    assert out["failures"] == [
        {"transaction_id": "tx2", "reason": "Monarch did not apply the category"}
    ]
    # Only the succeeded row (tx1) is undoable — the failed row (tx2) is not.
    assert journal.last_undoable_batch() == out["batch_id"]
    rows = journal.undoable_rows(out["batch_id"])
    assert [r["target_id"] for r in rows] == ["tx1"]


# ---------------------------------------------------------------------------
# undo_last / undo_history
# ---------------------------------------------------------------------------


def test_undo_last_reverses_most_recent_batch(tmp_path):
    svc, journal, server = _bulk_server(tmp_path)
    recat = _tool(server, "recategorize_matching")

    preview = recat(to_category="Shopping", merchant="Amazon")
    recat(to_category="Shopping", merchant="Amazon", confirm_token=preview["confirm_token"])
    svc._adapter.recategorized.clear()

    undo = _tool(server, "undo_last")
    out = undo()

    assert out["reversed"] == 2
    # Compensating writes restored the ORIGINAL category id ("uncat") for
    # both transactions -- not the new one ("shop").
    assert svc._adapter.recategorized == [("tx1", "uncat"), ("tx2", "uncat")]
    assert journal.last_undoable_batch() is None


def test_undo_last_nothing_to_undo(tmp_path):
    svc, journal, server = _bulk_server(tmp_path)
    out = _tool(server, "undo_last")()
    assert out["reversed"] == 0
    assert out["message"] == "nothing to undo"


def test_undo_history_lists_batches(tmp_path):
    svc, journal, server = _bulk_server(tmp_path)
    recat = _tool(server, "recategorize_matching")

    preview = recat(to_category="Shopping", merchant="Amazon")
    recat(to_category="Shopping", merchant="Amazon", confirm_token=preview["confirm_token"])

    out = _tool(server, "undo_history")()

    assert out["history"][0]["count"] == 2
    assert "→Shopping" in out["history"][0]["summary"]


def test_undo_off_mode_refuses(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="off")
    out = _tool(server, "undo_last")()
    assert "disabled" in out["error"]
    assert not getattr(svc._adapter, "recategorized", [])


def test_undo_last_multi_level_steps_to_prior_batch(tmp_path):
    """A second undo_last call after the first steps back to the batch before it."""
    svc, journal, server = _bulk_server(tmp_path)
    recat = _tool(server, "recategorize_transaction")

    # Batch 1: tx1 Uncategorized -> Shopping
    out1 = recat(transaction_id="tx1", category="Shopping")
    batch1 = out1["batch_id"]
    # Batch 2: tx2 Uncategorized -> Shopping
    out2 = recat(transaction_id="tx2", category="Shopping")
    batch2 = out2["batch_id"]

    assert journal.last_undoable_batch() == batch2

    undo = _tool(server, "undo_last")
    first = undo()
    assert first["reversed"] == 1
    assert first["batch_id"] == batch2
    # last_undoable_batch has advanced to the prior batch.
    assert journal.last_undoable_batch() == batch1

    second = undo()
    assert second["reversed"] == 1
    assert second["batch_id"] == batch1
    assert journal.last_undoable_batch() is None


class PartialFailUndoAdapter(BulkRecatAdapter):
    """Forward writes (to 'shop') always succeed so the batch journals both
    rows as undoable. The compensating write undo_last issues (back to
    'uncat') returns False for tx2 while `fail_tx2` is set — simulating
    Monarch accepting the call but not applying the restored category for
    one row of the batch."""

    def __init__(self):
        self.fail_tx2 = True

    def recategorize_transaction(self, transaction_id, category_id):
        self.recategorized = getattr(self, "recategorized", [])
        self.recategorized.append((transaction_id, category_id))
        if transaction_id == "tx2" and category_id == "uncat" and self.fail_tx2:
            return False
        return True


def test_undo_last_partial_failure_reports_honestly_and_retains_open_row(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    adapter = PartialFailUndoAdapter()
    svc = DataService(adapter, cache)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode="full", batch_cap=200, journal=journal)
    recat = _tool(server, "recategorize_matching")
    undo = _tool(server, "undo_last")

    # Forward batch: tx1, tx2 Uncategorized -> Shopping (adapter reverts fine
    # forward -- only the undo-direction compensating write fails for tx2).
    recat(to_category="Shopping", merchant="Amazon")
    batch_id = journal.last_undoable_batch()
    adapter.recategorized.clear()

    out = undo()

    assert out["reversed"] == 1
    assert out["failed"] == 1
    assert out["failures"] == [
        {"transaction_id": "tx2", "reason": "Monarch did not apply the category"}
    ]
    assert out["batch_id"] == batch_id
    assert out["restored_to"] == "Uncategorized"

    # The reverted row (tx1) is now closed; the failed row (tx2) stays open
    # and the batch remains undoable so a retry can target it.
    assert journal.last_undoable_batch() == batch_id
    open_rows = journal.undoable_rows(batch_id)
    assert [r["target_id"] for r in open_rows] == ["tx2"]


def test_undo_last_retry_after_partial_failure_finishes_the_batch(tmp_path):
    from financial_mcp.cache.undo_journal import UndoJournal

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    adapter = PartialFailUndoAdapter()
    svc = DataService(adapter, cache)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode="full", batch_cap=200, journal=journal)
    recat = _tool(server, "recategorize_matching")
    undo = _tool(server, "undo_last")

    recat(to_category="Shopping", merchant="Amazon")
    batch_id = journal.last_undoable_batch()

    first = undo()
    assert first["reversed"] == 1
    assert first["failed"] == 1

    # Monarch now applies the compensating write fine on retry.
    adapter.fail_tx2 = False
    adapter.recategorized.clear()

    second = undo()

    assert second["reversed"] == 1
    assert second["failed"] == 0
    assert second["failures"] == []
    assert second["batch_id"] == batch_id
    assert second["restored_to"] == "Uncategorized"
    assert adapter.recategorized == [("tx2", "uncat")]
    assert journal.last_undoable_batch() is None


class CategoryCrudAdapter(MonarchAdapter):
    """Stub adapter for create_category/rename_category tool tests (Task 4/5).

    get_categories() returns two categories in the same group so Task 5's
    rename-collision tests have something to collide with. create_category
    always returns "newcat". rename_category records the last-applied name
    and always reports success, so undo can re-invoke it to restore the old
    name.
    """

    def get_transactions(self):
        return []

    def get_balances(self):
        return []

    def get_accounts(self):
        return []

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

        return [
            Category(id="c1", name="Groceries", group="Food & Dining", group_id="g1"),
            Category(id="c2", name="Restaurants", group="Food & Dining", group_id="g1"),
        ]

    def get_budgets(self, start, end):
        return []

    def get_holdings(self):
        return []

    def get_recurring(self, start, end):
        return []

    def recategorize_transaction(self, transaction_id, category_id):
        return True

    def create_category(self, name, group_id, icon):
        self.created = getattr(self, "created", [])
        self.created.append((name, group_id, icon))
        return "newcat"

    def rename_category(self, category_id, new_name):
        self.renamed = getattr(self, "renamed", [])
        self.renamed.append((category_id, new_name))
        return True

    def source_name(self):
        return "fake"


def _category_server(tmp_path, write_mode="safe", batch_cap=200):
    """Shared helper for create_category (Task 4) and rename_category (Task 5) tool tests."""
    from financial_mcp.cache.undo_journal import UndoJournal

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(CategoryCrudAdapter(), cache)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode=write_mode, batch_cap=batch_cap, journal=journal)
    return server, journal


def test_create_category_off_mode_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="off")
    out = _tool(server, "create_category")(name="Vacations", group="Food & Dining")
    assert "disabled" in out["error"]


def test_create_category_safe_mode_creates_and_journals(tmp_path):
    server, journal = _category_server(tmp_path, write_mode="safe")
    out = _tool(server, "create_category")(name="Vacations", group="Food & Dining")
    assert out["created"] == 1
    assert out["name"] == "Vacations"
    assert out["group"] == "Food & Dining"
    assert journal.history()[0]["summary"] == "＋Vacations (created) ×1"
    assert journal.last_undoable_batch() is None  # create is not auto-undoable


def test_create_category_unknown_group_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="full")
    out = _tool(server, "create_category")(name="Vacations", group="Nope")
    assert "group" in out["error"].lower()


def test_create_category_duplicate_name_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="full")
    out = _tool(server, "create_category")(name="Groceries", group="Food & Dining")
    assert "already exists" in out["error"]
