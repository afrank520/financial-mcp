from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from mcp.server.fastmcp import FastMCP

from financial_mcp import writes
from financial_mcp.analytics import balances, budgets, holdings, networth, recurring, savings, spending
from financial_mcp.data_service import DataService
from financial_mcp.monarch.community_adapter import CloudflareBlockedError, SessionExpiredError

# Row-returning tools stream one MCP content block per transaction. A large
# result exceeds MCP's 1MB per-tool-result ceiling and is hard-rejected by the
# client, so these tools return a bounded, most-recent-first browsing view.
# For all-time totals, use category_summary / merchant_summary (one row each).
DEFAULT_TXN_ROWS = 100  # default page when the caller gives no limit
MAX_TXN_ROWS = 500  # hard ceiling; caller cannot exceed this


def _clamp_limit(limit: int | None) -> int:
    """Clamp a caller-supplied row limit into [1, MAX_TXN_ROWS]."""
    if limit is None:
        return DEFAULT_TXN_ROWS
    return max(1, min(limit, MAX_TXN_ROWS))


def build_server(
    svc: DataService,
    *,
    write_mode: str = "off",
    batch_cap: int = 200,
    journal=None,
) -> FastMCP:
    server = FastMCP("financial-mcp")

    def _dump(models) -> list[dict]:
        return [m.model_dump(mode="json") for m in models]

    def _envelope(data_type: str, data, count: int | None = None) -> dict:
        """Wrap tool data with freshness metadata so Claude can reason about age.

        Read freshness AFTER the data is fetched (the fetch populates the
        cache), so as_of reflects the data being returned. `data` is normally a
        list of rows (count defaults to len); pass an explicit count when data
        is a single aggregate object.
        """
        f = svc.freshness(data_type)
        age_s = f["age_seconds"]
        return {
            "as_of": f["as_of"],
            "age_hours": None if age_s is None else round(age_s / 3600, 1),
            "stale": not f["fresh"],
            "count": len(data) if count is None else count,
            "data": data,
        }

    def _live_envelope(data, count: int | None = None) -> dict:
        """Envelope for tools that fetch live every call (no cache to age against).

        Unlike _envelope, this does not read cache freshness: the data was just
        pulled from Monarch, so as_of is now, age is zero, and it is never stale.
        """
        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        return {
            "as_of": now,
            "age_hours": 0.0,
            "stale": False,
            "count": len(data) if count is None else count,
            "data": data,
        }

    def _guard(fn):
        """Execute fn and convert typed auth errors to friendly re-login messages."""
        try:
            return fn()
        except (SessionExpiredError, CloudflareBlockedError) as exc:
            return {"error": f"{exc}. Re-authenticate: financial-mcp-login"}

    def _writes_enabled() -> str | None:
        if write_mode == "off":
            return "writes are disabled. Set write_mode to 'safe' or 'full' to enable."
        return None

    @server.tool()
    def health_check() -> dict:
        """Liveness check; confirms the server and cache are reachable."""
        return {"status": "ok", "cache_types": len(svc.cache_status())}

    @server.tool()
    def get_accounts() -> dict:
        """List financial accounts with their latest known balance.

        Returns an envelope: as_of (when the data was last synced from Monarch),
        age_hours, stale, count, and data. If stale is true or age_hours is
        large, offer to refresh_cache before relying on the numbers.
        """
        return _guard(lambda: _envelope("accounts", _dump(svc.accounts())))

    @server.tool()
    def get_transactions(limit: int = DEFAULT_TXN_ROWS) -> dict:
        """Return the most recent transactions (default 100, max 500).

        Returns an envelope with freshness metadata (as_of, age_hours, stale)
        alongside the data rows.
        """
        n = _clamp_limit(limit)
        return _guard(
            lambda: _envelope(
                "transactions",
                _dump(sorted(svc.transactions(), key=lambda t: t.date, reverse=True)[:n]),
            )
        )

    @server.tool()
    def search_transactions(
        merchant: str | None = None,
        category: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        limit: int = DEFAULT_TXN_ROWS,
    ) -> dict:
        """Search transactions by merchant/category substring and amount range.

        Returns the most recent matches (default 100, max 500). For all-time
        totals across every matching transaction, use merchant_summary or
        category_summary instead.
        """
        n = _clamp_limit(limit)
        return _guard(
            lambda: _envelope(
                "transactions",
                _dump(
                    sorted(
                        spending.search(
                            svc.transactions(),
                            merchant=merchant,
                            category=category,
                            min_amount=None if min_amount is None else Decimal(str(min_amount)),
                            max_amount=None if max_amount is None else Decimal(str(max_amount)),
                        ),
                        key=lambda t: t.date,
                        reverse=True,
                    )[:n]
                ),
            )
        )

    @server.tool()
    def category_summary() -> dict:
        """Net total and count per category AND per Monarch category group.

        Returns an envelope whose data has `categories` (one row per category
        with its group) and `groups` (group-level subtotals, e.g. Food & Dining =
        Groceries + Restaurants). Categories with no group match roll into
        "Uncategorized". This is the accurate all-time total across every
        transaction. Freshness metadata (as_of, age_hours, stale) is included.
        """

        def _run():
            data = spending.category_summary(svc.transactions(), svc.categories())
            return _envelope("transactions", _jsonify([data])[0], count=len(data["categories"]))

        return _guard(_run)

    @server.tool()
    def merchant_summary() -> dict:
        """Net total and count per merchant, biggest net spend first.

        Returns an envelope with freshness metadata (as_of, age_hours, stale).
        This is the accurate all-time total across every transaction.
        """
        return _guard(
            lambda: _envelope(
                "transactions", _jsonify(spending.merchant_summary(svc.transactions()))
            )
        )

    @server.tool()
    def monthly_spending(year: int) -> dict:
        """Monthly total spend (negative amounts) for the given year.

        Returns an envelope with freshness metadata (as_of, age_hours, stale).
        """
        return _guard(
            lambda: _envelope(
                "transactions", _jsonify(spending.monthly_spending(svc.transactions(), year))
            )
        )

    @server.tool()
    def spending_summary(
        start_date: str,
        end_date: str,
        merchant: str | None = None,
        category: str | None = None,
    ) -> dict:
        """Exact server-computed spending total for a date window (YYYY-MM-DD).

        Use this for ANY "how much did I spend between/from date X to Y"
        question instead of summing search_transactions rows yourself. Dates are
        inclusive on both ends. Optionally filter by merchant/category substring.

        Returns an envelope whose data has: total (net spend, sign-flipped so
        ordinary spending is positive; can be negative when refunds exceed charges),
        gross_charges (excludes refunds), refunds, count, charge_count, refund_count.
        Note: charge_count + refund_count may be less than count when zero-amount
        transactions exist. An UNFILTERED window may include internal transfers; a
        merchant/category filter matches Monarch exactly.
        """
        try:
            start = datetime.date.fromisoformat(start_date)
        except ValueError:
            return {"error": "start_date must be YYYY-MM-DD"}
        try:
            end = datetime.date.fromisoformat(end_date)
        except ValueError:
            return {"error": "end_date must be YYYY-MM-DD"}
        if start > end:
            return {"error": "start_date must not be after end_date"}

        def _run():
            summary = spending.date_range_summary(
                svc.transactions(), start, end, merchant=merchant, category=category
            )
            return _envelope("transactions", _jsonify([summary])[0], count=summary["count"])

        return _guard(_run)

    @server.tool()
    def balance_as_of(date: str) -> dict:
        """Each account's balance as-of a date (YYYY-MM-DD), carry-forward.

        Returns the last known balance on-or-before the date per account, with
        the real snapshot_date it came from and is_carry_forward. Accounts with
        no snapshot on-or-before the date return balance null + reason. Balances
        are signed as Monarch reports them (liabilities negative). Use this for
        glide-path checkpoints like "where was the HELOC on 2026-06-30".
        """
        try:
            target = datetime.date.fromisoformat(date)
        except ValueError:
            return {"error": "date must be YYYY-MM-DD"}

        def _run():
            rows = balances.balance_as_of(svc.balance_history(), target)
            return _envelope("balance_history", _jsonify(rows))

        return _guard(_run)

    @server.tool()
    def balance_delta(start: str, end: str) -> dict:
        """Per-account balance change between two dates (YYYY-MM-DD, inclusive).

        Server-computed delta = end_balance - start_balance per account, so a
        HELOC paydown surfaces as a POSITIVE delta. Each endpoint reports its
        real snapshot_date and is_carry_forward. If either endpoint has no
        balance, delta is null + reason. Use this for "how much did the HELOC
        drop between X and Y" instead of subtracting balance_as_of calls yourself.
        """
        try:
            s = datetime.date.fromisoformat(start)
        except ValueError:
            return {"error": "start must be YYYY-MM-DD"}
        try:
            e = datetime.date.fromisoformat(end)
        except ValueError:
            return {"error": "end must be YYYY-MM-DD"}
        if s > e:
            return {"error": "start must not be after end"}

        def _run():
            rows = balances.balance_delta(svc.balance_history(), s, e)
            return _envelope("balance_history", _jsonify(rows))

        return _guard(_run)

    @server.tool()
    def savings_summary(start: str, end: str) -> dict:
        """Savings rate + "where it went" reconciliation for a window (YYYY-MM-DD, inclusive).

        Reports Monarch's authoritative cashflow savings rate (on take-home / net
        income) plus a true signed rate — Monarch floors negative rates at 0, so
        rate_is_floored flags when they diverge. Then reconciles that cash savings
        against the change in cash+debt account balances (net_worth_delta_cash_debt),
        naming the unattributed reconciliation_gap. Investment/real-estate balances
        are shown separately (mostly market movement, not saving). The methodology
        field explains what the rate does and does not include — always surface it
        when discussing the number. Use this for "what was my savings rate" and
        "where did my savings go" questions.
        """
        try:
            s = datetime.date.fromisoformat(start)
        except ValueError:
            return {"error": "start must be YYYY-MM-DD"}
        try:
            e = datetime.date.fromisoformat(end)
        except ValueError:
            return {"error": "end must be YYYY-MM-DD"}
        if s > e:
            return {"error": "start must not be after end"}

        def _run():
            cashflow = svc.cashflow(s, e)
            delta_rows = balances.balance_delta(svc.balance_history(), s, e)
            data = savings.summarize(cashflow, delta_rows)
            return _envelope("balance_history", _jsonify([data])[0], count=1)

        return _guard(_run)

    @server.tool()
    def networth_trend(start: str, end: str) -> dict:
        """Net worth at the start and end of a window (YYYY-MM-DD, inclusive) plus the change.

        Returns Monarch's own net-worth series: start_point, end_point, a
        server-computed change (end - start), and every monthly point in the
        window. This is net worth only (assets minus liabilities as one number);
        for WHY it changed (which accounts moved), use balance_delta or
        savings_summary. Use this for "how has my net worth changed" and
        glide-path questions.
        """
        try:
            s = datetime.date.fromisoformat(start)
        except ValueError:
            return {"error": "start must be YYYY-MM-DD"}
        try:
            e = datetime.date.fromisoformat(end)
        except ValueError:
            return {"error": "end must be YYYY-MM-DD"}
        if s > e:
            return {"error": "start must not be after end"}

        def _run():
            data = networth.networth_trend(svc.networth_history(), s, e)
            return _envelope("networth", _jsonify([data])[0], count=len(data["points"]))

        return _guard(_run)

    @server.tool()
    def budget_status(start: str, end: str) -> dict:
        """Per-category budgeted vs. actual for a month window (YYYY-MM-DD, inclusive).

        The red/green scorecard: each budgeted category with budgeted, actual,
        remaining, a `type` (Monarch's own income/expense/transfer), and a flag —
        `over_budget` for expense/transfer, `favorable` for income (earning more
        than planned is good, not overspending). `totals` is split into per-type
        sub-blocks (expense/income/transfer/unknown), so "how much did I overspend"
        reads off totals.expense alone — income is never mingled in. A `methodology`
        string explains the split; surface it when discussing the numbers. All
        figures are Monarch's own, passed through as-is.
        """
        try:
            s = datetime.date.fromisoformat(start)
        except ValueError:
            return {"error": "start must be YYYY-MM-DD"}
        try:
            e = datetime.date.fromisoformat(end)
        except ValueError:
            return {"error": "end must be YYYY-MM-DD"}
        if s > e:
            return {"error": "start must not be after end"}

        def _run():
            data = budgets.budget_status(svc.budgets(s, e), svc.categories(), s, e)
            return _envelope("categories", _jsonify([data])[0], count=len(data["categories"]))

        return _guard(_run)

    @server.tool()
    def holdings_summary() -> dict:
        """Current investment holdings: each position plus portfolio totals.

        Returns an envelope whose data has `positions` (one per holding:
        account, ticker, name, quantity, price, value, cost_basis, gain_loss,
        day_change_dollars, day_change_percent), `totals` (portfolio value,
        cost_basis, gain_loss), and a `methodology` string. gain_loss is
        value − cost_basis (Monarch's own numbers) and is null when Monarch has
        no cost basis for a position; such positions count toward total value
        but not toward total cost_basis/gain_loss. All figures are Monarch's
        own — no returns %, allocation %, or other derived ratios. Positions are
        largest-value first. Surface the methodology when discussing the numbers.
        """
        def _run():
            data = holdings.holdings_summary(svc.holdings())
            return _envelope("holdings", _jsonify([data])[0], count=len(data["positions"]))
        return _guard(_run)

    @server.tool()
    def recurring_summary(start: str, end: str) -> dict:
        """Upcoming/past recurring (subscription) items in a window (YYYY-MM-DD, inclusive).

        Returns an envelope whose data has `window`, `items` (one per recurring
        instance: date, merchant, amount, frequency, account, category, is_past),
        a `total_expected` (plain sum of the window's item amounts — Monarch's own
        signed amounts, NOT a normalized monthly figure), and a `methodology`
        string. Amounts and frequencies are Monarch's own; nothing is normalized
        or invented. Items are date-ascending (what is hitting next). Fetched live
        each call. Use this for "what subscriptions/recurring charges are coming
        up" and "how much of my month is already committed" questions.
        """
        try:
            s = datetime.date.fromisoformat(start)
        except ValueError:
            return {"error": "start must be YYYY-MM-DD"}
        try:
            e = datetime.date.fromisoformat(end)
        except ValueError:
            return {"error": "end must be YYYY-MM-DD"}
        if s > e:
            return {"error": "start must not be after end"}

        def _run():
            data = recurring.recurring_summary(svc.recurring(s, e), s, e)
            # Pulled live from Monarch every call — report honest live freshness
            # (as_of = now, not stale) rather than borrowing an unread cache type.
            return _live_envelope(_jsonify([data])[0], count=len(data["items"]))
        return _guard(_run)

    @server.tool()
    def cache_status() -> list[dict]:
        """Per-type cache freshness, age, source, and record count.

        Use this to check how current the data is before answering a
        freshness-sensitive question.
        """
        return svc.cache_status()

    @server.tool()
    def refresh_cache(data_type: str | None = None) -> dict:
        """Force a fresh pull from Monarch for one data type (or all if omitted).

        Call this when the user says they just changed something in Monarch
        (recategorized transactions, added or edited an account, updated a
        budget), or explicitly wants the very latest data instead of the cached
        copy. data_type is one of: transactions, balances, accounts,
        balance_history, networth, categories, holdings.
        """
        svc.refresh(data_type)
        return {"refreshed": data_type or "all"}

    @server.tool()
    def recategorize_transaction(transaction_id: str, category: str) -> dict:
        """Recategorize ONE transaction to an existing category (by name).

        Fail-closed: the category name must match an existing Monarch category
        exactly (case-insensitive) or the call refuses with the valid list. In
        write_mode 'off' this refuses; in 'safe'/'full' it executes one-shot and
        is reversible via undo_last. All figures pass through to Monarch as-is.
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}

        def _run():
            cats = svc.categories()
            try:
                target = writes.resolve_category(category, cats)
            except writes.WriteError as exc:
                return {"error": str(exc)}
            txn = next((t for t in svc.transactions() if t.id == transaction_id), None)
            if txn is None:
                return {"error": f"No transaction with id {transaction_id!r}."}
            old_matches = [c for c in cats if c.name.lower() == txn.category.lower()]
            old_id = old_matches[0].id if len(old_matches) == 1 else None
            batch_id = uuid.uuid4().hex
            journal.record(batch_id, txn.id, old_id, txn.category, target.id, target.name)
            applied = svc.recategorize(txn.id, target.id)
            if applied:
                journal.mark_succeeded(batch_id, txn.id)
                return {"changed": 1, "from": txn.category, "to": target.name,
                        "batch_id": batch_id, "undo_hint": "call undo_last to reverse"}
            return {"changed": 0, "failed": 1,
                    "failures": [{"transaction_id": txn.id, "reason": "Monarch did not apply the category"}]}

        return _guard(_run)

    @server.tool()
    def recategorize_matching(
        to_category: str,
        merchant: str | None = None,
        category: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        start: str | None = None,
        end: str | None = None,
        confirm_token: str | None = None,
    ) -> dict:
        """Bulk-recategorize every transaction matching a filter to one category.

        Filter mirrors search_transactions (merchant/category substrings +
        amount range) plus an optional YYYY-MM-DD date window. In 'safe' mode
        this is two-phase: call without confirm_token to preview (nothing
        mutates) and get a confirm_token, then call again with that token to
        execute. In 'full' mode it executes directly. Over the batch cap it
        refuses (no truncation). Reversible via undo_last.
        """
        disabled = _writes_enabled()
        if disabled:
            return {"error": disabled}

        s = e = None
        if start is not None:
            try:
                s = datetime.date.fromisoformat(start)
            except ValueError:
                return {"error": "start must be YYYY-MM-DD"}
        if end is not None:
            try:
                e = datetime.date.fromisoformat(end)
            except ValueError:
                return {"error": "end must be YYYY-MM-DD"}
        if s and e and s > e:
            return {"error": "start must not be after end"}

        def _run():
            cats = svc.categories()
            try:
                target = writes.resolve_category(to_category, cats)
            except writes.WriteError as exc:
                return {"error": str(exc)}
            matched = writes.match_transactions(
                svc.transactions(), merchant=merchant, category=category,
                min_amount=None if min_amount is None else Decimal(str(min_amount)),
                max_amount=None if max_amount is None else Decimal(str(max_amount)),
                start=s, end=e,
            )
            if not matched:
                return {"would_change": 0, "message": "no transactions matched"}
            if len(matched) > batch_cap:
                return {"error": f"{len(matched)} transactions match, over the batch cap "
                                 f"of {batch_cap}. Narrow the filter or raise batch_cap."}
            token = writes.compute_confirm_token([t.id for t in matched], target.id)

            def _preview():
                sample = [{"transaction_id": t.id, "date": t.date.isoformat(),
                           "merchant": t.merchant, "amount": str(t.amount),
                           "current_category": t.category} for t in matched[:20]]
                return {"dry_run": True, "would_change": len(matched),
                        "resolved_category": {"id": target.id, "name": target.name,
                                              "group": target.group},
                        "sample": sample, "confirm_token": token, "mode": write_mode}

            if write_mode == "safe":
                if confirm_token is None or confirm_token != token:
                    return _preview()
            # execute (safe+valid token, or full)
            by_name = {}
            for c in cats:
                by_name.setdefault(c.name.lower(), []).append(c)
            batch_id = uuid.uuid4().hex
            changed = 0
            failures = []
            for t in matched:
                oid_matches = by_name.get(t.category.lower(), [])
                old_id = oid_matches[0].id if len(oid_matches) == 1 else None
                journal.record(batch_id, t.id, old_id, t.category, target.id, target.name)
                if svc.recategorize(t.id, target.id):
                    journal.mark_succeeded(batch_id, t.id)
                    changed += 1
                else:
                    failures.append({"transaction_id": t.id,
                                     "reason": "Monarch did not apply the category"})
            return {"changed": changed, "failed": len(failures),
                    "failures": failures, "batch_id": batch_id, "to": target.name}

        return _guard(_run)

    return server


def _jsonify(rows: list[dict]) -> list[dict]:
    """Convert Decimal values (including nested dicts and lists) to strings for JSON transport."""

    def conv(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v

    return [{k: conv(v) for k, v in r.items()} for r in rows]
