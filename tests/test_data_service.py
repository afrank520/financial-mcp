import datetime
from decimal import Decimal

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.config import CacheTTLs
from financial_mcp.data_service import DataService
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, Transaction


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
            date=d, merchant="Costco", category="Groceries", account="Checking",
            original_statement="COSTCO", notes="", amount=Decimal("-5.00"), tags=[], owner="Adam",
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
        return [Account(name="Checking", latest_balance=Decimal("100"), as_of=datetime.date(2026, 1, 2))]

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
