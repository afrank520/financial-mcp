from __future__ import annotations

import datetime
import json
import sqlite3
import time
from collections.abc import Callable

from financial_mcp.config import CacheTTLs

SCHEMA_VERSION = 1

# Always-live (TTL 0) types are refetched every call, so a successful fetch
# leaves age ~= 0. If age exceeds this grace window, the data is NOT live — it
# is a stale fallback served after a failed fetch — and must report as stale.
ALWAYS_LIVE_GRACE_SECONDS = 300


class SqliteCache:
    def __init__(
        self,
        db_path: str,
        ttls: CacheTTLs,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db_path = db_path
        self._ttls = ttls
        self._clock = clock
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_entries (
                data_type TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                last_refresh REAL NOT NULL,
                source TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """)
        self._conn.commit()

    def _ttl_for(self, data_type: str) -> int:
        return getattr(self._ttls, data_type, 0)

    def store(self, data_type: str, payload: list[dict], source: str) -> None:
        self._conn.execute(
            """
            INSERT INTO cache_entries
                (data_type, payload, last_refresh, source, schema_version)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(data_type) DO UPDATE SET
                payload=excluded.payload,
                last_refresh=excluded.last_refresh,
                source=excluded.source,
                schema_version=excluded.schema_version
            """,
            (data_type, json.dumps(payload), self._clock(), source, SCHEMA_VERSION),
        )
        self._conn.commit()

    def load(self, data_type: str) -> list[dict]:
        row = self._conn.execute(
            "SELECT payload FROM cache_entries WHERE data_type=?", (data_type,)
        ).fetchone()
        return json.loads(row[0]) if row else []

    def _last_refresh(self, data_type: str) -> float | None:
        row = self._conn.execute(
            "SELECT last_refresh FROM cache_entries WHERE data_type=?", (data_type,)
        ).fetchone()
        return row[0] if row else None

    def age_seconds(self, data_type: str) -> float | None:
        lr = self._last_refresh(data_type)
        return None if lr is None else self._clock() - lr

    def is_fresh(self, data_type: str) -> bool:
        age = self.age_seconds(data_type)
        if age is None:
            return False
        return age < self._ttl_for(data_type)

    def freshness(self, data_type: str) -> dict:
        """Freshness metadata for one data type, for surfacing to the caller.

        Returns as_of (ISO-8601 UTC of last refresh, or None if never stored),
        age_seconds, and fresh (within TTL).

        A 0-TTL type is "always-live" — DataService refetches it on every call,
        so a successful fetch leaves it fresh. But DataService serves stale
        cache when a fetch fails, so we only call an always-live type fresh when
        its age is within a short grace window; an old always-live entry is a
        failed-fetch fallback and is honestly reported as stale.
        """
        lr = self._last_refresh(data_type)
        as_of = (
            None
            if lr is None
            else datetime.datetime.fromtimestamp(lr, tz=datetime.timezone.utc).isoformat()
        )
        age = self.age_seconds(data_type)
        always_live = self._ttl_for(data_type) == 0 and lr is not None
        fresh = (age < ALWAYS_LIVE_GRACE_SECONDS) if always_live else self.is_fresh(data_type)
        return {
            "as_of": as_of,
            "age_seconds": age,
            "fresh": fresh,
        }

    def invalidate(self, data_type: str) -> None:
        self._conn.execute(
            "UPDATE cache_entries SET last_refresh=0 WHERE data_type=?", (data_type,)
        )
        self._conn.commit()

    def status(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data_type, payload, source FROM cache_entries ORDER BY data_type"
        ).fetchall()
        out: list[dict] = []
        for data_type, payload, source in rows:
            out.append(
                {
                    "data_type": data_type,
                    "age_seconds": self.age_seconds(data_type),
                    "fresh": self.is_fresh(data_type),
                    "source": source,
                    "count": len(json.loads(payload)),
                }
            )
        return out
