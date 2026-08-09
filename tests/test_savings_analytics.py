import datetime
from decimal import Decimal

from financial_mcp.analytics import savings
from financial_mcp.monarch.models import Cashflow

D = datetime.date


def _cf(income="45976.73", expense="-34059.93", savings="11916.80", rate="0.2592"):
    return Cashflow(
        start=D(2026, 6, 1), end=D(2026, 6, 30),
        income=Decimal(income), expense=Decimal(expense),
        savings=Decimal(savings), savings_rate_monarch=Decimal(rate),
    )


def _row(account_id, display_name, type_name, include, delta):
    return {
        "account_id": account_id,
        "display_name": display_name,
        "type_name": type_name,
        "include_in_net_worth": include,
        "start": None,
        "end": None,
        "delta": None if delta is None else Decimal(delta),
    }


def test_true_signed_rate_quantized_to_four_dp():
    out = savings.summarize(_cf(), [])
    # 11916.80 / 45976.73 = 0.2591919... -> 0.2592
    assert out["cashflow"]["savings_rate"] == Decimal("0.2592")
    assert out["cashflow"]["savings_rate_monarch"] == Decimal("0.2592")
    assert out["cashflow"]["rate_is_floored"] is False


def test_floored_negative_rate_flagged():
    cf = _cf(income="100000.00", expense="-131086.46", savings="-31086.46", rate="0")
    out = savings.summarize(cf, [])
    # our signed rate is negative; Monarch floored to 0 -> flag true
    assert out["cashflow"]["savings_rate"] == Decimal("-0.3109")
    assert out["cashflow"]["savings_rate_monarch"] == Decimal("0")
    assert out["cashflow"]["rate_is_floored"] is True


def test_zero_income_guard_no_divide_by_zero():
    cf = _cf(income="0", expense="-500.00", savings="-500.00", rate="0")
    out = savings.summarize(cf, [])
    assert out["cashflow"]["savings_rate"] is None
    assert out["cashflow"]["rate_reason"] == "no income in window; rate undefined"
    assert out["cashflow"]["rate_is_floored"] is False


def test_cash_and_debt_summed_investments_shown_separately():
    rows = [
        _row("chk", "Checking", "depository", True, "3000.00"),
        _row("cc", "Credit Card", "credit", True, "-500.00"),
        _row("heloc", "HELOC", "loan", True, "10615.00"),
        _row("401k", "401k", "brokerage", True, "8000.00"),
        _row("house", "House", "real_estate", True, "1000.00"),
    ]
    out = savings.summarize(_cf(), rows)
    cd_ids = {r["account_id"] for r in out["where_it_went"]["cash_and_debt"]}
    inv_ids = {r["account_id"] for r in out["where_it_went"]["investments_shown_separately"]}
    assert cd_ids == {"chk", "cc", "heloc"}
    assert inv_ids == {"401k", "house"}
    # 3000 - 500 + 10615 = 13115
    assert out["net_worth_delta_cash_debt"] == Decimal("13115.00")
    # gap = net_worth_delta_cash_debt - savings = 13115.00 - 11916.80 = 1198.20
    assert out["reconciliation_gap"] == Decimal("1198.20")


def test_excluded_account_not_summed_and_listed():
    rows = [
        _row("chk", "Checking", "depository", True, "1000.00"),
        _row("corp", "Corporate Card", "credit", False, "-9999.00"),   # excluded from net worth
    ]
    out = savings.summarize(_cf(), rows)
    assert out["net_worth_delta_cash_debt"] == Decimal("1000.00")   # corp NOT summed
    excluded_ids = {e["account_id"] for e in out["excluded_accounts"]}
    assert "corp" in excluded_ids


def test_unclassified_type_excluded():
    rows = [_row("odd", "Odd", None, True, "5.00")]
    out = savings.summarize(_cf(), rows)
    assert out["net_worth_delta_cash_debt"] == Decimal("0")
    assert {e["account_id"] for e in out["excluded_accounts"]} == {"odd"}


def test_null_delta_cash_account_excluded_not_dropped_silently():
    rows = [_row("newchk", "New Checking", "depository", True, None)]
    out = savings.summarize(_cf(), rows)
    assert out["net_worth_delta_cash_debt"] == Decimal("0")
    e = next(e for e in out["excluded_accounts"] if e["account_id"] == "newchk")
    assert "reason" in e


def test_window_and_methodology_present():
    out = savings.summarize(_cf(), [])
    assert out["window"] == {"start": "2026-06-01", "end": "2026-06-30"}
    assert out["methodology"] == savings.METHODOLOGY
    assert "take-home" in out["methodology"]
