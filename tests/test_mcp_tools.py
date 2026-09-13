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


class _MutableClock:
    """Advanceable clock for exercising cache-age behavior (SqliteCache clock=)."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


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

    def delete_category(self, category_id):
        raise NotImplementedError("not exercised by these tests")

    def rename_transaction_merchant(self, transaction_id, merchant_name):
        return "m_" + str(transaction_id)

    def get_transaction_rules(self):
        return []

    def create_transaction_rule(self, contains, rename_to):
        return (False, None)

    def create_category_rule(self, contains, category_id):
        return (False, None)

    def delete_transaction_rule(self, rule_id):
        return False

    def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant):
        return False

    def source_name(self):
        return "fake"


def _service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(FakeAdapter(), cache)


def _tool(server, name):
    # FastMCP stores registered tools; fetch the underlying callable.
    return server._tool_manager._tools[name].fn


class LiveAdapter(FakeAdapter):
    def source_name(self):
        return "live"


def test_health_check_ok_when_live_and_no_stale(tmp_path):
    # live source + empty (never-queried) cache -> nothing to distrust.
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(LiveAdapter(), cache))
    out = _tool(server, "health_check")()
    assert out["status"] == "ok"
    assert out["source"] == "live"
    assert out["reasons"] == []


def test_health_check_degraded_when_source_not_live(tmp_path):
    # the silent-CSV-fallback case: effective source != live -> degraded.
    server = build_server(_service(tmp_path))  # FakeAdapter.source_name() -> "fake"
    out = _tool(server, "health_check")()
    assert out["status"] == "degraded"
    assert out["source"] == "fake"
    assert any("not live" in r for r in out["reasons"])


def test_health_check_stays_ok_after_get_accounts_runs(tmp_path):
    # Regression: `accounts` is an always-live (TTL 0) type that _get stores on
    # every call. cache_status must report it fresh whenever it has a successful
    # stored fetch -- regardless of the cache entry's age -- or health_check would
    # flip to degraded on a perfectly healthy live session. The original grace-
    # window fix only held for 300s after a fetch, so any real health check (e.g.
    # 11h later) still cried wolf; this now holds for an aged entry too.
    clock = _MutableClock(1000.0)
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs(), clock=clock)
    svc = DataService(LiveAdapter(), cache)
    server = build_server(svc)
    _tool(server, "get_accounts")()  # stores the always-live `accounts` type
    clock.t += 11 * 3600  # 11 hours pass with no further access
    assert {r["data_type"]: r["fresh"] for r in svc.cache_status()}["accounts"] is True
    out = _tool(server, "health_check")()
    assert out["status"] == "ok"
    assert out["reasons"] == []


def test_health_check_degraded_when_a_type_is_stale(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(LiveAdapter(), cache)
    server = build_server(svc)
    cache.store("accounts", [{"name": "Checking"}], source="live")
    cache.invalidate("accounts")  # failed-refetch leftover -> never-refreshed
    out = _tool(server, "health_check")()
    assert out["status"] == "degraded"
    assert out["source"] == "live"
    assert any("accounts is stale" in r for r in out["reasons"])


def test_get_accounts_tool(tmp_path):
    server = build_server(_service(tmp_path))
    result = _tool(server, "get_accounts")()
    accts = result["data"]
    assert accts[0]["name"] == "Checking"
    assert accts[0]["latest_balance"] == "100"


def test_cached_envelope_reports_effective_adapter_source(tmp_path):
    # source must come from the adapter actually in use, not Config.source.
    server = build_server(_service(tmp_path))  # FakeAdapter.source_name() -> "fake"
    result = _tool(server, "get_accounts")()
    assert result["source"] == "fake"


def test_live_envelope_reports_effective_adapter_source(tmp_path):
    server = build_server(_service(tmp_path))
    result = _tool(server, "recurring_summary")(start="2026-06-01", end="2026-06-30")
    assert result["source"] == "fake"
    assert result["stale"] is False  # live path


def test_refresh_cache_reports_per_type_outcome_and_source(tmp_path):
    server = build_server(_service(tmp_path))
    out = _tool(server, "refresh_cache")("transactions")
    assert out["refreshed"] == ["transactions"]
    assert out["failed"] == []
    assert out["source"] == "fake"


def test_refresh_cache_partial_failure_lists_failed(tmp_path):
    class _PartlyDown(FakeAdapter):
        def get_transactions(self):
            raise RuntimeError("monarch down")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(_PartlyDown(), cache))
    out = _tool(server, "refresh_cache")()  # all types
    assert {"type": "transactions", "error": "monarch down"} in out["failed"]
    assert "transactions" not in out["refreshed"]


def test_refresh_cache_typed_error_surfaces_as_error(tmp_path):
    from financial_mcp.monarch.community_adapter import SessionExpiredError

    class _Expired(FakeAdapter):
        def get_transactions(self):
            raise SessionExpiredError("session expired")

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(_Expired(), cache))
    out = _tool(server, "refresh_cache")("transactions")
    assert "financial-mcp-login" in out["error"]


def test_refresh_cache_unknown_type_returns_error(tmp_path):
    server = build_server(_service(tmp_path))
    out = _tool(server, "refresh_cache")("bogus")
    assert "bogus" in out["error"]


def test_get_accounts_enriches_carry_forward_and_keeps_all_accounts(tmp_path):
    # a2 has only a stale snapshot -> carry-forward; a3 has NO snapshot series and
    # must still appear (hybrid full coverage, not snapshot-only which would drop it).
    class A(FakeAdapter):
        def get_accounts(self):
            d = datetime.date(2026, 1, 2)
            return [
                Account(name="Checking", latest_balance=Decimal("100"), as_of=d, account_id="a1"),
                Account(name="Old 403b", latest_balance=Decimal("89800"), as_of=d, account_id="a2"),
                Account(name="Brand New 529", latest_balance=Decimal("200"), as_of=d, account_id="a3"),
            ]

        def get_balance_history(self):
            return [
                BalanceSnapshot(account_id="a1", display_name="Checking",
                                date=datetime.date(2026, 1, 2), balance=Decimal("100"),
                                include_in_net_worth=True),
                BalanceSnapshot(account_id="a2", display_name="Old 403b",
                                date=datetime.date(2025, 12, 1), balance=Decimal("89800"),
                                include_in_net_worth=True),
            ]

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    server = build_server(DataService(A(), cache))
    accts = {a["account_id"]: a for a in _tool(server, "get_accounts")()["data"]}
    assert set(accts) == {"a1", "a2", "a3"}  # parity: no account dropped
    assert accts["a1"]["is_carry_forward"] is False
    assert accts["a1"]["snapshot_date"] == "2026-01-02"
    assert accts["a2"]["is_carry_forward"] is True   # stale snapshot vs freshest data
    assert accts["a3"]["is_carry_forward"] is False  # zero-snapshot account, kept
    assert accts["a3"]["snapshot_date"] is None


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


class NetWorthLevelAdapter(FakeAdapter):
    """Balance snapshots where a dead 403b feed goes carry-forward.

    Freshest snapshot present is 2026-06-28. The 403b's last snapshot is weeks
    stale, so on the reference date it is carry-forward — INCLUDED in net worth
    per Monarch but a stale repeat, exactly the class of account that inflates a
    naive sum. net_worth must exclude it.
    """

    def get_balance_history(self):
        return [
            BalanceSnapshot(account_id="chk", display_name="Checking", date=datetime.date(2026, 6, 28),
                            balance=Decimal("10000.00"), type_name="depository", include_in_net_worth=True),
            BalanceSnapshot(account_id="heloc", display_name="HELOC", date=datetime.date(2026, 6, 28),
                            balance=Decimal("-2000.00"), type_name="loan", include_in_net_worth=True),
            BalanceSnapshot(account_id="403b", display_name="403b ...665Y71", date=datetime.date(2026, 5, 1),
                            balance=Decimal("500000.00"), type_name="brokerage", include_in_net_worth=True),
        ]


def _nwl_service(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    return DataService(NetWorthLevelAdapter(), cache)


def test_net_worth_tool_default_target_excludes_carry_forward(tmp_path):
    server = build_server(_nwl_service(tmp_path))
    result = _tool(server, "net_worth")()  # default target = latest snapshot present
    assert set(result) >= {"as_of", "age_hours", "stale", "count", "data"}
    data = result["data"]
    assert data["as_of"] == "2026-06-28"
    # 10000 - 2000 = 8000; the $500,000 carry-forward 403b is NOT summed.
    assert data["net_worth"] == "8000.00"           # Decimal serialized as string
    assert data["included_count"] == 2
    assert result["count"] == 2
    reasons = {r["account_id"]: r["reason"] for r in data["excluded"]}
    assert reasons["403b"] == "carry_forward"


def test_net_worth_tool_accepts_as_of_date(tmp_path):
    server = build_server(_nwl_service(tmp_path))
    result = _tool(server, "net_worth")(as_of="2026-01-01")  # before any snapshot
    data = result["data"]
    assert data["net_worth"] == "0"
    assert data["included_count"] == 0


def test_net_worth_tool_rejects_bad_date(tmp_path):
    server = build_server(_nwl_service(tmp_path))
    assert _tool(server, "net_worth")(as_of="Jan 1") == {"error": "as_of must be YYYY-MM-DD"}


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


class RetailSyncMerchantAdapter(BulkRecatAdapter):
    """The two plain BulkRecatAdapter Amazon transactions (tx1/tx2, untagged)
    plus a third Amazon transaction (tx3) tagged 'Retail Sync' — for
    exercising the retail-sync partition in rename_merchant_matching."""

    def get_transactions(self):
        tagged = Transaction(
            id="tx3",
            date=datetime.date(2026, 1, 4),
            merchant="Amazon",
            category="Uncategorized",
            account="Checking",
            original_statement="AMZN",
            notes="",
            amount=Decimal("-15.00"),
            tags=["Retail Sync"],
            owner="A",
        )
        return super().get_transactions() + [tagged]


def _retail_sync_bulk_server(tmp_path, write_mode="safe", batch_cap=200):
    from financial_mcp.cache.undo_journal import UndoJournal

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(RetailSyncMerchantAdapter(), cache)
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
# rename_merchant_matching
# ---------------------------------------------------------------------------


def test_rename_merchant_matching_off_mode_refuses(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="off")
    tool = _tool(server, "rename_merchant_matching")

    out = tool(to_merchant="Amazon Prime", merchant="Amazon")

    assert "disabled" in out["error"]


def test_rename_merchant_matching_dry_run_then_confirm(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "rename_merchant_matching")

    pv = tool(to_merchant="Amazon Prime", merchant="Amazon")
    assert pv["dry_run"] is True
    assert pv["would_change"] == 2
    assert pv["mode"] == "safe"

    out = tool(to_merchant="Amazon Prime", merchant="Amazon", confirm_token=pv["confirm_token"])
    assert out["changed"] == pv["would_change"]
    assert out["to"] == "Amazon Prime"
    assert "batch_id" in out
    assert journal.last_undoable_batch() == out["batch_id"]


def test_rename_merchant_matching_excludes_retail_sync_by_default(tmp_path):
    svc, journal, server = _retail_sync_bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "rename_merchant_matching")

    pv = tool(to_merchant="Amazon Prime", merchant="Amazon")
    assert pv["would_change"] == 2
    assert pv["skipped_retail_sync"] == 1

    pv2 = tool(to_merchant="Amazon Prime", merchant="Amazon", include_retail_sync=True)
    assert pv2["would_change"] > pv["would_change"]
    assert pv2["skipped_retail_sync"] == 0


def test_rename_merchant_matching_blank_target_refuses(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe")
    tool = _tool(server, "rename_merchant_matching")

    out = tool(to_merchant="  ", merchant="Amazon")

    assert "error" in out
    assert "to_merchant" in out["error"]


def test_rename_merchant_matching_over_cap_refuses(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="safe", batch_cap=1)
    tool = _tool(server, "rename_merchant_matching")

    out = tool(to_merchant="Amazon Prime", merchant="Amazon")

    assert "error" in out
    assert "over the batch cap" in out["error"]


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

    def delete_category(self, category_id):
        self.deleted = getattr(self, "deleted", [])
        self.deleted.append(category_id)
        return True

    def rename_transaction_merchant(self, transaction_id, merchant_name):
        return "m_" + str(transaction_id)

    def get_transaction_rules(self):
        return []

    def create_transaction_rule(self, contains, rename_to):
        return (False, None)

    def create_category_rule(self, contains, category_id):
        return (False, None)

    def delete_transaction_rule(self, rule_id):
        return False

    def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant):
        return False

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


def test_rename_category_off_mode_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="off")
    out = _tool(server, "rename_category")(name="Groceries", new_name="Food")
    assert "disabled" in out["error"]


def test_rename_category_safe_mode_renames_and_is_undoable(tmp_path):
    server, journal = _category_server(tmp_path, write_mode="safe")
    out = _tool(server, "rename_category")(name="Groceries", new_name="Food")
    assert out["renamed"] == 1
    assert out["from"] == "Groceries"
    assert out["to"] == "Food"
    assert journal.last_undoable_batch() == out["batch_id"]

    undo = _tool(server, "undo_last")()
    assert undo["reversed"] == 1
    assert undo["restored_to"] == "Groceries"


def test_rename_category_unknown_source_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="full")
    out = _tool(server, "rename_category")(name="Nope", new_name="Food")
    assert "No category named" in out["error"]


def test_rename_category_new_name_taken_refuses(tmp_path):
    server, _ = _category_server(tmp_path, write_mode="full")
    # 'Groceries' and 'Restaurants' both exist in the stub; renaming to an existing name refuses.
    out = _tool(server, "rename_category")(name="Groceries", new_name="Restaurants")
    assert "already exists" in out["error"]


# ---------------------------------------------------------------------------
# undo_last multi-level dispatch across MIXED op types (spec §9) — one journal,
# one server, a rename batch AND a recategorize batch, stepped back through
# by successive undo_last calls in seq DESC order.
# ---------------------------------------------------------------------------


class MixedOpsAdapter(CategoryCrudAdapter):
    """CategoryCrudAdapter (create/rename) plus one real transaction so the
    SAME adapter/server/journal can also exercise recategorize_transaction —
    needed to prove undo_last dispatches correctly across batches of
    DIFFERENT op types sharing one journal."""

    def get_transactions(self):
        return [
            Transaction(
                id="tx1",
                date=datetime.date(2026, 1, 2),
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

    def recategorize_transaction(self, transaction_id, category_id):
        self.recategorized = getattr(self, "recategorized", [])
        self.recategorized.append((transaction_id, category_id))
        return True


def _mixed_ops_server(tmp_path, write_mode="full", batch_cap=200):
    from financial_mcp.cache.undo_journal import UndoJournal

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    adapter = MixedOpsAdapter()
    svc = DataService(adapter, cache)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode=write_mode, batch_cap=batch_cap, journal=journal)
    return svc, journal, server


def test_undo_last_multi_level_dispatch_across_mixed_op_types(tmp_path):
    svc, journal, server = _mixed_ops_server(tmp_path)
    rename = _tool(server, "rename_category")
    recat = _tool(server, "recategorize_transaction")
    undo = _tool(server, "undo_last")

    # Batch A (older): rename_category — Groceries -> Food.
    rename_out = rename(name="Groceries", new_name="Food")
    assert rename_out["renamed"] == 1
    batch_a = rename_out["batch_id"]

    # Batch B (newer): recategorize_transaction — tx1 Groceries -> Restaurants.
    recat_out = recat(transaction_id="tx1", category="Restaurants")
    assert recat_out["changed"] == 1
    batch_b = recat_out["batch_id"]

    assert journal.last_undoable_batch() == batch_b

    # First undo_last reverses the NEWEST batch first (seq DESC) — the
    # recategorize, even though a rename op exists earlier in the journal.
    first = undo()
    assert first["batch_id"] == batch_b
    assert first["reversed"] == 1
    assert first["restored_to"] == "Groceries"
    assert svc._adapter.recategorized[-1] == ("tx1", "c1")
    assert journal.last_undoable_batch() == batch_a

    # Second undo_last reverses the OLDER batch — the rename — via the
    # correct compensating write for ITS op type.
    second = undo()
    assert second["batch_id"] == batch_a
    assert second["reversed"] == 1
    assert second["restored_to"] == "Groceries"
    assert svc._adapter.renamed[-1] == ("c1", "Groceries")
    assert journal.last_undoable_batch() is None

    # Nothing left to undo.
    third = undo()
    assert third == {"reversed": 0, "message": "nothing to undo"}


# ---------------------------------------------------------------------------
# delete_category tool (Task 6, spec §2-§3): empty-only, dependency-checked,
# gated handshake in BOTH safe and full modes, undo-by-recreate.
# ---------------------------------------------------------------------------


class DeleteNonEmptyAdapter(CategoryCrudAdapter):
    """Groceries (c1) has one transaction, so delete('Groceries') must refuse."""
    def get_transactions(self):
        return [Transaction(id="tx1", date=datetime.date(2026, 1, 2), merchant="m",
                            category="Groceries", account="a", original_statement="",
                            notes="", amount=Decimal("-1"), tags=[], owner="")]


def _delete_server(tmp_path, adapter=None, write_mode="safe"):
    from financial_mcp.cache.undo_journal import UndoJournal
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter or CategoryCrudAdapter(), cache)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode=write_mode, batch_cap=200, journal=journal)
    return svc, journal, server


def test_delete_category_off_mode_refuses(tmp_path):
    _, _, server = _delete_server(tmp_path, write_mode="off")
    out = _tool(server, "delete_category")(name="Groceries")
    assert "disabled" in out["error"]


def test_delete_category_dry_run_then_confirm(tmp_path):
    svc, journal, server = _delete_server(tmp_path, write_mode="safe")
    delete = _tool(server, "delete_category")
    preview = delete(name="Groceries")
    assert preview["dry_run"] is True
    assert preview["id"] == "c1"
    assert preview["transaction_count"] == 0
    token = preview["confirm_token"]
    done = delete(name="Groceries", confirm_token=token)
    assert done["deleted"] == 1
    assert svc._adapter.deleted == ["c1"]
    assert journal.last_undoable_batch() == done["batch_id"]


def test_delete_category_full_mode_still_requires_handshake(tmp_path):
    _, _, server = _delete_server(tmp_path, write_mode="full")
    preview = _tool(server, "delete_category")(name="Groceries")
    assert preview["dry_run"] is True   # full does NOT one-shot a delete


def test_delete_category_non_empty_refuses(tmp_path):
    _, _, server = _delete_server(tmp_path, DeleteNonEmptyAdapter(), write_mode="full")
    out = _tool(server, "delete_category")(name="Groceries")
    assert "transactions" in out["error"] and "1" in out["error"]


def test_delete_category_pending_dependency_refuses(tmp_path):
    svc, journal, server = _delete_server(tmp_path, write_mode="full")
    # A pending recat whose undo would restore INTO c1.
    journal.record("bx", "recategorize", "tx1", "c1", "c2", "Groceries", "Restaurants")
    journal.mark_succeeded("bx", "tx1")
    out = _tool(server, "delete_category")(name="Groceries")
    assert "pending undo" in out["error"]


def test_delete_category_stale_token_re_previews(tmp_path):
    _, _, server = _delete_server(tmp_path, write_mode="safe")
    out = _tool(server, "delete_category")(name="Groceries", confirm_token="wrong")
    assert out["dry_run"] is True


def test_delete_category_not_found_refuses(tmp_path):
    _, _, server = _delete_server(tmp_path, write_mode="full")
    out = _tool(server, "delete_category")(name="Nope", confirm_token="x")
    assert "No category named" in out["error"]


def test_delete_then_undo_recreates(tmp_path):
    svc, journal, server = _delete_server(tmp_path, write_mode="safe")
    delete = _tool(server, "delete_category")
    token = delete(name="Groceries")["confirm_token"]
    delete(name="Groceries", confirm_token=token)
    undo = _tool(server, "undo_last")()
    assert undo["reversed"] == 1
    # recreate called with (name, group_id, icon) — icon defaulted since stub has none
    assert svc._adapter.created[-1][0] == "Groceries"
    assert svc._adapter.created[-1][1] == "g1"
    assert journal.last_undoable_batch() is None


# ---------------------------------------------------------------------------
# undo_last multi-level dispatch across THREE mixed op types (spec §9 extended
# for Task 6): rename, recategorize, and delete in one journal, reversed
# newest-first, each dispatched to its correct compensating write.
# ---------------------------------------------------------------------------


def test_undo_last_multi_level_dispatch_across_three_op_types(tmp_path):
    svc, journal, server = _mixed_ops_server(tmp_path)
    rename = _tool(server, "rename_category")
    recat = _tool(server, "recategorize_transaction")
    delete = _tool(server, "delete_category")
    undo = _tool(server, "undo_last")

    # Batch A (oldest): rename_category — Groceries -> Food.
    rename_out = rename(name="Groceries", new_name="Food")
    assert rename_out["renamed"] == 1
    batch_a = rename_out["batch_id"]

    # Batch B (middle): recategorize_transaction — tx1 Groceries -> Restaurants.
    recat_out = recat(transaction_id="tx1", category="Restaurants")
    assert recat_out["changed"] == 1
    batch_b = recat_out["batch_id"]

    # Batch C (newest): delete_category — Restaurants (c2). The stub's
    # get_transactions() is static (always shows tx1 in "Groceries"), so
    # Restaurants stays empty regardless of the batch-B recat call; delete
    # still requires the handshake even in 'full' mode.
    preview = delete(name="Restaurants")
    assert preview["dry_run"] is True
    delete_out = delete(name="Restaurants", confirm_token=preview["confirm_token"])
    assert delete_out["deleted"] == 1
    batch_c = delete_out["batch_id"]

    assert journal.last_undoable_batch() == batch_c

    # First undo_last reverses the NEWEST batch — the delete, via recreate.
    first = undo()
    assert first["batch_id"] == batch_c
    assert first["reversed"] == 1
    assert svc._adapter.created[-1][0] == "Restaurants"
    assert svc._adapter.created[-1][1] == "g1"
    assert journal.last_undoable_batch() == batch_b

    # Second undo_last reverses the middle batch — the recategorize.
    second = undo()
    assert second["batch_id"] == batch_b
    assert second["reversed"] == 1
    assert svc._adapter.recategorized[-1] == ("tx1", "c1")
    assert journal.last_undoable_batch() == batch_a

    # Third undo_last reverses the oldest batch — the rename.
    third = undo()
    assert third["batch_id"] == batch_a
    assert third["reversed"] == 1
    assert svc._adapter.renamed[-1] == ("c1", "Groceries")
    assert journal.last_undoable_batch() is None

    # Nothing left to undo.
    fourth = undo()
    assert fourth == {"reversed": 0, "message": "nothing to undo"}


def test_undo_last_reverses_rename_merchant(tmp_path):
    svc, journal, server = _bulk_server(tmp_path, write_mode="full")
    rename = _tool(server, "rename_merchant_matching")
    undo = _tool(server, "undo_last")

    out = rename(to_merchant="Amazon Prime", merchant="Amazon")
    assert out["changed"] == 2
    batch_id = out["batch_id"]
    assert journal.last_undoable_batch() == batch_id

    result = undo()

    assert result["reversed"] >= 1
    assert result["batch_id"] == batch_id
    assert journal.last_undoable_batch() is None


# ---------------------------------------------------------------------------
# merchant_cleanup_suggestions
# ---------------------------------------------------------------------------


class MerchantCleanupAdapter(FakeAdapter):
    """A mix of transactions for merchant_cleanup_suggestions:
    - a curated 'Amazon' (has a logo + merchant_id) with two transactions,
    - an uncurated 'AMZN Mktp US*2X4' variant (no logo) that normalizes
      toward the 'Amazon' stem,
    - a Retail Sync-tagged 'Target' transaction (no logo) that should be
      reported separately, not suggested for rename.
    """

    def get_transactions(self):
        return [
            Transaction(
                id="tx1",
                date=datetime.date(2026, 1, 2),
                merchant="Amazon",
                category="Shopping",
                account="Checking",
                original_statement="AMAZON.COM",
                notes="",
                amount=Decimal("-10.00"),
                tags=[],
                owner="A",
                merchant_id="m_amazon",
                merchant_logo="https://logo.example/amazon.png",
            ),
            Transaction(
                id="tx2",
                date=datetime.date(2026, 1, 3),
                merchant="AMZN Mktp US*2X4",
                category="Shopping",
                account="Checking",
                original_statement="AMZN MKTP US*2X4",
                notes="",
                amount=Decimal("-20.00"),
                tags=[],
                owner="A",
                merchant_id="",
                merchant_logo="",
            ),
            Transaction(
                id="tx3",
                date=datetime.date(2026, 1, 4),
                merchant="Target",
                category="Shopping",
                account="Checking",
                original_statement="TARGET",
                notes="",
                amount=Decimal("-30.00"),
                tags=["Retail Sync"],
                owner="A",
                merchant_id="",
                merchant_logo="",
            ),
        ]


def _merchant_cleanup_server(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(MerchantCleanupAdapter(), cache)
    server = build_server(svc)
    return svc, server


def test_merchant_cleanup_suggestions_surfaces_uncurated(tmp_path):
    svc, server = _merchant_cleanup_server(tmp_path)
    tool = _tool(server, "merchant_cleanup_suggestions")

    out = tool()

    assert out["uncurated_count"] >= 1
    names = [s["merchant"] for s in out["suggestions"]]
    assert "AMZN Mktp US*2X4" in names
    assert "curated" not in out


def test_merchant_cleanup_suggestions_flags_retail_sync(tmp_path):
    svc, server = _merchant_cleanup_server(tmp_path)
    tool = _tool(server, "merchant_cleanup_suggestions")

    out = tool()

    assert out["retail_sync_managed"]
    assert "Target" in out["retail_sync_managed"]
    # Retail Sync-managed merchants are not offered as rename suggestions.
    names = [s["merchant"] for s in out["suggestions"]]
    assert "Target" not in names


def test_merchant_cleanup_suggestions_include_curated(tmp_path):
    svc, server = _merchant_cleanup_server(tmp_path)
    tool = _tool(server, "merchant_cleanup_suggestions")

    out_default = tool()
    assert "curated" not in out_default

    out = tool(include_curated=True)
    assert "curated" in out
    curated_names = [r["name"] for r in out["curated"]]
    assert "Amazon" in curated_names


# ---------------------------------------------------------------------------
# create_merchant_rule
# ---------------------------------------------------------------------------


class RuleAdapter(BulkRecatAdapter):
    """BulkRecatAdapter's two Amazon transactions plus an in-memory
    transaction-rules store so create/delete/list round-trip like a real
    Monarch rules API."""

    def __init__(self):
        self.rules: list[dict] = []
        self._next_id = 1

    def get_transaction_rules(self):
        return list(self.rules)

    def create_transaction_rule(self, contains, rename_to):
        rule_id = f"rule{self._next_id}"
        self._next_id += 1
        self.rules.append({"id": rule_id, "operator": "contains", "value": contains,
                            "set_merchant": rename_to, "set_category": None})
        return (True, rule_id)

    def create_category_rule(self, contains, category_id):
        rule_id = f"rule{self._next_id}"
        self._next_id += 1
        # Store category_id in set_category; the tool's dup-guard compares against
        # the resolved category NAME, so dup-guard tests pre-seed a name explicitly.
        self.rules.append({"id": rule_id, "operator": "contains", "value": contains,
                            "set_merchant": None, "set_category": category_id})
        return (True, rule_id)

    def delete_transaction_rule(self, rule_id):
        before = len(self.rules)
        self.rules = [r for r in self.rules if r["id"] != rule_id]
        return len(self.rules) < before


class DecliningRuleAdapter(RuleAdapter):
    """create_transaction_rule always reports Monarch declining the write."""

    def create_transaction_rule(self, contains, rename_to):
        return (False, None)

    def create_category_rule(self, contains, category_id):
        return (False, None)


class UncapturableIdRuleAdapter(RuleAdapter):
    """create_transaction_rule succeeds but Monarch's response didn't carry
    a usable rule id (the honest-degradation path)."""

    def create_transaction_rule(self, contains, rename_to):
        return (True, None)

    def create_category_rule(self, contains, category_id):
        return (True, None)


def _rule_server(tmp_path, adapter=None, write_mode="safe", batch_cap=200):
    from financial_mcp.cache.undo_journal import UndoJournal

    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter or RuleAdapter(), cache)
    journal = UndoJournal(str(tmp_path / "j.db"))
    server = build_server(svc, write_mode=write_mode, batch_cap=batch_cap, journal=journal)
    return svc, journal, server


def test_create_merchant_rule_off_mode_refuses(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="off")
    tool = _tool(server, "create_merchant_rule")

    out = tool("Amazon", "Amazon.com")

    assert "error" in out
    assert svc._adapter.rules == []


def test_create_merchant_rule_blank_refuses(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="safe")
    tool = _tool(server, "create_merchant_rule")

    assert "error" in tool("  ", "Amazon.com")
    assert "error" in tool("Amazon", " ")


def test_create_merchant_rule_dry_run_then_confirm(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="safe")
    tool = _tool(server, "create_merchant_rule")

    preview = tool("Amazon", "Amazon.com")

    assert preview["dry_run"] is True
    assert preview["rule"] == {"contains": "Amazon", "rename_to": "Amazon.com",
                                "apply_to_existing": False}
    # BulkRecatAdapter's two Amazon transactions match — shown, but NOT changed.
    assert preview["existing_matches"] == 2
    assert preview["message"]
    assert "future" in preview["message"].lower()
    assert "confirm_token" in preview
    assert svc._adapter.rules == []  # nothing created on preview

    out = tool("Amazon", "Amazon.com", confirm_token=preview["confirm_token"])

    assert out["created"] is True
    assert out["rule_id"]
    assert "undo_available" not in out
    assert len(svc._adapter.rules) == 1
    assert journal.last_undoable_batch() is not None


def test_create_merchant_rule_full_mode_executes_without_token(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="full")
    tool = _tool(server, "create_merchant_rule")

    out = tool("Amazon", "Amazon.com")

    assert out["created"] is True
    assert out["rule_id"]
    assert len(svc._adapter.rules) == 1


def test_create_merchant_rule_duplicate_guard_refuses(tmp_path):
    adapter = RuleAdapter()
    adapter.rules.append({"id": "rule0", "operator": "contains", "value": "Amazon",
                          "set_merchant": "Amazon.com", "set_category": None})
    svc, journal, server = _rule_server(tmp_path, adapter=adapter, write_mode="safe")
    tool = _tool(server, "create_merchant_rule")

    out = tool("Amazon", "Amazon.com")

    assert out["existing_rule_id"] == "rule0"
    assert len(svc._adapter.rules) == 1  # refused, nothing new created


def test_create_merchant_rule_declined_reports_created_false(tmp_path):
    svc, journal, server = _rule_server(tmp_path, adapter=DecliningRuleAdapter(),
                                         write_mode="full")
    tool = _tool(server, "create_merchant_rule")

    out = tool("Amazon", "Amazon.com")

    assert out["created"] is False
    assert "reason" in out
    assert journal.last_undoable_batch() is None  # no journal entry on decline


def test_create_merchant_rule_uncapturable_id_briefs_user(tmp_path):
    svc, journal, server = _rule_server(tmp_path, adapter=UncapturableIdRuleAdapter(),
                                         write_mode="full")
    tool = _tool(server, "create_merchant_rule")

    out = tool("Amazon", "Amazon.com")

    assert out["created"] is True
    assert out["rule_id"] is None
    assert out["undo_available"] is False
    assert out["note"]
    assert journal.last_undoable_batch() is None  # no journal entry when id uncapturable


def test_undo_last_reverses_create_rule(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="full")
    create = _tool(server, "create_merchant_rule")
    undo = _tool(server, "undo_last")

    out = create("Amazon", "Amazon.com")
    rule_id = out["rule_id"]
    assert rule_id in [r["id"] for r in svc._adapter.rules]

    result = undo()

    assert result["reversed"] >= 1
    assert rule_id not in [r["id"] for r in svc._adapter.rules]


def test_create_category_rule_off_mode_refuses(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="off")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert "error" in out
    assert svc._adapter.rules == []


def test_create_category_rule_blank_and_unknown_category_refuse(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="safe")
    tool = _tool(server, "create_category_rule")
    assert "error" in tool("  ", "Shopping")
    assert "error" in tool("Amazon", " ")
    # Unknown category -> fail-closed refusal, nothing created.
    out = tool("Amazon", "NoSuchCategory")
    assert "error" in out
    assert svc._adapter.rules == []


def test_create_category_rule_dry_run_then_confirm(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="safe")
    tool = _tool(server, "create_category_rule")

    preview = tool("Amazon", "Shopping")
    assert preview["dry_run"] is True
    assert preview["rule"] == {"contains": "Amazon", "set_category": "Shopping",
                               "apply_to_existing": False}
    assert preview["existing_matches"] == 2   # two Amazon txns matched, NOT changed
    assert "future" in preview["message"].lower()
    assert "confirm_token" in preview
    assert svc._adapter.rules == []            # nothing created on preview

    out = tool("Amazon", "Shopping", confirm_token=preview["confirm_token"])
    assert out["created"] is True
    assert out["rule_id"]
    assert len(svc._adapter.rules) == 1
    assert journal.last_undoable_batch() is not None


def test_create_category_rule_full_mode_executes_without_token(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="full")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert out["created"] is True
    assert out["rule_id"]
    assert len(svc._adapter.rules) == 1


def test_create_category_rule_duplicate_guard_refuses(tmp_path):
    adapter = RuleAdapter()
    adapter.rules.append({"id": "rule0", "operator": "contains", "value": "Amazon",
                          "set_merchant": None, "set_category": "Shopping"})
    svc, journal, server = _rule_server(tmp_path, adapter=adapter, write_mode="safe")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert out["existing_rule_id"] == "rule0"
    assert len(svc._adapter.rules) == 1


def test_create_category_rule_declined_reports_created_false(tmp_path):
    svc, journal, server = _rule_server(tmp_path, adapter=DecliningRuleAdapter(),
                                        write_mode="full")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert out["created"] is False
    assert "reason" in out
    assert journal.last_undoable_batch() is None


def test_create_category_rule_uncapturable_id_briefs_user(tmp_path):
    svc, journal, server = _rule_server(tmp_path, adapter=UncapturableIdRuleAdapter(),
                                        write_mode="full")
    out = _tool(server, "create_category_rule")("Amazon", "Shopping")
    assert out["created"] is True
    assert out["rule_id"] is None
    assert out["undo_available"] is False
    assert out["note"]
    assert journal.last_undoable_batch() is None


def test_undo_last_reverses_create_category_rule(tmp_path):
    svc, journal, server = _rule_server(tmp_path, write_mode="full")
    create = _tool(server, "create_category_rule")
    undo = _tool(server, "undo_last")

    out = create("Amazon", "Shopping")
    rule_id = out["rule_id"]
    assert rule_id in [r["id"] for r in svc._adapter.rules]

    result = undo()
    assert result["reversed"] >= 1
    assert rule_id not in [r["id"] for r in svc._adapter.rules]


class UpdateRuleAdapter(BulkRecatAdapter):
    """In-memory rules store with the enriched read shape + update support."""

    def __init__(self):
        self.rules: list[dict] = []

    def get_categories(self):
        from financial_mcp.monarch.models import Category
        return [
            Category(id="c7", name="Entertainment", group="Lifestyle"),
            Category(id="c8", name="Shopping", group="Lifestyle"),
        ]

    def get_transaction_rules(self):
        return [dict(r) for r in self.rules]

    def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant):
        name_by_id = {"c7": "Entertainment", "c8": "Shopping"}
        for r in self.rules:
            if r["id"] == rule_id:
                r["operator"] = operator
                r["value"] = value
                r["set_category_id"] = set_category_id
                r["set_category"] = name_by_id.get(set_category_id)
                r["set_merchant"] = set_merchant
                return True
        return False

    def delete_transaction_rule(self, rule_id):
        n = len(self.rules)
        self.rules = [r for r in self.rules if r["id"] != rule_id]
        return len(self.rules) < n


def _simple_cat_rule():
    return {"id": "r1", "operator": "contains", "value": "ubs", "order": 0,
            "set_merchant": None, "set_category": "Entertainment", "set_category_id": "c7"}


def test_update_rule_off_mode_refuses(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="off")
    out = _tool(server, "update_transaction_rule")("r1", match_text="ubs arena")
    assert "error" in out
    assert ad.rules[0]["value"] == "ubs"  # unchanged


def test_update_rule_not_found_refuses(tmp_path):
    ad = UpdateRuleAdapter()
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    out = _tool(server, "update_transaction_rule")("nope", match_text="x")
    assert "error" in out and "nope" in out["error"]


def test_update_rule_refuses_complex_rule(tmp_path):
    ad = UpdateRuleAdapter()
    ad.rules.append({**_simple_cat_rule(), "has_amount_criteria": True})
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    out = _tool(server, "update_transaction_rule")("r1", match_text="ubs arena")
    assert "refused" in out and "amount" in out["refused"]
    assert ad.rules[0]["value"] == "ubs"


def test_update_rule_no_op_refuses(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    out = _tool(server, "update_transaction_rule")("r1", match_text="ubs")  # same value
    assert "refused" in out


def test_update_rule_unresolvable_category_refuses(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    out = _tool(server, "update_transaction_rule")("r1", set_category="Nonexistent")
    assert "error" in out
    assert ad.rules[0]["value"] == "ubs"


def test_update_rule_dry_run_then_confirm(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="safe")
    tool = _tool(server, "update_transaction_rule")

    preview = tool("r1", match_text="ubs arena")
    assert preview["dry_run"] is True
    assert preview["before"]["match"] == "ubs"
    assert preview["after"]["match"] == "ubs arena"
    assert "future" in preview["message"].lower()
    assert "confirm_token" in preview
    assert ad.rules[0]["value"] == "ubs"  # nothing changed on preview

    out = tool("r1", match_text="ubs arena", confirm_token=preview["confirm_token"])
    assert out["updated"] is True
    assert ad.rules[0]["value"] == "ubs arena"
    assert journal.last_undoable_batch() is not None


def test_update_rule_change_category_full_mode(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="full")
    out = _tool(server, "update_transaction_rule")("r1", set_category="Shopping")
    assert out["updated"] is True
    assert out["after"]["category"] == "Shopping"
    assert ad.rules[0]["set_category_id"] == "c8"


def test_update_rule_failed_write_reports_and_does_not_journal(tmp_path):
    class _FailAdapter(UpdateRuleAdapter):
        def update_transaction_rule(self, *a, **k):
            return False
    ad = _FailAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="full")
    out = _tool(server, "update_transaction_rule")("r1", match_text="ubs arena")
    assert out["updated"] is False
    assert journal.last_undoable_batch() is None  # nothing journaled


def test_update_rule_undo_round_trip(tmp_path):
    ad = UpdateRuleAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="full")
    update = _tool(server, "update_transaction_rule")
    undo = _tool(server, "undo_last")

    update("r1", match_text="ubs arena", set_category="Shopping")
    assert ad.rules[0]["value"] == "ubs arena"
    assert ad.rules[0]["set_category_id"] == "c8"

    out = undo()
    assert out["reversed"] == 1 and out["failed"] == 0
    # restored to the original state
    assert ad.rules[0]["value"] == "ubs"
    assert ad.rules[0]["set_category_id"] == "c7"


def test_update_rule_undo_failure_stays_retryable(tmp_path):
    class _FailOnUndoAdapter(UpdateRuleAdapter):
        def __init__(self):
            super().__init__()
            self._allow = True
        def update_transaction_rule(self, rule_id, operator, value, set_category_id, set_merchant):
            if not self._allow:      # the compensating edit (undo) fails
                return False
            return super().update_transaction_rule(
                rule_id, operator, value, set_category_id, set_merchant)

    ad = _FailOnUndoAdapter(); ad.rules.append(_simple_cat_rule())
    svc, journal, server = _rule_server(tmp_path, adapter=ad, write_mode="full")
    _tool(server, "update_transaction_rule")("r1", match_text="ubs arena")
    ad._allow = False

    out = _tool(server, "undo_last")()
    assert out["reversed"] == 0 and out["failed"] == 1
    assert out["failures"][0]["rule_id"] == "r1"
    # still retryable: batch not closed
    assert journal.last_undoable_batch() is not None
