from __future__ import annotations

from decimal import Decimal

from mcp.server.fastmcp import FastMCP

from financial_mcp.analytics import spending
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


def build_server(svc: DataService) -> FastMCP:
    server = FastMCP("financial-mcp")

    def _dump(models) -> list[dict]:
        return [m.model_dump(mode="json") for m in models]

    def _guard(fn):
        """Execute fn and convert typed auth errors to friendly re-login messages."""
        try:
            return fn()
        except (SessionExpiredError, CloudflareBlockedError) as exc:
            return {"error": f"{exc}. Re-authenticate: financial-mcp-login"}

    @server.tool()
    def health_check() -> dict:
        """Liveness check; confirms the server and cache are reachable."""
        return {"status": "ok", "cache_types": len(svc.cache_status())}

    @server.tool()
    def get_accounts() -> list[dict] | dict:
        """List financial accounts with their latest known balance."""
        return _guard(lambda: _dump(svc.accounts()))

    @server.tool()
    def get_transactions(limit: int = DEFAULT_TXN_ROWS) -> list[dict] | dict:
        """Return the most recent transactions (default 100, max 500)."""
        n = _clamp_limit(limit)
        return _guard(
            lambda: _dump(sorted(svc.transactions(), key=lambda t: t.date, reverse=True)[:n])
        )

    @server.tool()
    def search_transactions(
        merchant: str | None = None,
        category: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        limit: int = DEFAULT_TXN_ROWS,
    ) -> list[dict] | dict:
        """Search transactions by merchant/category substring and amount range.

        Returns the most recent matches (default 100, max 500). For all-time
        totals across every matching transaction, use merchant_summary or
        category_summary instead.
        """
        n = _clamp_limit(limit)
        return _guard(
            lambda: _dump(
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
            )
        )

    @server.tool()
    def category_summary() -> list[dict] | dict:
        """Net total and count per category, biggest net spend first."""
        return _guard(lambda: _jsonify(spending.category_summary(svc.transactions())))

    @server.tool()
    def merchant_summary() -> list[dict] | dict:
        """Net total and count per merchant, biggest net spend first."""
        return _guard(lambda: _jsonify(spending.merchant_summary(svc.transactions())))

    @server.tool()
    def monthly_spending(year: int) -> list[dict] | dict:
        """Monthly total spend (negative amounts) for the given year."""
        return _guard(lambda: _jsonify(spending.monthly_spending(svc.transactions(), year)))

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
