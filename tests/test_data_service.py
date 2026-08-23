import datetime
from decimal import Decimal

from financial_mcp.cache.sqlite_cache import SCHEMA_VERSION, SqliteCache
from financial_mcp.config import CacheTTLs
from financial_mcp.data_service import DataService
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Cashflow, Transaction


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeAdapter(MonarchAdapter):
    def __init__(self):
        self.calls = 0
        self.account_calls = 0
        self.fail = False

    def _txn(self):
        d = datetime.date(2026, 1, 2)
        return Transaction(
            id=Transaction.synth_id(d, "Costco", Decimal("-5.00"), "COSTCO"),
            date=d,
            merchant="Costco",
            category="Groceries",
            account="Checking",
            original_statement="COSTCO",
            notes="",
            amount=Decimal("-5.00"),
            tags=[],
            owner="Adam",
        )

    def get_transactions(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("monarch down")
        return [self._txn()]

    def get_balances(self):
        return [Balance(date=datetime.date(2026, 1, 2), balance=Decimal("100"), account="Checking")]

    def get_accounts(self):
        self.account_calls += 1
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
        return []

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

    def delete_transaction_rule(self, rule_id):
        return False

    def source_name(self):
        return "fake"


def _svc(tmp_path, clock, adapter):
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs(transactions=100), clock=clock)
    return DataService(adapter, cache), cache


def test_first_read_hits_adapter_then_caches(tmp_path):
    clock = FakeClock()
    adapter = FakeAdapter()
    svc, _ = _svc(tmp_path, clock, adapter)
    assert len(svc.transactions()) == 1
    assert adapter.calls == 1
    svc.transactions()  # still fresh -> served from cache
    assert adapter.calls == 1


def test_expired_cache_refetches(tmp_path):
    clock = FakeClock(1000.0)
    adapter = FakeAdapter()
    svc, _ = _svc(tmp_path, clock, adapter)
    svc.transactions()
    clock.t = 2000.0  # age 1000 > ttl 100
    svc.transactions()
    assert adapter.calls == 2


def test_serves_stale_on_adapter_failure(tmp_path):
    clock = FakeClock(1000.0)
    adapter = FakeAdapter()
    svc, _ = _svc(tmp_path, clock, adapter)
    svc.transactions()  # populate cache
    clock.t = 2000.0
    adapter.fail = True
    txns = svc.transactions()  # stale served, no raise
    assert len(txns) == 1


def test_reraises_when_no_cache_and_adapter_fails(tmp_path):
    adapter = FakeAdapter()
    adapter.fail = True
    svc, _ = _svc(tmp_path, FakeClock(), adapter)
    try:
        svc.transactions()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_refresh_forces_refetch(tmp_path):
    clock = FakeClock()
    adapter = FakeAdapter()
    svc, _ = _svc(tmp_path, clock, adapter)
    svc.transactions()
    svc.refresh("transactions")
    assert adapter.calls == 2


def test_accounts_never_cached(tmp_path):
    clock = FakeClock()
    adapter = FakeAdapter()
    svc, _ = _svc(tmp_path, clock, adapter)
    svc.accounts()  # first call
    assert adapter.account_calls == 1
    svc.accounts()  # second call should also hit adapter
    assert adapter.account_calls == 2


class _StubAdapter(MonarchAdapter):
    def __init__(self, snaps): self._snaps = snaps; self.calls = 0
    def source_name(self): return "csv"
    def get_transactions(self): return []
    def get_balances(self): return []
    def get_accounts(self): return []
    def get_balance_history(self):
        self.calls += 1
        return self._snaps
    def get_cashflow(self, start, end):
        return Cashflow(
            start=start, end=end,
            income=Decimal("0"), expense=Decimal("0"),
            savings=Decimal("0"), savings_rate_monarch=Decimal("0"),
        )
    def get_networth_history(self): return []
    def get_categories(self): return []
    def get_budgets(self, start, end): return []
    def get_holdings(self): return []
    def get_recurring(self, start, end): return []
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
    def delete_transaction_rule(self, rule_id):
        return False


