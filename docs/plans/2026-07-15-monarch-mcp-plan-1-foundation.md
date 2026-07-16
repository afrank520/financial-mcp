# Monarch MCP — Plan 1: Foundation & CSV-Backed Read Server

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a local MCP server the Claude Desktop app can talk to, answering finance questions over the user's exported Monarch CSVs — with zero credential risk and a clean TDD surface.

**Architecture:** A single local Python process exposes MCP tools over stdio. Tools contain no business logic: they call a `MonarchAdapter` (abstract interface) for data and pure analytics functions for computation. In this plan the only adapter implementation is `CsvAdapter`, which loads the two CSV exports into Pydantic models. A SQLite cache layer with per-type TTLs sits between the adapter and the tools. Live Monarch auth (Plan 2), deeper analytics (Plan 3), and writes (Plan 4) are additive and land behind the same interfaces.

**Tech Stack:** Python 3.12+, `mcp` (official SDK, `FastMCP`), Pydantic v2, SQLite (stdlib `sqlite3`), PyYAML, `keyring` (declared now, used in Plan 2), pytest, Ruff, Black.

## Global Constraints

- Python **3.12+**; modern typing (`X | None`, `list[X]`), no `typing.Optional`.
- Pydantic **v2** models for every Monarch entity shape.
- **No secrets in any committed file.** `config.yaml` is git-ignored; only `config.example.yaml` is committed.
- **Never log** credentials, session tokens, account numbers, or PII — sensitive fields are masked.
- Real CSV exports are **read copy-only**: never modified, never committed. Only a small anonymized slice lives in `tests/fixtures/`.
- SQLite cache DB is git-ignored.
- Per-type cache TTLs (defaults): transactions/balances 12h, investments/holdings 12h, budgets/networth/cashflow 24h, categories/merchants 7d.
- Standalone git history in `Monarch MCP/` — clean commit per task.
- Test coverage target ≥90% on analytics and cache/guardrail logic.
- CSV schemas (exact headers):
  - Balances: `Date,Balance,Account`
  - Transactions: `Date,Merchant,Category,Account,Original Statement,Notes,Amount,Tags,Owner`

---

### Task 1: Project scaffold & tooling

**Files:**
- Create: `pyproject.toml`
- Create: `financial_mcp/__init__.py`
- Create: `financial_mcp/version.py`
- Create: `tests/__init__.py`
- Create: `tests/test_version.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `financial_mcp.version.__version__: str`; installable package `financial_mcp`; working `pytest`/`ruff`/`black`.

- [ ] **Step 1: Write the failing test**

`tests/test_version.py`:
```python
from financial_mcp.version import __version__


def test_version_is_semver_string():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_version.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp'`

- [ ] **Step 3: Create the package and config**

`financial_mcp/__init__.py`:
```python
```
(empty file)

`financial_mcp/version.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`:
```python
```
(empty file)

`pyproject.toml`:
```toml
[project]
name = "financial-mcp"
version = "0.1.0"
description = "Local MCP server for Monarch Money (personal use)"
requires-python = ">=3.12"
dependencies = [
    "mcp>=1.2.0",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "keyring>=25.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "black>=24.0",
]

[project.scripts]
financial-mcp = "financial_mcp.main:main"
financial-mcp-login = "financial_mcp.login_cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["financial_mcp*"]

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["tests"]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.black]
target-version = ["py312"]
line-length = 100
```

- [ ] **Step 4: Install and run the test**

Run: `python -m pip install -e ".[dev]" && python -m pytest tests/test_version.py -v`
Expected: PASS

Note: the `[project.scripts]` reference `financial_mcp.main` / `financial_mcp.login_cli` do not exist yet. `pip install -e` does not import them, so this succeeds. They are created in Task 9 (main) and Plan 2 (login_cli).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml financial_mcp/ tests/
git commit -m "feat: project scaffold, tooling, version module"
```

---

### Task 2: Config loader

**Files:**
- Create: `financial_mcp/config.py`
- Create: `config.example.yaml`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `financial_mcp.config.CacheTTLs` (Pydantic model): fields `transactions:int, balances:int, investments:int, holdings:int, budgets:int, networth:int, cashflow:int, categories:int, merchants:int` — all TTLs in **seconds**.
  - `financial_mcp.config.Config` (Pydantic model): `db_path:str`, `log_level:str`, `log_file:str | None`, `write_mode:str` (`"off"|"safe"|"full"`), `batch_cap:int`, `cache_ttls:CacheTTLs`, `csv_transactions_path:str | None`, `csv_balances_path:str | None`.
  - `financial_mcp.config.load_config(path:str | None = None) -> Config` — loads YAML, applies defaults for any missing key. If `path` is None, defaults only.

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
import textwrap

from financial_mcp.config import Config, load_config


def test_defaults_when_no_file():
    cfg = load_config(None)
    assert isinstance(cfg, Config)
    assert cfg.write_mode == "safe"
    assert cfg.batch_cap == 200
    assert cfg.cache_ttls.transactions == 12 * 3600
    assert cfg.cache_ttls.categories == 7 * 24 * 3600


def test_yaml_overrides_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        textwrap.dedent(
            """
            write_mode: off
            batch_cap: 50
            cache_ttls:
              transactions: 3600
            """
        )
    )
    cfg = load_config(str(p))
    assert cfg.write_mode == "off"
    assert cfg.batch_cap == 50
    assert cfg.cache_ttls.transactions == 3600
    # untouched TTL keeps its default
    assert cfg.cache_ttls.balances == 12 * 3600


def test_invalid_write_mode_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("write_mode: wideopen\n")
    try:
        load_config(str(p))
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.config'`

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/config.py`:
```python
from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError

