from __future__ import annotations

import asyncio
import datetime
from collections.abc import Callable
from decimal import Decimal

from financial_mcp.monarch import auth
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, Transaction


class SessionExpiredError(RuntimeError):
    """Raised when Monarch rejects the stored token (401/403). Caller should
    prompt the user to re-run `financial-mcp-login`."""


def _default_client_factory(token: str) -> object:
    from monarchmoney import MonarchMoney

    return MonarchMoney(token=token)


def _is_auth_error(exc: Exception) -> bool:
    msg = str(exc)
    return "401" in msg or "403" in msg or "Unauthorized" in msg


def _to_account(d: dict, as_of: datetime.date) -> Account:
    return Account(
        name=d["displayName"],
        latest_balance=Decimal(str(d["currentBalance"])),
        as_of=as_of,
    )


def _to_transaction(d: dict) -> Transaction:
    date = datetime.date.fromisoformat(d["date"])
    merchant = (d.get("merchant") or {}).get("name", "")
    amount = Decimal(str(d["amount"]))
    original = d.get("plaidName") or ""
    tid = d.get("id") or Transaction.synth_id(date, merchant, amount, original)
    return Transaction(
        id=str(tid),
        date=date,
        merchant=merchant,
        category=(d.get("category") or {}).get("name", ""),
        account=(d.get("account") or {}).get("displayName", ""),
        original_statement=original,
        notes=d.get("notes") or "",
        amount=amount,
        tags=[t["name"] for t in (d.get("tags") or [])],
        owner=d.get("owner") or "",
    )


class CommunityAdapter(MonarchAdapter):
    def __init__(
        self,
        token: str,
        service_name: str = auth.DEFAULT_SERVICE,
        client_factory: Callable[[str], object] | None = None,
        today: Callable[[], datetime.date] = datetime.date.today,
        page_size: int = 100,
    ) -> None:
        self._token = token
        self._service_name = service_name
        self._client_factory = client_factory or _default_client_factory
        self._today = today
        self._page_size = page_size

    def source_name(self) -> str:
        return "live"

    def _run(self, coro):
        try:
            return asyncio.run(coro)
        except Exception as exc:  # noqa: BLE001 — re-typed below
            if _is_auth_error(exc):
                raise SessionExpiredError(
                    "Monarch session expired — re-run `financial-mcp-login`."
                ) from exc
            raise

    def get_accounts(self) -> list[Account]:
        client = self._client_factory(self._token)
        raw = self._run(client.get_accounts())
        as_of = self._today()
        return [_to_account(a, as_of) for a in raw.get("accounts", [])]

    def get_balances(self) -> list[Balance]:
        return [
            Balance(date=a.as_of, balance=a.latest_balance, account=a.name)
            for a in self.get_accounts()
        ]

    def get_transactions(self) -> list[Transaction]:
        client = self._client_factory(self._token)
        out: list[Transaction] = []
        offset = 0
        while True:
            raw = self._run(client.get_transactions(limit=self._page_size, offset=offset))
            block = raw.get("allTransactions", {})
            results = block.get("results", [])
            out.extend(_to_transaction(r) for r in results)
            offset += len(results)
            if not results or offset >= block.get("totalCount", 0):
                break
        return out