def test_balance_history_is_served_and_cached(tmp_path):
    snaps = [BalanceSnapshot(account_id="heloc", display_name="HELOC",
                             date=datetime.date(2026, 6, 28), balance=Decimal("-202143.55"),
                             type_name="loan", include_in_net_worth=True)]
    adapter = _StubAdapter(snaps)
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter, cache)
    first = svc.balance_history()
    assert first == snaps
    # second call within TTL is served from cache (adapter not hit again)
    second = svc.balance_history()
    assert second == snaps
    assert adapter.calls == 1
    # reconstructed from JSON cache exactly (Decimal + date survive round-trip)
    assert second[0].balance == Decimal("-202143.55")


def test_cachettls_has_balance_history_field():
    assert isinstance(CacheTTLs().balance_history, int)


def test_stale_schema_version_forces_live_refetch_and_restores(tmp_path):
    # Reproduces the migration defect: a warm cache entry persisted under the
    # OLD BalanceSnapshot shape ({account, date, balance}) must not be handed
    # to BalanceSnapshot(**d) -- that raises pydantic.ValidationError because
    # account_id/display_name are now required. Instead DataService must treat
    # the version mismatch as a cache miss, re-fetch live, and re-store under
    # the current schema.
    clock = FakeClock(1000.0)
    new_snap = BalanceSnapshot(
        account_id="heloc", display_name="HELOC",
        date=datetime.date(2026, 6, 28), balance=Decimal("-202143.55"),
        type_name="loan", include_in_net_worth=True,
    )
    adapter = _StubAdapter([new_snap])
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs(), clock=clock)
    # Write an old-schema row directly, bypassing store() (which always writes
    # the current SCHEMA_VERSION) -- this simulates the live, pre-migration
    # cache.db that ships with this branch's merge.
    cache._conn.execute(
        "INSERT INTO cache_entries (data_type, payload, last_refresh, source, schema_version) "
        "VALUES (?, ?, ?, ?, ?)",
        ("balance_history",
         '[{"account": "HELOC", "date": "2026-06-28", "balance": "-202143.55"}]',
         clock.t, "monarch", SCHEMA_VERSION - 1),
    )
    cache._conn.commit()

    svc = DataService(adapter, cache)
    result = svc.balance_history()  # must NOT raise pydantic.ValidationError

    assert result == [new_snap]
    assert adapter.calls == 1  # live re-fetch happened, not a stale reconstruction

    # And it was re-stored under the current schema -- a second call within
    # TTL now serves from cache without hitting the adapter again.
    svc.balance_history()
    assert adapter.calls == 1


def test_stale_fallback_reraises_on_schema_version_mismatch_instead_of_crashing(tmp_path):
    # If the live fetch also fails, DataService's stale-fallback path must not
    # hand the version-mismatched (old-shape) row to BalanceSnapshot(**d) --
    # that would crash with pydantic.ValidationError instead of the original
    # fetch error. A version-mismatched entry must be treated as "no usable
    # cache", so the original fetch exception propagates.
    clock = FakeClock(1000.0)
    adapter = _StubAdapter([])

    class _FailingAdapter(_StubAdapter):
        def get_balance_history(self):
            raise RuntimeError("monarch down")

    failing_adapter = _FailingAdapter([])
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs(), clock=clock)
    cache._conn.execute(
        "INSERT INTO cache_entries (data_type, payload, last_refresh, source, schema_version) "
        "VALUES (?, ?, ?, ?, ?)",
        ("balance_history",
         '[{"account": "HELOC", "date": "2026-06-28", "balance": "-202143.55"}]',
         clock.t, "monarch", SCHEMA_VERSION - 1),
    )
    cache._conn.commit()

    svc = DataService(failing_adapter, cache)
    try:
        svc.balance_history()
        assert False, "expected RuntimeError to propagate, not a crash on stale reconstruction"
    except RuntimeError as e:
        assert "monarch down" in str(e)


