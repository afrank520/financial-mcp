from __future__ import annotations

from decimal import Decimal

from mcp.server.fastmcp import FastMCP

from financial_mcp.analytics import spending
from financial_mcp.data_service import DataService
from financial_mcp.monarch.community_adapter import SessionExpiredError


def build_server(svc: DataService) -> FastMCP:
    server = FastMCP("financial-mcp")

    def _dump(models) -> list[dict]:
        return [m.model_dump(mode="json") for m in models]

    @server.tool()
    def health_check() -> dict:
        """Liveness check; confirms the server and cache are reachable."""
        return {"status": "ok", "cache_types": len(svc.cache_status())}

    @server.tool()
    def get_accounts() -> list[dict] | dict:
        """List financial accounts with their latest known balance."""
        try:
            return _dump(svc.accounts())
        except SessionExpiredError as exc:
            return {"error": f"{exc}. Re-authenticate: financial-mcp-login"}

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
        """Net total and count per category, biggest net spend first."""
        return _jsonify(spending.category_summary(svc.transactions()))

    @server.tool()
    def merchant_summary() -> list[dict]:
        """Net total and count per merchant, biggest net spend first."""
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