HOUR = 3600
DAY = 24 * HOUR


class CacheTTLs(BaseModel):
    transactions: int = 12 * HOUR
    balances: int = 12 * HOUR
    investments: int = 12 * HOUR
    holdings: int = 12 * HOUR
    budgets: int = DAY
    networth: int = DAY
    cashflow: int = DAY
    categories: int = 7 * DAY
    merchants: int = 7 * DAY


class Config(BaseModel):
    db_path: str = "cache.db"
    log_level: str = "INFO"
    log_file: str | None = None
    write_mode: Literal["off", "safe", "full"] = "safe"
    batch_cap: int = 200
    cache_ttls: CacheTTLs = CacheTTLs()
    csv_transactions_path: str | None = None
    csv_balances_path: str | None = None


def load_config(path: str | None = None) -> Config:
    raw: dict = {}
    if path:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    try:
        return Config(**raw)
    except ValidationError as exc:
        raise ValueError(f"invalid config: {exc}") from exc
```

- [ ] **Step 4: Create the committed example config**

`config.example.yaml`:
```yaml
# Copy to config.yaml (git-ignored) and edit. NEVER put secrets here —
# credentials live in the OS keyring, set via `financial-mcp-login`.

db_path: cache.db
log_level: INFO
log_file: financial_mcp.log      # omit or null for stderr only

# off = reads only | safe = reads + reversible writes | full = + destructive
write_mode: safe
batch_cap: 200

# Cache freshness in SECONDS, per data type.
cache_ttls:
  transactions: 43200   # 12h
  balances: 43200       # 12h
  investments: 43200    # 12h
  holdings: 43200       # 12h
  budgets: 86400        # 24h
  networth: 86400       # 24h
  cashflow: 86400       # 24h
  categories: 604800    # 7d
  merchants: 604800     # 7d

# Offline CSV fallback (Plan 1). Point at copies of your Monarch exports.
csv_transactions_path: null
csv_balances_path: null
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/config.py config.example.yaml tests/test_config.py
git commit -m "feat: YAML config loader with per-type TTL defaults"
```

---

### Task 3: Logging with sensitive-field masking

**Files:**
- Create: `financial_mcp/utils/__init__.py`
- Create: `financial_mcp/utils/logging.py`
- Create: `tests/test_logging.py`

**Interfaces:**
- Consumes: `Config` (for `log_level`, `log_file`).
- Produces:
  - `financial_mcp.utils.logging.mask(value:str) -> str` — masks all but the last 4 chars of a sensitive value (e.g. account number/token); strings ≤4 chars become `"****"`.
  - `financial_mcp.utils.logging.mask_mapping(d:dict, sensitive_keys:set[str]) -> dict` — returns a copy with values of any key in `sensitive_keys` (case-insensitive) replaced by `mask(str(value))`.
  - `financial_mcp.utils.logging.configure_logging(cfg:Config) -> logging.Logger` — returns a configured logger named `"financial_mcp"`.

- [ ] **Step 1: Write the failing test**

`tests/test_logging.py`:
```python
from financial_mcp.config import Config
from financial_mcp.utils.logging import configure_logging, mask, mask_mapping


def test_mask_keeps_last_four():
    assert mask("1234567890") == "******7890"


def test_mask_short_value():
    assert mask("abc") == "****"


def test_mask_mapping_is_case_insensitive_and_nondestructive():
    src = {"Token": "supersecretvalue", "amount": 42}
    out = mask_mapping(src, {"token"})
    assert out["Token"] == "************alue"
    assert out["amount"] == 42
    assert src["Token"] == "supersecretvalue"  # original untouched


def test_configure_logging_returns_named_logger():
    logger = configure_logging(Config(log_level="DEBUG", log_file=None))
    assert logger.name == "financial_mcp"
    assert logger.level == 10  # DEBUG
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.utils'`

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/utils/__init__.py`:
```python
```
(empty file)

`financial_mcp/utils/logging.py`:
```python
from __future__ import annotations

import logging
import sys

from financial_mcp.config import Config


def mask(value: str) -> str:
    s = str(value)
    if len(s) <= 4:
        return "****"
    return "*" * (len(s) - 4) + s[-4:]


def mask_mapping(d: dict, sensitive_keys: set[str]) -> dict:
    lowered = {k.lower() for k in sensitive_keys}
    return {
        k: (mask(str(v)) if k.lower() in lowered else v) for k, v in d.items()
    }


