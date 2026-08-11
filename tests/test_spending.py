import datetime
from decimal import Decimal

from financial_mcp.analytics.spending import (
    category_summary,
    date_range_summary,
    merchant_summary,
    monthly_spending,
    search,
)
from financial_mcp.monarch.models import Category, Transaction


def _txn(merchant, category, amount, month=1, day=1, year=2026):
    d = datetime.date(year, month, day)
    return Transaction(
        id=Transaction.synth_id(d, merchant, Decimal(amount), merchant),
        date=d,
        merchant=merchant,
        category=category,
        account="Checking",
        original_statement=merchant,
        notes="",
        amount=Decimal(amount),
        tags=[],
        owner="Adam",
    )


TXNS = [
    _txn("Costco", "Groceries", "-152.10", month=1),
    _txn("Costco", "Groceries", "-88.40", month=2),
    _txn("Netflix", "Entertainment", "-15.99", month=1),
    _txn("Paycheck", "Income", "2500.00", month=1),
]


def test_category_summary_sorted_biggest_spend_first():
    out = category_summary(TXNS, [])
    rows = out["categories"]
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


# --- Amazon Jul 19-22 2026 accuracy fixture (ground truth: Monarch UI) ---
# 21 charges + 3 refunds = 24 txns; gross 784.65, refunds 58.86, net 725.79.
AMAZON_WINDOW = [
    # July 19 — 3 charges
    _txn("Amazon", "Shopping", "-8.22", month=7, day=19),
    _txn("Amazon", "Shopping", "-14.11", month=7, day=19),
    _txn("Amazon", "Shopping", "-8.58", month=7, day=19),
    # July 20 — 5 charges
    _txn("Amazon", "Shopping", "-190.58", month=7, day=20),
    _txn("Amazon", "Shopping", "-24.99", month=7, day=20),
    _txn("Amazon", "Shopping", "-20.76", month=7, day=20),
    _txn("Amazon", "Shopping", "-132.58", month=7, day=20),
    _txn("Amazon", "Shopping", "-51.57", month=7, day=20),
    # July 21 — 10 charges
    _txn("Amazon", "Shopping", "-23.55", month=7, day=21),
    _txn("Amazon", "Shopping", "-21.85", month=7, day=21),
    _txn("Amazon", "Shopping", "-17.02", month=7, day=21),
    _txn("Amazon", "Shopping", "-29.60", month=7, day=21),
    _txn("Amazon", "Shopping", "-26.18", month=7, day=21),
    _txn("Amazon", "Shopping", "-20.94", month=7, day=21),
    _txn("Amazon", "Shopping", "-20.93", month=7, day=21),
    _txn("Amazon", "Shopping", "-19.87", month=7, day=21),
    _txn("Amazon", "Shopping", "-18.84", month=7, day=21),
    _txn("Amazon", "Shopping", "-23.56", month=7, day=21),
    # July 21 — 3 refunds
    _txn("Amazon", "Shopping", "14.11", month=7, day=21),
    _txn("Amazon", "Shopping", "14.90", month=7, day=21),
    _txn("Amazon", "Shopping", "29.85", month=7, day=21),
    # July 22 — 3 charges
    _txn("Amazon", "Shopping", "-20.28", month=7, day=22),
    _txn("Amazon", "Shopping", "-14.65", month=7, day=22),
    _txn("Amazon", "Shopping", "-75.99", month=7, day=22),
]

# Noise that must be excluded by the date window or the merchant filter.
NOISE = [
    _txn("Costco", "Groceries", "-50.00", month=7, day=20),  # in window, wrong merchant
    _txn("Amazon", "Shopping", "-999.00", month=7, day=18),  # right merchant, before window
    _txn("Amazon", "Shopping", "-999.00", month=7, day=23),  # right merchant, after window
]

JUL19 = datetime.date(2026, 7, 19)
JUL22 = datetime.date(2026, 7, 22)


def test_date_range_summary_matches_monarch_to_the_penny():
    # Headline Goal #4 accuracy test — must equal the Monarch UI numbers.
    s = date_range_summary(AMAZON_WINDOW + NOISE, JUL19, JUL22, merchant="amazon")
    assert s["total"] == Decimal("725.79")  # matches Monarch "Total spending"
    assert s["count"] == 24  # matches Monarch txn count
    assert s["gross_charges"] == Decimal("784.65")
    assert s["refunds"] == Decimal("58.86")
    assert s["charge_count"] == 21
    assert s["refund_count"] == 3


