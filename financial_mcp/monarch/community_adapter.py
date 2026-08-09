# financial_mcp/monarch/community_adapter.py
from __future__ import annotations

import asyncio
import concurrent.futures
import datetime
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal

from financial_mcp.monarch import auth
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import Account, Balance, BalanceSnapshot, Cashflow, Transaction
from financial_mcp.monarch.queries import (
    GET_ACCOUNT_SNAPSHOTS,
    GET_ACCOUNTS,
    GET_ACCOUNTS_MIN,
    GET_CASHFLOW_SUMMARY,
    GET_TRANSACTIONS,
)

GRAPHQL_URL = "https://api.monarch.com/graphql"
# A real-browser User-Agent keeps us clear of any future Cloudflare UA rule.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


class SessionExpiredError(RuntimeError):
    """Raised when Monarch rejects the stored session (401/403 auth failure).
    Caller should prompt the user to re-run `financial-mcp-login`."""


class CloudflareBlockedError(RuntimeError):
    """Raised when Cloudflare blocks the request (403 "Shall Not Pass") before it
    reaches the Monarch API. Distinct from an expired session so a future Monarch
    tightening is diagnosable rather than mistaken for a dead token."""


def _to_account(d: dict, as_of: datetime.date) -> Account:
    return Account(
        name=d["displayName"],
        latest_balance=Decimal(str(d.get("currentBalance") or 0)),
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
        session: dict,
        service_name: str = auth.DEFAULT_SERVICE,
        transport: Callable[[str, str, dict], Awaitable[dict]] | None = None,
        today: Callable[[], datetime.date] = datetime.date.today,
        page_size: int = 100,
    ) -> None:
        self._session = session
        self._service_name = service_name
        self._transport = transport or self._default_transport
        self._today = today
        self._page_size = page_size

    def source_name(self) -> str:
        return "live"

    async def _default_transport(self, operation: str, query: str, variables: dict) -> dict:
        """POST a GraphQL query to Monarch with cookie auth. Returns the `data`
        object; raises typed errors on auth/Cloudflare failure. A fresh
        ClientSession per call — a shared session breaks under the _run bridge,
        which may execute each call on a different event loop."""
        import aiohttp

        sid = self._session["session_id"]
        csrf = self._session["csrftoken"]
        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "client-platform": "web",
            "origin": "https://app.monarch.com",
            "user-agent": USER_AGENT,
            "x-csrftoken": csrf,
        }
        cookies = {"session_id": sid, "csrftoken": csrf}
        payload = {"operationName": operation, "variables": variables, "query": query}

        async with aiohttp.ClientSession(cookies=cookies) as session:
            async with session.post(GRAPHQL_URL, json=payload, headers=headers) as resp:
                status = resp.status
                text = await resp.text()

        if status == 200:
            try:
                body = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Monarch returned non-JSON (HTTP 200): {text[:200]}") from exc
            if body.get("data"):
                return body["data"]
            raise RuntimeError(f"Monarch GraphQL error: {json.dumps(body.get('errors'))[:200]}")

        if status in (401, 403):
            if "Shall Not Pass" in text:
                raise CloudflareBlockedError(
                    "Monarch's Cloudflare blocked the request — refresh your session via "
                    "`financial-mcp-login`."
                )
            raise SessionExpiredError("Monarch session expired — re-run `financial-mcp-login`.")

        raise RuntimeError(f"Monarch HTTP {status}: {text[:200]}")

    def _run(self, coro):
        """Run a coroutine to completion, tolerating an already-running event loop.
        FastMCP invokes sync tools on its running loop, where asyncio.run() would
        raise; offload to a worker thread in that case."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running = False
        else:
            running = True
        if running:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    def get_accounts(self) -> list[Account]:
        data = self._run(self._transport("GetAccounts", GET_ACCOUNTS, {}))
        as_of = self._today()
        return [_to_account(a, as_of) for a in data.get("accounts", [])]

    def get_balances(self) -> list[Balance]:
        return [
            Balance(date=a.as_of, balance=a.latest_balance, account=a.name)
            for a in self.get_accounts()
        ]

    def get_transactions(self) -> list[Transaction]:
        out: list[Transaction] = []
        offset = 0
        while True:
            variables = {
                "offset": offset,
                "limit": self._page_size,
                "orderBy": "date",
                "filters": {"search": "", "categories": [], "accounts": [], "tags": []},
            }
            data = self._run(self._transport("GetTransactionsList", GET_TRANSACTIONS, variables))
            block = data.get("allTransactions", {})
            results = block.get("results", [])
            out.extend(_to_transaction(r) for r in results)
            if not results:
                break
            offset += len(results)
            total = block.get("totalCount")
            if total is not None and offset >= total:
                break
        return out

    def get_balance_history(self) -> list[BalanceSnapshot]:
        accts = self._run(self._transport("GetAccountsMin", GET_ACCOUNTS_MIN, {}))
        out: list[BalanceSnapshot] = []
        for acct in accts.get("accounts", []):
            name = acct.get("displayName", "")
            data = self._run(
                self._transport("SnapshotsForAccount", GET_ACCOUNT_SNAPSHOTS, {"id": acct["id"]})
            )
            for snap in data.get("snapshots") or []:
                out.append(BalanceSnapshot(
                    account=name,
                    date=datetime.date.fromisoformat(snap["date"]),
                    balance=Decimal(str(snap["signedBalance"])),
                ))
        out.sort(key=lambda s: (s.account, s.date))
        return out

    def get_cashflow(self, start: datetime.date, end: datetime.date) -> Cashflow:
        variables = {
            "filters": {
                "search": "",
                "categories": [],
                "accounts": [],
                "tags": [],
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            }
        }
        data = self._run(
            self._transport("Web_GetCashFlowPage", GET_CASHFLOW_SUMMARY, variables)
        )
        summary = data["summary"][0]["summary"]
        return Cashflow(
            start=start,
            end=end,
            income=Decimal(str(summary["sumIncome"])),
            expense=Decimal(str(summary["sumExpense"])),
            savings=Decimal(str(summary["savings"])),
            savings_rate_monarch=Decimal(str(summary["savingsRate"])),
        )