def configure_logging(cfg: Config) -> logging.Logger:
    logger = logging.getLogger("financial_mcp")
    logger.handlers.clear()
    logger.setLevel(cfg.log_level.upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    logger.addHandler(stderr_handler)

    if cfg.log_file:
        file_handler = logging.FileHandler(cfg.log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_logging.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/utils/ tests/test_logging.py
git commit -m "feat: structured logging with sensitive-field masking"
```

---

### Task 4: Pydantic models for Monarch entities

**Files:**
- Create: `financial_mcp/monarch/__init__.py`
- Create: `financial_mcp/monarch/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces (all Pydantic v2 `BaseModel`):
  - `Transaction`: `id:str`, `date:datetime.date`, `merchant:str`, `category:str`, `account:str`, `original_statement:str`, `notes:str`, `amount:Decimal`, `tags:list[str]`, `owner:str`.
  - `Balance`: `date:datetime.date`, `balance:Decimal`, `account:str`.
  - `Account`: `name:str`, `latest_balance:Decimal`, `as_of:datetime.date`.
  - Helper `Transaction.synth_id(date, merchant, amount, original_statement) -> str` — stable SHA-1-based id (CSV has no id column). Returns the hex digest (40 chars).

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
import datetime
from decimal import Decimal

from financial_mcp.monarch.models import Account, Balance, Transaction


def test_transaction_synth_id_is_stable_and_hex():
    a = Transaction.synth_id(datetime.date(2026, 1, 2), "Costco", Decimal("-52.10"), "COSTCO #123")
    b = Transaction.synth_id(datetime.date(2026, 1, 2), "Costco", Decimal("-52.10"), "COSTCO #123")
    assert a == b
    assert len(a) == 40
    c = Transaction.synth_id(datetime.date(2026, 1, 2), "Costco", Decimal("-52.11"), "COSTCO #123")
    assert a != c


def test_transaction_construction():
    txn = Transaction(
        id="x",
        date=datetime.date(2026, 1, 2),
        merchant="Costco",
        category="Groceries",
        account="Checking",
        original_statement="COSTCO #123",
        notes="",
        amount=Decimal("-52.10"),
        tags=["food"],
        owner="Adam",
    )
    assert txn.amount == Decimal("-52.10")
    assert txn.tags == ["food"]


def test_balance_and_account():
    bal = Balance(date=datetime.date(2026, 1, 2), balance=Decimal("1000.00"), account="Checking")
    acct = Account(name="Checking", latest_balance=Decimal("1000.00"), as_of=datetime.date(2026, 1, 2))
    assert bal.account == acct.name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.monarch'`

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/monarch/__init__.py`:
```python
```
(empty file)

`financial_mcp/monarch/models.py`:
```python
from __future__ import annotations

import datetime
import hashlib
from decimal import Decimal

from pydantic import BaseModel


class Transaction(BaseModel):
    id: str
    date: datetime.date
    merchant: str
    category: str
    account: str
    original_statement: str
    notes: str
    amount: Decimal
    tags: list[str]
    owner: str

    @staticmethod
    def synth_id(
        date: datetime.date, merchant: str, amount: Decimal, original_statement: str
    ) -> str:
        key = f"{date.isoformat()}|{merchant}|{amount}|{original_statement}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()


class Balance(BaseModel):
    date: datetime.date
    balance: Decimal
    account: str


class Account(BaseModel):
    name: str
    latest_balance: Decimal
    as_of: datetime.date
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/__init__.py financial_mcp/monarch/models.py tests/test_models.py
git commit -m "feat: Pydantic models for transactions, balances, accounts"
```

---

### Task 5: Adapter interface

**Files:**
- Create: `financial_mcp/monarch/adapter.py`
- Create: `tests/test_adapter_interface.py`

**Interfaces:**
- Consumes: `Transaction`, `Balance`, `Account` from Task 4.
- Produces: `financial_mcp.monarch.adapter.MonarchAdapter` — an `abc.ABC` defining the read contract every adapter (CSV now, community later) implements:
  - `get_transactions(self) -> list[Transaction]`
  - `get_balances(self) -> list[Balance]`
  - `get_accounts(self) -> list[Account]`
  - `source_name(self) -> str` — short label for cache provenance (e.g. `"csv"`).

- [ ] **Step 1: Write the failing test**

`tests/test_adapter_interface.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_adapter_interface.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.monarch.adapter'`

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/monarch/adapter.py`:
```python
from __future__ import annotations

import abc

from financial_mcp.monarch.models import Account, Balance, Transaction


class MonarchAdapter(abc.ABC):
    """Read contract the rest of the app depends on. Never references a
    concrete Monarch library — implementations are swappable (CSV, community)."""

    @abc.abstractmethod
    def get_transactions(self) -> list[Transaction]: ...

    @abc.abstractmethod
    def get_balances(self) -> list[Balance]: ...

    @abc.abstractmethod
    def get_accounts(self) -> list[Account]: ...

    @abc.abstractmethod
    def source_name(self) -> str: ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_adapter_interface.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/monarch/adapter.py tests/test_adapter_interface.py
git commit -m "feat: MonarchAdapter abstract read interface"
```

---

### Task 6: CSV adapter

**Files:**
- Create: `financial_mcp/monarch/csv_adapter.py`
- Create: `tests/fixtures/transactions_sample.csv`
- Create: `tests/fixtures/balances_sample.csv`
- Create: `tests/test_csv_adapter.py`

**Interfaces:**
- Consumes: `MonarchAdapter` (Task 5), models (Task 4).
- Produces: `financial_mcp.monarch.csv_adapter.CsvAdapter(transactions_path:str, balances_path:str)` implementing `MonarchAdapter`.
  - Parses Transactions header `Date,Merchant,Category,Account,Original Statement,Notes,Amount,Tags,Owner`. `Tags` splits on `,` (empty → `[]`). `Amount` → `Decimal`. `id` via `Transaction.synth_id`.
  - Parses Balances header `Date,Balance,Account`.
  - `get_accounts()` derives one `Account` per distinct balance account, using its most-recent-date balance row.
  - `source_name()` returns `"csv"`.

- [ ] **Step 1: Create anonymized fixtures**

`tests/fixtures/transactions_sample.csv`:
```csv
Date,Merchant,Category,Account,Original Statement,Notes,Amount,Tags,Owner
2026-01-02,Costco,Groceries,Checking,COSTCO WHSE #123,weekly run,-152.10,"food,bulk",Adam
2026-01-03,Paycheck,Income,Checking,ACME PAYROLL,,2500.00,,Adam
2026-01-05,Netflix,Entertainment,Credit Card,NETFLIX.COM,,-15.99,subscription,Adam
2026-01-10,Costco,Groceries,Checking,COSTCO WHSE #123,,-88.40,food,Adam
```

`tests/fixtures/balances_sample.csv`:
```csv
Date,Balance,Account
2026-01-01,1000.00,Checking
2026-01-31,3200.00,Checking
2026-01-31,-450.00,Credit Card
```

- [ ] **Step 2: Write the failing test**

`tests/test_csv_adapter.py`:
```python
import datetime
from decimal import Decimal
from pathlib import Path

from financial_mcp.monarch.csv_adapter import CsvAdapter

FIX = Path(__file__).parent / "fixtures"


def _adapter():
    return CsvAdapter(str(FIX / "transactions_sample.csv"), str(FIX / "balances_sample.csv"))


def test_source_name():
    assert _adapter().source_name() == "csv"


def test_transactions_parsed():
    txns = _adapter().get_transactions()
    assert len(txns) == 4
    first = txns[0]
    assert first.merchant == "Costco"
    assert first.amount == Decimal("-152.10")
    assert first.tags == ["food", "bulk"]
    assert first.date == datetime.date(2026, 1, 2)
    # empty tags -> []
    paycheck = next(t for t in txns if t.merchant == "Paycheck")
    assert paycheck.tags == []


def test_balances_parsed():
    bals = _adapter().get_balances()
    assert len(bals) == 3
    assert any(b.account == "Credit Card" and b.balance == Decimal("-450.00") for b in bals)


def test_accounts_use_latest_balance():
    accts = {a.name: a for a in _adapter().get_accounts()}
    assert accts["Checking"].latest_balance == Decimal("3200.00")
    assert accts["Checking"].as_of == datetime.date(2026, 1, 31)
    assert accts["Credit Card"].latest_balance == Decimal("-450.00")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_csv_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.monarch.csv_adapter'`

- [ ] **Step 4: Write minimal implementation**

`financial_mcp/monarch/csv_adapter.py`:
```python
from __future__ import annotations

import csv
import datetime
from decimal import Decimal

from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, Transaction


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s.strip())


def _parse_tags(s: str) -> list[str]:
    return [t.strip() for t in s.split(",") if t.strip()]


class CsvAdapter(MonarchAdapter):
    def __init__(self, transactions_path: str, balances_path: str) -> None:
        self._transactions_path = transactions_path
        self._balances_path = balances_path

    def source_name(self) -> str:
        return "csv"

    def get_transactions(self) -> list[Transaction]:
        out: list[Transaction] = []
        with open(self._transactions_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                date = _parse_date(row["Date"])
                merchant = row["Merchant"].strip()
                amount = Decimal(row["Amount"].strip() or "0")
                original = row["Original Statement"].strip()
                out.append(
                    Transaction(
                        id=Transaction.synth_id(date, merchant, amount, original),
                        date=date,
                        merchant=merchant,
                        category=row["Category"].strip(),
                        account=row["Account"].strip(),
                        original_statement=original,
                        notes=row["Notes"].strip(),
                        amount=amount,
                        tags=_parse_tags(row["Tags"]),
                        owner=row["Owner"].strip(),
                    )
                )
        return out

    def get_balances(self) -> list[Balance]:
        out: list[Balance] = []
        with open(self._balances_path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                out.append(
                    Balance(
                        date=_parse_date(row["Date"]),
                        balance=Decimal(row["Balance"].strip() or "0"),
                        account=row["Account"].strip(),
                    )
                )
        return out

    def get_accounts(self) -> list[Account]:
        latest: dict[str, Balance] = {}
        for bal in self.get_balances():
            cur = latest.get(bal.account)
            if cur is None or bal.date > cur.date:
                latest[bal.account] = bal
        return [
            Account(name=name, latest_balance=b.balance, as_of=b.date)
            for name, b in sorted(latest.items())
        ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_csv_adapter.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/monarch/csv_adapter.py tests/fixtures/ tests/test_csv_adapter.py
git commit -m "feat: CSV adapter parsing Monarch transaction and balance exports"
```

---

### Task 7: SQLite cache with per-type TTL

**Files:**
- Create: `financial_mcp/cache/__init__.py`
- Create: `financial_mcp/cache/sqlite_cache.py`
- Create: `tests/test_sqlite_cache.py`

**Interfaces:**
- Consumes: `CacheTTLs` (Task 2).
- Produces: `financial_mcp.cache.sqlite_cache.SqliteCache(db_path:str, ttls:CacheTTLs, clock:Callable[[], float] = time.time)`.
  - `store(self, data_type:str, payload:list[dict], source:str) -> None` — replaces all rows for `data_type`, stamping refresh time (from `clock`), `source`, and `schema_version=1`. `payload` items must be JSON-serializable dicts.
  - `load(self, data_type:str) -> list[dict]` — returns stored payload (empty list if never stored).
  - `is_fresh(self, data_type:str) -> bool` — True if `now - last_refresh < ttls[data_type]`.
  - `age_seconds(self, data_type:str) -> float | None` — `now - last_refresh`, or None if never stored.
  - `status(self) -> list[dict]` — one dict per stored type: `{"data_type","age_seconds","fresh","source","count"}`.
  - `invalidate(self, data_type:str) -> None` — marks a type stale (sets last_refresh to 0) without deleting payload.
  - `clock` is injectable so tests control time deterministically.

- [ ] **Step 1: Write the failing test**

`tests/test_sqlite_cache.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sqlite_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.cache'`

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/cache/__init__.py`:
```python
```
(empty file)

`financial_mcp/cache/sqlite_cache.py`:
```python
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable

from financial_mcp.config import CacheTTLs

SCHEMA_VERSION = 1


class SqliteCache:
    def __init__(
        self,
        db_path: str,
        ttls: CacheTTLs,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db_path = db_path
        self._ttls = ttls
        self._clock = clock
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_entries (
                data_type TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                last_refresh REAL NOT NULL,
                source TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()

    def _ttl_for(self, data_type: str) -> int:
        return getattr(self._ttls, data_type, 0)

    def store(self, data_type: str, payload: list[dict], source: str) -> None:
        self._conn.execute(
            """
            INSERT INTO cache_entries
                (data_type, payload, last_refresh, source, schema_version)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(data_type) DO UPDATE SET
                payload=excluded.payload,
                last_refresh=excluded.last_refresh,
                source=excluded.source,
                schema_version=excluded.schema_version
            """,
            (data_type, json.dumps(payload), self._clock(), source, SCHEMA_VERSION),
        )
        self._conn.commit()

    def load(self, data_type: str) -> list[dict]:
        row = self._conn.execute(
            "SELECT payload FROM cache_entries WHERE data_type=?", (data_type,)
        ).fetchone()
        return json.loads(row[0]) if row else []

    def _last_refresh(self, data_type: str) -> float | None:
        row = self._conn.execute(
            "SELECT last_refresh FROM cache_entries WHERE data_type=?", (data_type,)
        ).fetchone()
        return row[0] if row else None

    def age_seconds(self, data_type: str) -> float | None:
        lr = self._last_refresh(data_type)
        return None if lr is None else self._clock() - lr

    def is_fresh(self, data_type: str) -> bool:
        age = self.age_seconds(data_type)
        if age is None:
            return False
        return age < self._ttl_for(data_type)

    def invalidate(self, data_type: str) -> None:
        self._conn.execute(
            "UPDATE cache_entries SET last_refresh=0 WHERE data_type=?", (data_type,)
        )
        self._conn.commit()

    def status(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data_type, payload, source FROM cache_entries ORDER BY data_type"
        ).fetchall()
        out: list[dict] = []
        for data_type, payload, source in rows:
            out.append(
                {
                    "data_type": data_type,
                    "age_seconds": self.age_seconds(data_type),
                    "fresh": self.is_fresh(data_type),
                    "source": source,
                    "count": len(json.loads(payload)),
                }
            )
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sqlite_cache.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/cache/ tests/test_sqlite_cache.py
git commit -m "feat: SQLite cache with per-type TTL freshness and status"
```

---

### Task 8: Spending analytics (pure functions)

**Files:**
- Create: `financial_mcp/analytics/__init__.py`
- Create: `financial_mcp/analytics/spending.py`
- Create: `tests/test_spending.py`

**Interfaces:**
- Consumes: `Transaction` (Task 4).
- Produces (pure functions over `list[Transaction]`, no I/O):
  - `category_summary(txns) -> list[dict]` — `[{"category","total","count"}]`, `total` = sum of amounts (Decimal), sorted by `total` ascending (most-negative/biggest spend first).
  - `merchant_summary(txns) -> list[dict]` — same shape keyed by `"merchant"`.
  - `monthly_spending(txns, year:int) -> list[dict]` — `[{"month":1..12,"total"}]` summing only **negative** amounts (spend) for that year, months with activity only, ascending month.
  - `search(txns, *, merchant:str|None=None, category:str|None=None, min_amount:Decimal|None=None, max_amount:Decimal|None=None) -> list[Transaction]` — case-insensitive substring match on merchant/category; inclusive amount bounds.

- [ ] **Step 1: Write the failing test**

`tests/test_spending.py`:
```python
import datetime
from decimal import Decimal

from financial_mcp.analytics.spending import (
    category_summary,
    merchant_summary,
    monthly_spending,
    search,
)
from financial_mcp.monarch.models import Transaction


def _txn(merchant, category, amount, month=1, day=1, year=2026):
    d = datetime.date(year, month, day)
    return Transaction(
        id=Transaction.synth_id(d, merchant, Decimal(amount), merchant),
        date=d, merchant=merchant, category=category, account="Checking",
        original_statement=merchant, notes="", amount=Decimal(amount), tags=[], owner="Adam",
    )


TXNS = [
    _txn("Costco", "Groceries", "-152.10", month=1),
    _txn("Costco", "Groceries", "-88.40", month=2),
    _txn("Netflix", "Entertainment", "-15.99", month=1),
    _txn("Paycheck", "Income", "2500.00", month=1),
]


def test_category_summary_sorted_biggest_spend_first():
    rows = category_summary(TXNS)
    assert rows[0]["category"] == "Groceries"
    assert rows[0]["total"] == Decimal("-240.50")
    assert rows[0]["count"] == 2
    assert rows[-1]["category"] == "Income"


def test_merchant_summary():
    rows = {r["merchant"]: r for r in merchant_summary(TXNS)}
    assert rows["Costco"]["total"] == Decimal("-240.50")
    assert rows["Netflix"]["count"] == 1


def test_monthly_spending_negative_only():
    rows = {r["month"]: r["total"] for r in monthly_spending(TXNS, 2026)}
    assert rows[1] == Decimal("-168.09")  # -152.10 + -15.99 (paycheck excluded)
    assert rows[2] == Decimal("-88.40")
    assert set(rows.keys()) == {1, 2}


def test_search_filters():
    assert len(search(TXNS, merchant="cost")) == 2
    assert len(search(TXNS, category="income")) == 1
    assert len(search(TXNS, min_amount=Decimal("0"))) == 1  # only paycheck >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_spending.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.analytics'`

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/analytics/__init__.py`:
```python
```
(empty file)

`financial_mcp/analytics/spending.py`:
```python
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from financial_mcp.monarch.models import Transaction


def _group_totals(txns: list[Transaction], key: str) -> list[dict]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for t in txns:
        k = getattr(t, key)
        totals[k] += t.amount
        counts[k] += 1
    rows = [{key: k, "total": totals[k], "count": counts[k]} for k in totals]
    rows.sort(key=lambda r: r["total"])
    return rows


def category_summary(txns: list[Transaction]) -> list[dict]:
    return _group_totals(txns, "category")


def merchant_summary(txns: list[Transaction]) -> list[dict]:
    return _group_totals(txns, "merchant")


def monthly_spending(txns: list[Transaction], year: int) -> list[dict]:
    totals: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for t in txns:
        if t.date.year == year and t.amount < 0:
            totals[t.date.month] += t.amount
    return [{"month": m, "total": totals[m]} for m in sorted(totals)]


def search(
    txns: list[Transaction],
    *,
    merchant: str | None = None,
    category: str | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
) -> list[Transaction]:
    result = txns
    if merchant is not None:
        m = merchant.lower()
        result = [t for t in result if m in t.merchant.lower()]
    if category is not None:
        c = category.lower()
        result = [t for t in result if c in t.category.lower()]
    if min_amount is not None:
        result = [t for t in result if t.amount >= min_amount]
    if max_amount is not None:
        result = [t for t in result if t.amount <= max_amount]
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_spending.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/analytics/ tests/test_spending.py
git commit -m "feat: spending analytics (category, merchant, monthly, search)"
```

---

### Task 9: Data service — adapter + cache glue

**Files:**
- Create: `financial_mcp/data_service.py`
- Create: `tests/test_data_service.py`

**Interfaces:**
- Consumes: `MonarchAdapter` (Task 5), `SqliteCache` (Task 7), models (Task 4).
- Produces: `financial_mcp.data_service.DataService(adapter:MonarchAdapter, cache:SqliteCache)`.
  - `transactions(self) -> list[Transaction]` — if cache fresh for `"transactions"`, deserialize from cache; else pull from adapter, store to cache, return. On adapter error, serve stale cache if present (log a warning) else re-raise.
  - `balances(self) -> list[Balance]` / `accounts(self) -> list[Account]` — same pattern for `"balances"` / `"accounts"`. (`accounts` uses no TTL of its own — it is derived; cache under `"balances"` freshness by refreshing balances.)
  - `refresh(self, data_type:str | None = None) -> None` — force-invalidate then reload one type, or all if None.
  - `cache_status(self) -> list[dict]` — passthrough to `cache.status()`.
  - Serialization: models → dict via `model.model_dump(mode="json")`; dict → model via `Model(**d)`.
  - Note: `"accounts"` is not a `CacheTTLs` field, so `is_fresh("accounts")` is always False → accounts always re-derived from the adapter. That is intentional and cheap.

- [ ] **Step 1: Write the failing test**

`tests/test_data_service.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_data_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.data_service'`

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/data_service.py`:
```python
from __future__ import annotations

import logging

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, Transaction

logger = logging.getLogger("financial_mcp")


class DataService:
    def __init__(self, adapter: MonarchAdapter, cache: SqliteCache) -> None:
        self._adapter = adapter
        self._cache = cache

    def _get(self, data_type, fetch, model):
        if self._cache.is_fresh(data_type):
            return [model(**d) for d in self._cache.load(data_type)]
        try:
            items = fetch()
        except Exception:
            cached = self._cache.load(data_type)
            if cached:
                logger.warning("adapter failed for %s; serving stale cache", data_type)
                return [model(**d) for d in cached]
            raise
        self._cache.store(
            data_type,
            [m.model_dump(mode="json") for m in items],
            source=self._adapter.source_name(),
        )
        return items

    def transactions(self) -> list[Transaction]:
        return self._get("transactions", self._adapter.get_transactions, Transaction)

    def balances(self) -> list[Balance]:
        return self._get("balances", self._adapter.get_balances, Balance)

    def accounts(self) -> list[Account]:
        return self._get("accounts", self._adapter.get_accounts, Account)

    def refresh(self, data_type: str | None = None) -> None:
        types = [data_type] if data_type else ["transactions", "balances", "accounts"]
        for t in types:
            self._cache.invalidate(t)
        for t in types:
            if t == "transactions":
                self.transactions()
            elif t == "balances":
                self.balances()
            elif t == "accounts":
                self.accounts()

    def cache_status(self) -> list[dict]:
        return self._cache.status()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_data_service.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add financial_mcp/data_service.py tests/test_data_service.py
git commit -m "feat: DataService gluing adapter to cache with stale-on-failure"
```

---

### Task 10: MCP server, tool registration & entrypoint

**Files:**
- Create: `financial_mcp/mcp_server.py`
- Create: `financial_mcp/main.py`
- Create: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `Config`/`load_config` (Task 2), `configure_logging` (Task 3), `CsvAdapter` (Task 6), `SqliteCache` (Task 7), `DataService` (Task 9), `spending` analytics (Task 8).
- Produces:
  - `financial_mcp.mcp_server.build_server(svc:DataService) -> FastMCP` — registers read + cache-ops tools against the given service (dependency-injected so tests pass a fake-adapter-backed service). Tools return JSON-serializable dicts/lists.
  - `financial_mcp.main.main() -> None` — builds `Config` from env var `FINANCIAL_MCP_CONFIG` (path) or defaults, configures logging, constructs `CsvAdapter` from `cfg.csv_*_path` (raising a clear error if unset), wraps in `DataService`, and runs the server over stdio.
- Tools registered (all Tier 0 reads + ops):
  - `get_accounts()`, `get_transactions(limit:int=100)`, `search_transactions(merchant, category, min_amount, max_amount)`, `category_summary()`, `merchant_summary()`, `monthly_spending(year:int)`, `cache_status()`, `refresh_cache(data_type:str|None=None)`, `health_check()`.

- [ ] **Step 1: Write the failing test**

`tests/test_mcp_tools.py`:
```python
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
                date=d, merchant="Costco", category="Groceries", account="Checking",
                original_statement="COSTCO", notes="", amount=Decimal("-5.00"), tags=[], owner="A",
            )
        ]

    def get_balances(self):
        return [Balance(date=datetime.date(2026, 1, 2), balance=Decimal("100"), account="Checking")]

    def get_accounts(self):
        return [Account(name="Checking", latest_balance=Decimal("100"), as_of=datetime.date(2026, 1, 2))]

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


def test_cache_status_tool(tmp_path):
    server = build_server(_service(tmp_path))
    _tool(server, "get_transactions")()  # populate
    statuses = {s["data_type"]: s for s in _tool(server, "cache_status")()}
    assert statuses["transactions"]["count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_mcp.mcp_server'`

- [ ] **Step 3: Write minimal implementation**

`financial_mcp/mcp_server.py`:
```python
from __future__ import annotations

from decimal import Decimal

from mcp.server.fastmcp import FastMCP

from financial_mcp.analytics import spending
from financial_mcp.data_service import DataService


def build_server(svc: DataService) -> FastMCP:
    server = FastMCP("financial-mcp")

    def _dump(models) -> list[dict]:
        return [m.model_dump(mode="json") for m in models]

    @server.tool()
    def health_check() -> dict:
        """Liveness check; confirms the server and cache are reachable."""
        return {"status": "ok", "cache_types": len(svc.cache_status())}

    @server.tool()
    def get_accounts() -> list[dict]:
        """List financial accounts with their latest known balance."""
        return _dump(svc.accounts())

    @server.tool()
    def get_transactions(limit: int = 100) -> list[dict]:
        """Return the most recent transactions (default 100)."""
        txns = sorted(svc.transactions(), key=lambda t: t.date, reverse=True)
        return _dump(txns[:limit])

    @server.tool()
    def search_transactions(
        merchant: str | None = None,
        category: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
    ) -> list[dict]:
        """Search transactions by merchant/category substring and amount range."""
        results = spending.search(
            svc.transactions(),
            merchant=merchant,
            category=category,
            min_amount=None if min_amount is None else Decimal(str(min_amount)),
            max_amount=None if max_amount is None else Decimal(str(max_amount)),
        )
        return _dump(results)

    @server.tool()
    def category_summary() -> list[dict]:
        """Total spend and count per category, biggest spend first."""
        return _jsonify(spending.category_summary(svc.transactions()))

    @server.tool()
    def merchant_summary() -> list[dict]:
        """Total spend and count per merchant, biggest spend first."""
        return _jsonify(spending.merchant_summary(svc.transactions()))

    @server.tool()
    def monthly_spending(year: int) -> list[dict]:
        """Monthly total spend (negative amounts) for the given year."""
        return _jsonify(spending.monthly_spending(svc.transactions(), year))

    @server.tool()
    def cache_status() -> list[dict]:
        """Per-type cache freshness, age, source, and record count."""
        return svc.cache_status()

    @server.tool()
    def refresh_cache(data_type: str | None = None) -> dict:
        """Force a refresh of one data type (or all if omitted)."""
        svc.refresh(data_type)
        return {"refreshed": data_type or "all"}

    return server


def _jsonify(rows: list[dict]) -> list[dict]:
    """Convert Decimal values in analytics rows to strings for JSON transport."""
    out = []
    for r in rows:
        out.append({k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()})
    return out
```

`financial_mcp/main.py`:
```python
from __future__ import annotations

import os

from financial_mcp.cache.sqlite_cache import SqliteCache
from financial_mcp.config import load_config
from financial_mcp.data_service import DataService
from financial_mcp.mcp_server import build_server
from financial_mcp.monarch.csv_adapter import CsvAdapter
from financial_mcp.utils.logging import configure_logging


def main() -> None:
    cfg = load_config(os.environ.get("FINANCIAL_MCP_CONFIG"))
    logger = configure_logging(cfg)

    if not cfg.csv_transactions_path or not cfg.csv_balances_path:
        raise SystemExit(
            "Plan 1 runs on CSV exports. Set csv_transactions_path and "
            "csv_balances_path in your config.yaml."
        )

    adapter = CsvAdapter(cfg.csv_transactions_path, cfg.csv_balances_path)
    cache = SqliteCache(cfg.db_path, cfg.cache_ttls)
    svc = DataService(adapter, cache)
    logger.info("financial-mcp starting (source=%s)", adapter.source_name())

    server = build_server(svc)
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_tools.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full suite and lint**

Run: `python -m pytest -q && python -m ruff check financial_mcp tests && python -m black --check financial_mcp tests`
Expected: all tests PASS; ruff clean; black reports no changes (run `python -m black financial_mcp tests` first if it wants reformatting, then re-commit).

- [ ] **Step 6: Commit**

```bash
git add financial_mcp/mcp_server.py financial_mcp/main.py tests/test_mcp_tools.py
git commit -m "feat: MCP read + cache-ops tools and stdio entrypoint"
```

---

### Task 11: Manual smoke test + INSTALL doc

**Files:**
- Create: `docs/INSTALL.md`
- Create: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: documentation only. No code, no test.

- [ ] **Step 1: Manual end-to-end smoke test against real CSVs**

Copy the real exports into a git-ignored working dir and point config at them:
```bash
mkdir -p ~/.financial_mcp
cp "Balances_2026-06-15T11-36-15.csv" ~/.financial_mcp/balances.csv
cp "Transactions_2026-06-15T11-36-05.csv" ~/.financial_mcp/transactions.csv
cp config.example.yaml config.yaml
# edit config.yaml: csv_transactions_path / csv_balances_path -> the ~/.financial_mcp copies
```

Verify the server imports and a tool returns data (no live MCP client needed):
```bash
FINANCIAL_MCP_CONFIG=config.yaml python -c "
from financial_mcp.main import load_config
from financial_mcp.monarch.csv_adapter import CsvAdapter
cfg = load_config('config.yaml')
a = CsvAdapter(cfg.csv_transactions_path, cfg.csv_balances_path)
print('transactions:', len(a.get_transactions()))
print('accounts:', [x.name for x in a.get_accounts()])
"
```
Expected: prints a transaction count in the tens of thousands and the list of account names. If it errors, fix before writing docs.

- [ ] **Step 2: Write INSTALL.md**

`docs/INSTALL.md`:
```markdown
# Install — Monarch MCP (Plan 1, CSV mode)

## 1. Install
```bash
python -m pip install -e ".[dev]"
```

## 2. Configure
```bash
cp config.example.yaml config.yaml
```
Copy your Monarch CSV exports somewhere git-ignored (e.g. `~/.financial_mcp/`)
and set `csv_transactions_path` and `csv_balances_path` in `config.yaml`.
**Never commit real CSVs or `config.yaml`.**

## 3. Connect the Claude Desktop app
Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "monarch": {
      "command": "financial-mcp",
      "env": { "FINANCIAL_MCP_CONFIG": "/absolute/path/to/config.yaml" }
    }
  }
}
```
Restart the Claude Desktop app. Ask: "What are my account balances?"

## Plan 1 scope
Read-only, offline, over your CSV exports. Live Monarch sync (Plan 2),
deeper analytics (Plan 3), and writes (Plan 4) come next.
```

- [ ] **Step 3: Write README.md**

`README.md`:
```markdown
# Monarch MCP (personal, local)

A local Model Context Protocol server that connects the Claude Desktop app to
your Monarch Money data. Personal use, runs entirely on your Mac, no hosting.

- **Plan 1 (this):** CSV-backed read server — accounts, transactions, spending
  analytics, cache with TTLs. Fully offline.
- **Plan 2:** live Monarch auth + sync.
- **Plan 3:** forecasting, subscriptions, investment/income/net-worth reports.
- **Plan 4:** guarded write-back (bulk cleanup) with dry-run, batch cap, audit
  log, and undo.

See `docs/specs/2026-07-15-monarch-mcp-server-design.md` for the full design and
`docs/INSTALL.md` to get running.
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/INSTALL.md
git commit -m "docs: README and Plan 1 install/smoke-test guide"
```

---

## Self-Review

**Spec coverage (Plan 1 scope only — Plans 2–4 cover the rest):**
- §4 Architecture (Python 3.12, Pydantic, MCP SDK, adapter, cache, config, layout) → Tasks 1–10 ✅
- §4 adapter pattern → Task 5 interface + Task 6 CSV impl ✅
- §6 SQLite cache + per-type TTL + `cache_status`/`refresh_cache` + stale-on-failure → Tasks 7, 9, 10 ✅
- §8 read tools (`get_accounts`, `get_transactions`, `search_transactions`, `category_summary`, `merchant_summary`, `monthly_spending`) + ops (`refresh_cache`, `cache_status`, `health_check`) → Task 10 ✅
- §10 logging + masking → Task 3 ✅
- §11 config, no secrets, `config.example.yaml` → Task 2 ✅
- §13 CSV copy-only, anonymized fixtures → Task 6 fixtures + Task 11 smoke test ✅
- §14 docs (README, INSTALL) → Task 11 ✅ (SECURITY/ARCHITECTURE/BACKUP land with the features they document, Plans 2–4)
- **Deferred to later plans (intentional, not gaps):** auth/login_cli/Keychain (§5 → Plan 2); community_adapter live sync (§4 → Plan 2); budgets/cashflow/networth/holdings/forecasting/subscriptions reads (§8 → Plan 3); all write tools + guardrails + audit log (§7 → Plan 4).

**Placeholder scan:** No TBD/TODO. Every code step contains complete, runnable code. `_jsonify` (used by `category_summary`/`merchant_summary`/`monthly_spending` tools) and `_dump` are both defined in Task 10.

**Type consistency:** `MonarchAdapter` method names (`get_transactions`/`get_balances`/`get_accounts`/`source_name`) are identical across Tasks 5, 6, 9, 10. `CacheTTLs` field names match cache `data_type` strings (`transactions`,`balances`). `DataService` methods (`transactions`/`balances`/`accounts`/`refresh`/`cache_status`) match their call sites in `mcp_server.py`. `Transaction.synth_id` signature `(date, merchant, amount, original_statement)` is consistent across Tasks 4, 6, and all tests.
