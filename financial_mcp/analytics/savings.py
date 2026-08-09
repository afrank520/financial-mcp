from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from financial_mcp.monarch.models import Cashflow

CASH_DEBT_TYPES = {"depository", "credit", "loan"}
INVESTMENT_TYPES = {"brokerage", "real_estate"}

METHODOLOGY = (
    "Savings rate is on take-home (net) income; pre-tax 401k/403b/withholding "
    "are not in Monarch's cashflow and are excluded from the rate — that saving "
    "appears in where_it_went as retirement/brokerage balances rising. The "
    "reconciliation compares cash savings against the change in cash and debt "
    "account balances only; investment and real-estate balances are shown "
    "separately because their changes are mostly market movement, not saving. "
    "The remaining gap reflects internal transfers (excluded from cashflow), "
    "snapshot timing, and any manual/unlinked cash accounts; it is not "
    "attributed to a single cause."
)


def _signed_rate(cashflow: Cashflow) -> tuple[Decimal | None, str | None]:
    if cashflow.income == 0:
        return None, "no income in window; rate undefined"
    rate = (cashflow.savings / cashflow.income).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return rate, None


def summarize(cashflow: Cashflow, delta_rows: list[dict]) -> dict:
    rate, rate_reason = _signed_rate(cashflow)
    rate_is_floored = (
        rate is not None and rate < 0 and cashflow.savings_rate_monarch == 0
    )

    cash_and_debt: list[dict] = []
    investments: list[dict] = []
    excluded: list[dict] = []
    net_worth_delta = Decimal("0")

    for row in delta_rows:
        tag = {
            "account_id": row["account_id"],
            "display_name": row["display_name"],
        }
        if not row["include_in_net_worth"]:
            excluded.append({**tag, "reason": "excluded from Monarch net worth"})
            continue
        type_name = row["type_name"]
        if type_name in INVESTMENT_TYPES:
            investments.append(row)
            continue
        if type_name not in CASH_DEBT_TYPES:
            excluded.append(
                {**tag, "reason": "account type not classified for reconciliation"}
            )
            continue
        # cash + debt
        if row["delta"] is None:
            excluded.append(
                {**tag, "reason": "missing balance at one or both endpoints"}
            )
            continue
        cash_and_debt.append(row)
        net_worth_delta += row["delta"]

    gap = net_worth_delta - cashflow.savings

    cashflow_out: dict = {
        "income": cashflow.income,
        "expense": cashflow.expense,
        "savings": cashflow.savings,
        "savings_rate": rate,
        "savings_rate_monarch": cashflow.savings_rate_monarch,
        "rate_is_floored": rate_is_floored,
    }
    if rate_reason:
        cashflow_out["rate_reason"] = rate_reason

    return {
        "window": {"start": cashflow.start.isoformat(), "end": cashflow.end.isoformat()},
        "cashflow": cashflow_out,
        "where_it_went": {
            "cash_and_debt": cash_and_debt,
            "investments_shown_separately": investments,
        },
        "net_worth_delta_cash_debt": net_worth_delta,
        "reconciliation_gap": gap,
        "excluded_accounts": excluded,
        "methodology": METHODOLOGY,
    }
