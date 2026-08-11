from __future__ import annotations

from decimal import Decimal

from financial_mcp.monarch.models import Holding

METHODOLOGY = (
    "Values, cost basis, and day-change are Monarch's own. gain_loss is value "
    "minus cost basis (Monarch's own numbers); positions with no cost basis are "
    "excluded from the cost-basis and gain_loss totals but still counted in total value."
)


def holdings_summary(holdings: list[Holding]) -> dict:
    positions: list[dict] = []
    for h in holdings:
        gain_loss = None if h.cost_basis is None else h.value - h.cost_basis
        positions.append({
            "account": h.account,
            "ticker": h.ticker,
            "name": h.name,
            "quantity": h.quantity,
            "price": h.price,
            "value": h.value,
            "cost_basis": h.cost_basis,
            "gain_loss": gain_loss,
            "day_change_dollars": h.day_change_dollars,
            "day_change_percent": h.day_change_percent,
        })
    positions.sort(key=lambda p: p["value"], reverse=True)

    totals: dict = {}
    if holdings:
        with_basis = [h for h in holdings if h.cost_basis is not None]
        totals = {
            "value": sum((h.value for h in holdings), Decimal("0")),
            "cost_basis": sum((h.cost_basis for h in with_basis), Decimal("0")),
            "gain_loss": sum((h.value - h.cost_basis for h in with_basis), Decimal("0")),
        }

    return {"positions": positions, "totals": totals, "methodology": METHODOLOGY}