def test_date_range_summary_inclusive_boundaries():
    # A txn exactly on start and one exactly on end are included; neighbors excluded.
    txns = [
        _txn("Amazon", "Shopping", "-10.00", month=7, day=18),  # day before start
        _txn("Amazon", "Shopping", "-1.00", month=7, day=19),  # on start
        _txn("Amazon", "Shopping", "-2.00", month=7, day=22),  # on end
        _txn("Amazon", "Shopping", "-10.00", month=7, day=23),  # day after end
    ]
    s = date_range_summary(txns, JUL19, JUL22)
    assert s["count"] == 2
    assert s["total"] == Decimal("3.00")


def test_date_range_summary_category_filter():
    txns = [
        _txn("Store A", "Groceries", "-40.00", month=7, day=20),
        _txn("Store B", "Shopping", "-60.00", month=7, day=20),
    ]
    s = date_range_summary(txns, JUL19, JUL22, category="grocer")
    assert s["count"] == 1
    assert s["total"] == Decimal("40.00")


def test_date_range_summary_empty_window_is_zeros():
    s = date_range_summary(AMAZON_WINDOW, datetime.date(2026, 1, 1), datetime.date(2026, 1, 2))
    assert s == {
        "total": Decimal("0"),
        "gross_charges": Decimal("0"),
        "refunds": Decimal("0"),
        "count": 0,
        "charge_count": 0,
        "refund_count": 0,
    }


def test_date_range_summary_zero_amount_row_is_neither_charge_nor_refund():
    # Regression test for Finding 1: zero-amount txns count in total but not charge/refund split.
    txns = [
        _txn("Store A", "Shopping", "-10.00", month=7, day=20),  # charge
        _txn("Store B", "Shopping", "4.00", month=7, day=20),  # refund
        _txn("Store C", "Shopping", "0.00", month=7, day=20),  # zero — neither charge nor refund
    ]
    s = date_range_summary(txns, JUL19, JUL22)
    assert s["count"] == 3  # all three in window
    assert s["charge_count"] == 1
    assert s["refund_count"] == 1
    # The zero row contributes nothing to the money math:
    assert s["gross_charges"] == Decimal("10.00")
    assert s["refunds"] == Decimal("4.00")
    assert s["total"] == Decimal("6.00")  # 10.00 - 4.00


def _cat_txn(category, amount):
    d = datetime.date(2026, 1, 2)
    return Transaction(id=Transaction.synth_id(d, "M", Decimal(amount), "M"), date=d,
                       merchant="M", category=category, account="A", original_statement="M",
                       notes="", amount=Decimal(amount), tags=[], owner="A")


def _cats():
    return [
        Category(id="c1", name="Groceries", group="Food & Dining"),
        Category(id="c2", name="Restaurants", group="Food & Dining"),
    ]


def test_category_summary_adds_group_and_rolls_up():
    txns = [_cat_txn("Groceries", "-100.00"), _cat_txn("Restaurants", "-40.00"),
            _cat_txn("Groceries", "-60.00")]
    out = category_summary(txns, _cats())
    groc = next(r for r in out["categories"] if r["category"] == "Groceries")
    assert groc["group"] == "Food & Dining"
    assert groc["total"] == Decimal("-160.00")
    fd = next(g for g in out["groups"] if g["group"] == "Food & Dining")
    assert fd["total"] == Decimal("-200.00")   # -160 groceries + -40 restaurants
    assert fd["count"] == 3


def test_unmatched_category_is_uncategorized_not_dropped():
    txns = [_cat_txn("Groceries", "-100.00"), _cat_txn("Mystery Merch", "-25.00")]
    out = category_summary(txns, _cats())
    myst = next(r for r in out["categories"] if r["category"] == "Mystery Merch")
    assert myst["group"] is None                       # row group null
    unc = next(g for g in out["groups"] if g["group"] == "Uncategorized")
    assert unc["total"] == Decimal("-25.00")           # bucketed, not dropped
