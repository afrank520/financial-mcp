# financial_mcp/monarch/community_adapter.py
from __future__ import annotations

import asyncio
import concurrent.futures
import datetime
import json
import logging
import ssl
from collections.abc import Awaitable, Callable
from decimal import Decimal

import aiohttp
import certifi

from financial_mcp.monarch import auth
from financial_mcp.monarch.adapter import MonarchAdapter
from financial_mcp.monarch.models import (
    Account, Balance, BalanceSnapshot, BudgetItem, Cashflow, Category, Holding, NetWorthSnapshot,
    RecurringItem, Transaction,
)
from financial_mcp.monarch.queries import (
    CREATE_CATEGORY,
    CREATE_TRANSACTION_RULE,
    DELETE_CATEGORY,
    DELETE_TRANSACTION_RULE,
    GET_ACCOUNT_SNAPSHOTS,
    GET_ACCOUNTS,
    GET_ACCOUNTS_MIN,
    GET_AGGREGATE_SNAPSHOTS,
    GET_BUDGETS,
    GET_CASHFLOW_SUMMARY,
    GET_CATEGORIES,
    GET_HOLDINGS,
    GET_RECURRING,
    GET_TRANSACTION_RULES,
    GET_TRANSACTIONS,
    UPDATE_CATEGORY,
    UPDATE_TRANSACTION_CATEGORY,
    UPDATE_TRANSACTION_MERCHANT,
    UPDATE_TRANSACTION_RULE,
)
from financial_mcp.writes import _category_rule_input, _rename_rule_input, _update_rule_input

GRAPHQL_URL = "https://api.monarch.com/graphql"
# Build the SSL context from certifi's bundle rather than relying on the OS/Python
# install to have activated its own CA certs — python.org's macOS Python ships certs
# but never activates them, so uncontexted HTTPS calls die with CERTIFICATE_VERIFY_FAILED.
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
logger = logging.getLogger("financial_mcp")
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


