from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.config import CacheTTLs


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _cache(tmp_path, clock):
    return SqliteCache(str(tmp_path / "c.db"), CacheTTLs(transactions=100), clock=clock)


def test_store_and_load_roundtrip(tmp_path):
    clock = FakeClock()
    c = _cache(tmp_path, clock)
    c.store("transactions", [{"id": "1", "amount": "-5.00"}], source="csv")
    assert c.load("transactions") == [{"id": "1", "amount": "-5.00"}]


def test_load_empty_when_never_stored(tmp_path):
    c = _cache(tmp_path, FakeClock())
    assert c.load("transactions") == []
    assert c.age_seconds("transactions") is None


def test_freshness_respects_ttl(tmp_path):
    clock = FakeClock(t=1000.0)
    c = _cache(tmp_path, clock)
    c.store("transactions", [{"id": "1"}], source="csv")
    assert c.is_fresh("transactions") is True
    clock.t = 1099.0  # age 99 < ttl 100
    assert c.is_fresh("transactions") is True
    clock.t = 1101.0  # age 101 > ttl 100
    assert c.is_fresh("transactions") is False
    assert round(c.age_seconds("transactions")) == 101


def test_invalidate_marks_stale_but_keeps_payload(tmp_path):
    c = _cache(tmp_path, FakeClock())
    c.store("transactions", [{"id": "1"}], source="csv")
    c.invalidate("transactions")
    assert c.is_fresh("transactions") is False
    assert c.load("transactions") == [{"id": "1"}]


def test_status_lists_types(tmp_path):
    clock = FakeClock()
    c = _cache(tmp_path, clock)
    c.store("transactions", [{"id": "1"}, {"id": "2"}], source="csv")
    rows = {r["data_type"]: r for r in c.status()}
    assert rows["transactions"]["count"] == 2
    assert rows["transactions"]["source"] == "csv"
    assert rows["transactions"]["fresh"] is True


def test_persists_across_instances(tmp_path):
    clock = FakeClock()
    _cache(tmp_path, clock).store("balances", [{"account": "Checking"}], source="csv")
    reopened = SqliteCache(str(tmp_path / "c.db"), CacheTTLs(), clock=clock)
    assert reopened.load("balances") == [{"account": "Checking"}]