def test_networth_history_is_cached(tmp_path):
    from financial_mcp.monarch.models import NetWorthSnapshot

    class _NWAdapter(_StubAdapter):
        def __init__(self):
            super().__init__([])
            self.nw_calls = 0

        def get_networth_history(self):
            self.nw_calls += 1
            return [NetWorthSnapshot(date=datetime.date(2026, 6, 30),
                                     net_worth=Decimal("1300000.00"))]

    adapter = _NWAdapter()
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter, cache)
    first = svc.networth_history()
    second = svc.networth_history()
    assert first == second
    assert adapter.nw_calls == 1                       # served from cache
    assert second[0].net_worth == Decimal("1300000.00")  # Decimal survives round-trip


def test_categories_is_cached(tmp_path):
    from financial_mcp.monarch.models import Category

    class _CatAdapter(_StubAdapter):
        def __init__(self):
            super().__init__([])
            self.cat_calls = 0

        def get_categories(self):
            self.cat_calls += 1
            return [Category(id="c1", name="Groceries", group="Food & Dining")]

    adapter = _CatAdapter()
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter, cache)
    assert svc.categories() == svc.categories()
    assert adapter.cat_calls == 1


def test_holdings_is_cached(tmp_path):
    from financial_mcp.monarch.models import Holding

    class _HAdapter(_StubAdapter):
        def __init__(self):
            super().__init__([])
            self.holdings_calls = 0
        def get_holdings(self):
            self.holdings_calls += 1
            return [Holding(account="B", ticker="VTI", name="VTI",
                            quantity=Decimal("1"), price=Decimal("10"), value=Decimal("10"),
                            cost_basis=Decimal("8"))]

    adapter = _HAdapter()
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter, cache)
    first = svc.holdings()
    second = svc.holdings()          # within TTL -> served from cache
    assert adapter.holdings_calls == 1
    assert second[0].value == Decimal("10")   # round-trips through JSON cache


def test_delete_category_invalidates_categories_cache(tmp_path):
    from financial_mcp.monarch.models import Category

    class _DelAdapter(_StubAdapter):
        def __init__(self):
            super().__init__([])
            self.cat_calls = 0
            self.deleted = []

        def get_categories(self):
            self.cat_calls += 1
            return [Category(id="c1", name="Groceries", group="Food & Dining")]

        def delete_category(self, category_id):
            self.deleted.append(category_id)
            return True

    adapter = _DelAdapter()
    cache = SqliteCache(str(tmp_path / "c.db"), CacheTTLs())
    svc = DataService(adapter, cache)

    svc.categories()  # populate cache
    assert adapter.cat_calls == 1

    assert svc.delete_category("c1") is True
    assert adapter.deleted == ["c1"]

    svc.categories()  # cache must have been invalidated -> refetch, not stale-served
    assert adapter.cat_calls == 2


def test_cachettls_holdings_is_one_hour():
    from financial_mcp.config import HOUR
    assert CacheTTLs().holdings == 1 * HOUR


def test_rename_merchant_invalidates_transactions_cache(tmp_path):
    class _RenameAdapter(FakeAdapter):
        def rename_transaction_merchant(self, transaction_id, merchant_name):
            return "m9"

    clock = FakeClock()
    adapter = _RenameAdapter()
    svc, _ = _svc(tmp_path, clock, adapter)

    svc.transactions()  # populate cache
    assert adapter.calls == 1

    assert svc.rename_merchant("t1", "Amazon") == "m9"

    svc.transactions()  # cache must have been invalidated -> refetch, not stale-served
    assert adapter.calls == 2


def test_transaction_rules_passthrough(tmp_path):
    class _RulesAdapter(FakeAdapter):
        def get_transaction_rules(self):
            return [{"id": "r1", "operator": "contains", "value": "amzn",
                     "set_merchant": "Amazon", "set_category": None}]

    clock = FakeClock()
    adapter = _RulesAdapter()
    svc, _ = _svc(tmp_path, clock, adapter)

    assert svc.transaction_rules() == [{"id": "r1", "operator": "contains", "value": "amzn",
                                         "set_merchant": "Amazon", "set_category": None}]
