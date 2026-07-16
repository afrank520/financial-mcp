import datetime
from decimal import Decimal

from financial_mcp.monarch.models import Account, Balance, Transaction


def test_transaction_synth_id_is_stable_and_hex():
    a = Transaction.synth_id(datetime.date(2026, 1, 2), "Costco", Decimal("-52.10"), "COSTCO #123")
    b = Transaction.synth_id(datetime.date(2026, 1, 2), "Costco", Decimal("-52.10"), "COSTCO #123")
    assert a == b
    assert len(a) == 40
    # Changing amount produces different id
    c = Transaction.synth_id(datetime.date(2026, 1, 2), "Costco", Decimal("-52.11"), "COSTCO #123")
    assert a != c
    # Changing date produces different id
    d = Transaction.synth_id(datetime.date(2026, 1, 3), "Costco", Decimal("-52.10"), "COSTCO #123")
    assert a != d
    # Changing merchant produces different id
    e = Transaction.synth_id(datetime.date(2026, 1, 2), "Target", Decimal("-52.10"), "COSTCO #123")
    assert a != e
    # Changing original_statement produces different id
    f = Transaction.synth_id(datetime.date(2026, 1, 2), "Costco", Decimal("-52.10"), "COSTCO #456")
    assert a != f


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
    acct = Account(
        name="Checking", latest_balance=Decimal("1000.00"), as_of=datetime.date(2026, 1, 2)
    )
    assert bal.account == acct.name