def _parse_dt(value) -> datetime.datetime | None:
    """Parse an ISO timestamp from Monarch, treating epoch-0/1970 as 'never' (None).

    Monarch sends displayLastUpdatedAt as an ISO-8601 string (often Z-suffixed).
    An epoch-0 / year-1970 value means the account never refreshed — normalize it
    to None so downstream code reads it as 'no real update' rather than 'updated
    in 1970' (the epoch-0 leak Increment 1 fixed elsewhere)."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return None if dt.year <= 1970 else dt


def _to_account(d: dict, as_of: datetime.date) -> Account:
    # displayBalance is the value Monarch shows; currentBalance is null for many
    # manual/asset/investment accounts, so reading currentBalance alone reported a
    # funded account as $0. Prefer displayBalance, fall back to currentBalance.
    bal = d.get("displayBalance")
    if bal is None:
        bal = d.get("currentBalance")
    cred = d.get("credential") or {}
    return Account(
        name=d["displayName"],
        latest_balance=Decimal(str(bal or 0)),
        as_of=as_of,
        account_id=str(d.get("id") or ""),
        include_in_net_worth=bool(d.get("includeInNetWorth", True)),
        # Connection health (I-6). A credential block means Monarch links this
        # account to a data provider — i.e. it's *supposed* to sync. Manual and
        # valuation accounts (Zestimate-backed real estate) have no credential, so
        # has_credential=False exempts them from the stale check.
        has_credential=bool(cred),
        sync_disabled=bool(d.get("syncDisabled")),
        is_deactivated=d.get("deactivatedAt") is not None,
        credential_disconnected=cred.get("disconnectedFromDataProviderAt") is not None,
        credential_update_required=bool(cred.get("updateRequired")),
        last_updated=_parse_dt(d.get("displayLastUpdatedAt")),
        is_hidden=bool(d.get("isHidden")),
    )


def _to_transaction(d: dict) -> Transaction:
    date = datetime.date.fromisoformat(d["date"])
    m = d.get("merchant") or {}
    merchant = m.get("name", "")
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
        merchant_id=m.get("id") or "",
        merchant_logo=m.get("logoUrl") or "",
    )


class CommunityAdapter(MonarchAdapter):
    def __init__(
        self,
        session: dict,
        service_name: str = auth.DEFAULT_SERVICE,
        transport: Callable[[str, str, dict], Awaitable[dict]] | None = None,
        today: Callable[[], datetime.date] = datetime.date.today,
        page_size: int = 100,
        reload_session: Callable[[], dict | None] | None = None,
    ) -> None:
        self._session = session
        self._service_name = service_name
        # The raw transport does the actual HTTP; self._transport wraps it with
        # the self-heal-on-expiry retry so every method (they all call
        # self._transport) gets the behavior without change.
        self._raw_transport = transport or self._default_transport
        self._transport = self._authed_transport
        self._today = today
        self._page_size = page_size
        # Optional callback that re-reads the saved session from the credential
        # store. When set, a SessionExpiredError triggers a one-time reload +
        # retry, so a session refreshed elsewhere (Terminal login, another chat)
        # is picked up mid-run instead of the process clinging to a dead token.
        self._reload_session = reload_session

    def source_name(self) -> str:
        return "live"

    async def _authed_transport(self, operation: str, query: str, variables: dict) -> dict:
        """Wrap the raw transport with self-heal on session expiry.

        On SessionExpiredError, reload the session from the credential store; if
        it yields a DIFFERENT session, adopt it and retry the call exactly once.
        A same/absent session, or a second expiry, raises — so a truly dead
        session fails fast (surfacing the re-login prompt) and never loops.
        CloudflareBlockedError is not retried: a reload can't clear a Cloudflare
        block."""
        try:
            return await self._raw_transport(operation, query, variables)
        except SessionExpiredError:
            if self._reload_session is None:
                raise
            fresh = self._reload_session()
            if not fresh or fresh == self._session:
                raise
            logger.info("session expired mid-call; reloaded fresh session, retrying once")
            self._session = fresh
            return await self._raw_transport(operation, query, variables)

    async def _default_transport(self, operation: str, query: str, variables: dict) -> dict:
        """POST a GraphQL query to Monarch with cookie auth. Returns the `data`
        object; raises typed errors on auth/Cloudflare failure. A fresh
        ClientSession per call — a shared session breaks under the _run bridge,
        which may execute each call on a different event loop."""
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

        connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
        async with aiohttp.ClientSession(cookies=cookies, connector=connector) as session:
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
        seen: set[str] = set()
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
            # Dedupe by id across pages: a page boundary that shifts (new txn
            # imported mid-pagination, or an overlapping window) otherwise repeats
            # the same id, double-counting a real transaction in every total.
            for r in results:
                txn = _to_transaction(r)
                if txn.id in seen:
                    continue
                seen.add(txn.id)
                out.append(txn)
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
            account_id = acct["id"]
            display_name = acct.get("displayName", "")
            type_name = (acct.get("type") or {}).get("name")
            include = acct.get("includeInNetWorth", True)
            data = self._run(
                self._transport("SnapshotsForAccount", GET_ACCOUNT_SNAPSHOTS, {"id": account_id})
            )
            for snap in data.get("snapshots") or []:
                out.append(BalanceSnapshot(
                    account_id=account_id,
                    display_name=display_name,
                    date=datetime.date.fromisoformat(snap["date"]),
                    balance=Decimal(str(snap["signedBalance"])),
                    type_name=type_name,
                    include_in_net_worth=include,
                ))
        out.sort(key=lambda s: (s.display_name, s.account_id, s.date))
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

    def get_networth_history(self) -> list[NetWorthSnapshot]:
        # Fetch the full series (no window); callers window in the analytics layer,
        # mirroring get_balance_history. startDate/endDate omitted -> Monarch's full range.
        variables = {"filters": {"startDate": None, "endDate": None, "accountType": None}}
        data = self._run(
            self._transport("GetAggregateSnapshots", GET_AGGREGATE_SNAPSHOTS, variables)
        )
        out = [
            NetWorthSnapshot(
                date=datetime.date.fromisoformat(p["date"]),
                net_worth=Decimal(str(p["balance"])),
            )
            for p in data.get("aggregateSnapshots") or []
        ]
        out.sort(key=lambda s: s.date)
        return out

    def get_categories(self) -> list[Category]:
        data = self._run(self._transport("GetCategories", GET_CATEGORIES, {}))
        return [
            Category(
                id=c["id"],
                name=c["name"],
                icon=c.get("icon"),
                group=(c.get("group") or {}).get("name"),
                group_id=(c.get("group") or {}).get("id"),
                group_type=(c.get("group") or {}).get("type"),
            )
            for c in data.get("categories") or []
        ]

    def get_budgets(self, start: datetime.date, end: datetime.date) -> list[BudgetItem]:
        variables = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
        }
        data = self._run(self._transport("GetJointPlanningData", GET_BUDGETS, variables))
        rows = (data.get("budgetData") or {}).get("monthlyAmountsByCategory") or []
        out: list[BudgetItem] = []
        for r in rows:
            cat_id = (r.get("category") or {}).get("id")
            months = r.get("monthlyAmounts") or []
            if not cat_id or not months:
                continue
            m = months[0]  # one month per window row
            out.append(BudgetItem(
                category_id=cat_id,
                budgeted=Decimal(str(m.get("plannedCashFlowAmount") or 0)),
                actual=Decimal(str(m.get("actualAmount") or 0)),
                remaining=Decimal(str(m.get("remainingAmount") or 0)),
            ))
        return out

    def get_holdings(self) -> list[Holding]:
        accts = self._run(self._transport("GetAccounts", GET_ACCOUNTS, {}))
        today = self._today().isoformat()
        out: list[Holding] = []
        for a in accts.get("accounts", []):
            if not (a.get("holdingsCount") or 0):
                continue
            account_name = a.get("displayName", "")
            variables = {"input": {
                "accountIds": [a["id"]],
                "startDate": today,
                "endDate": today,
                "includeHiddenHoldings": True,
            }}
            data = self._run(self._transport("Web_GetHoldings", GET_HOLDINGS, variables))
            port = (data.get("portfolio") or {})
            edges = ((port.get("aggregateHoldings") or {}).get("edges")) or []
            for edge in edges:
                node = edge.get("node") or {}
                sec = node.get("security") or {}
                price = sec.get("currentPrice")
                if price is None:
                    price = sec.get("closingPrice")
                basis = node.get("basis")
                dcd = node.get("securityPriceChangeDollars")
                dcp = node.get("securityPriceChangePercent")
                out.append(Holding(
                    account=account_name,
                    ticker=sec.get("ticker"),
                    name=sec.get("name") or "",
                    quantity=Decimal(str(node.get("quantity") or 0)),
                    price=Decimal(str(price or 0)),
                    value=Decimal(str(node.get("totalValue") or 0)),
                    cost_basis=None if basis is None else Decimal(str(basis)),
                    day_change_dollars=None if dcd is None else Decimal(str(dcd)),
                    day_change_percent=None if dcp is None else Decimal(str(dcp)),
                ))
        return out

    def get_recurring(self, start: datetime.date, end: datetime.date) -> list[RecurringItem]:
        variables = {"startDate": start.isoformat(), "endDate": end.isoformat(), "filters": None}
        data = self._run(
            self._transport("Web_GetUpcomingRecurringTransactionItems", GET_RECURRING, variables)
        )
        out: list[RecurringItem] = []
        for it in data.get("recurringTransactionItems") or []:
            stream = it.get("stream") or {}
            merchant = (stream.get("merchant") or {}).get("name") or ""
            out.append(RecurringItem(
                date=datetime.date.fromisoformat(it["date"]),
                merchant=merchant,
                amount=Decimal(str(it.get("amount") or 0)),
                frequency=stream.get("frequency"),
                account=(it.get("account") or {}).get("displayName"),
                category=(it.get("category") or {}).get("name"),
                is_past=bool(it.get("isPast")),
            ))
        return out

    def recategorize_transaction(self, transaction_id: str, category_id: str) -> bool:
        data = self._run(
            self._transport(
                "Web_TransactionDrawerUpdateTransaction",
                UPDATE_TRANSACTION_CATEGORY,
                {"input": {"id": transaction_id, "category": category_id}},
            )
        )
        txn = (data.get("updateTransaction") or {}).get("transaction") or {}
        applied = (txn.get("category") or {}).get("id")
        return applied == category_id

    def create_category(self, name: str, group_id: str, icon: str) -> str:
        data = self._run(
            self._transport(
                "CreateCategory", CREATE_CATEGORY,
                {"input": {"group": group_id, "name": name, "icon": icon}},
            )
        )
        cat = (data.get("createCategory") or {}).get("category") or {}
        new_id = cat.get("id")
        if not new_id:
            raise RuntimeError(f"Monarch did not create category {name!r}")
        return new_id

    def rename_category(self, category_id: str, new_name: str) -> bool:
        data = self._run(
            self._transport(
                "UpdateCategory", UPDATE_CATEGORY,
                {"input": {"id": category_id, "name": new_name}},
            )
        )
        cat = (data.get("updateCategory") or {}).get("category") or {}
        return cat.get("name") == new_name

    def delete_category(self, category_id: str) -> bool:
        data = self._run(
            self._transport("Web_DeleteCategory", DELETE_CATEGORY, {"id": category_id})
        )
        res = (data.get("deleteCategory") or {})
        return bool(res.get("deleted"))

    def rename_transaction_merchant(self, transaction_id: str, merchant_name: str) -> str | None:
        data = self._run(
            self._transport(
                "Web_TransactionDrawerUpdateTransaction", UPDATE_TRANSACTION_MERCHANT,
                {"input": {"id": transaction_id, "name": merchant_name}},
            )
        )
        txn = (data.get("updateTransaction") or {}).get("transaction") or {}
        merch = txn.get("merchant") or {}
        return merch.get("id") if merch.get("name") == merchant_name else None

    def get_transaction_rules(self) -> list[dict]:
        data = self._run(self._transport("GetTransactionRules", GET_TRANSACTION_RULES, {}))
        out = []
        for r in data.get("transactionRules") or []:
            # The merchant-NAME match ("name contains X" — what our create/update
            # tools write) lives in merchantNameCriteria (a list), NOT merchantCriteria.
            # merchantCriteria is the separate curated-merchant match; when present it
            # makes a rule non-simple (see has_merchant_criteria + rule_complexity),
            # because _update_rule_input sends it null and a full-replacement edit
            # would silently wipe it.
            name_crit_list = r.get("merchantNameCriteria") or []
            crit = name_crit_list[0] if name_crit_list else {}
            sm = r.get("setMerchantAction") or {}
            sc = r.get("setCategoryAction") or {}
            out.append({
                "id": r.get("id"),
                "operator": crit.get("operator"),
                "value": crit.get("value"),
                "has_merchant_criteria": bool(r.get("merchantCriteria")),
                "set_merchant": sm.get("name"),
                "set_category": sc.get("name"),
                "set_category_id": sc.get("id"),
                "order": r.get("order"),
                "uses_original_statement": bool(r.get("merchantCriteriaUseOriginalStatement")),
                "category_ids": r.get("categoryIds") or [],
                "account_ids": r.get("accountIds") or [],
                "has_amount_criteria": bool(r.get("amountCriteria")),
                "has_original_statement_criteria": bool(r.get("originalStatementCriteria")),
                "review_status_action": r.get("reviewStatusAction"),
                "send_notification": bool(r.get("sendNotificationAction")),
                "unassign_needs_review": bool(r.get("unassignNeedsReviewByUserAction")),
                "has_add_tags": bool(r.get("addTagsAction")),
                "has_link_goal": bool(r.get("linkGoalAction")),
                "has_link_savings_goal": bool(r.get("linkSavingsGoalAction")),
                "has_split_action": bool(r.get("splitTransactionsAction")),
            })
        return out

    def _create_rule_from_input(self, rule_input: dict) -> tuple[bool, str | None]:
        # Monarch's createTransactionRuleV2 returns NO id, so we learn it by
        # snapshotting the rule-id set before and after and diffing. Success with
        # exactly one new id -> (True, id); created but 0 or >1 new ids ->
        # (True, None) (id un-capturable, caller must not journal an undo).
        before = {r["id"] for r in self.get_transaction_rules()}
        payload = self._run(self._transport(
            "Common_CreateTransactionRuleMutationV2", CREATE_TRANSACTION_RULE,
            {"input": rule_input}))
        errors = ((payload.get("createTransactionRuleV2") or {}).get("errors")) or []
        if errors:
            return (False, None)
        after = self.get_transaction_rules()
        new = [r["id"] for r in after if r["id"] not in before]
        return (True, new[0]) if len(new) == 1 else (True, None)

    def create_transaction_rule(self, contains: str, rename_to: str) -> tuple[bool, str | None]:
        return self._create_rule_from_input(_rename_rule_input(contains, rename_to))

    def create_category_rule(self, contains: str, category_id: str) -> tuple[bool, str | None]:
        return self._create_rule_from_input(_category_rule_input(contains, category_id))

    def delete_transaction_rule(self, rule_id: str) -> bool:
        # Monarch's deleteTransactionRule (a V2 mutation) returns deleted:false
        # even when the delete actually succeeds — a false-negative confirmed
        # live. So we ignore that boolean and verify by absence, mirroring how
        # create_transaction_rule re-reads the rules list. Success iff the rule
        # id no longer appears; a still-present rule → False (undo stays retryable).
        self._run(self._transport(
            "Common_DeleteTransactionRule", DELETE_TRANSACTION_RULE, {"id": rule_id}))
        return rule_id not in {r["id"] for r in self.get_transaction_rules()}

    def update_transaction_rule(self, rule_id, operator, value,
                                set_category_id, set_merchant) -> bool:
        # updateTransactionRuleV2 returns only {errors}; verify by re-read.
        payload = self._run(self._transport(
            "Common_UpdateTransactionRuleMutationV2", UPDATE_TRANSACTION_RULE,
            {"input": _update_rule_input(rule_id, operator, value, set_category_id, set_merchant)}))
        errors = ((payload.get("updateTransactionRuleV2") or {}).get("errors")) or []
        if errors:
            return False
        rule = next((r for r in self.get_transaction_rules() if r["id"] == rule_id), None)
        if rule is None:
            return False
        return (rule.get("operator") == operator
                and rule.get("value") == value
                and (rule.get("set_category_id") or None) == (set_category_id or None)
                and (rule.get("set_merchant") or None) == (set_merchant or None))
