from __future__ import annotations

import datetime
import json
import sqlite3
import time
from collections.abc import Callable

from financial_mcp.config import CacheTTLs, ensure_parent

SCHEMA_VERSION = 2


class SqliteCache:
    def __init__(
        self,
        db_path: str,
        ttls: CacheTTLs,
        clock: Callable[[], float] = time.time,
    ) -> None:
        ensure_parent(db_path)
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

    def _row_schema_version(self, data_type: str) -> int | None:
        row = self._conn.execute(
            "SELECT schema_version FROM cache_entries WHERE data_type=?", (data_type,)
        ).fetchone()
        return row[0] if row else None

    def load(self, data_type: str) -> list[dict]:
        row = self._conn.execute(
            "SELECT payload, schema_version FROM cache_entries WHERE data_type=?", (data_type,)
        ).fetchone()
        if row is None:
            return []
        payload, schema_version = row
        if schema_version != SCHEMA_VERSION:
            # A version mismatch means the persisted row was written by an
            # older model shape (e.g. the pre-migration BalanceSnapshot). It
            # must never be handed to model(**d) -- treat it as no usable
            # cache at all, for every caller (is_fresh, DataService's
            # live path, and DataService's stale-fallback path alike).
            return []
        return json.loads(payload)

    def _last_refresh(self, data_type: str) -> float | None:
        row = self._conn.execute(
            "SELECT last_refresh FROM cache_entries WHERE data_type=?", (data_type,)
        ).fetchone()
        if row is None:
            return None
        lr = row[0]
        # 0 is the invalidate() sentinel, never a real clock value (store() always
        # writes self._clock()). Treat it as never-refreshed so age/as_of report
        # None rather than leaking a 1970-01-01 epoch-0 timestamp when a failed
        # refetch leaves an invalidated entry behind.
        return None if not lr else lr

    def age_seconds(self, data_type: str) -> float | None:
        lr = self._last_refresh(data_type)
        return None if lr is None else self._clock() - lr

    def is_fresh(self, data_type: str) -> bool:
        age = self.age_seconds(data_type)
        if age is None:
            return False
        row_version = self._row_schema_version(data_type)
        if row_version != SCHEMA_VERSION:
            # A schema-version mismatch is never fresh, regardless of age --
            # the persisted row was written under an older model shape and
            # forcing a live re-fetch is the only safe path (see load()).
            return False
        return age < self._ttl_for(data_type)

    def _reported_fresh(self, data_type: str) -> bool:
        """Freshness AS REPORTED to callers (envelope / cache_status / health).

        Distinct from is_fresh(), which drives DataService's refetch decision and
        for a 0-TTL "always-live" type is deliberately always False (refetch every
        call). For reporting, an always-live type's cache age is irrelevant: since
        every read refetches, the age measures only "time since anyone last asked,"
        not the staleness of what a caller gets. So it reports fresh whenever it has
        a successful stored fetch (last_refresh set); an invalidated/never-stored
        entry (a failed-refetch leftover, last_refresh None) reports stale. Gating
        an always-live type on age cried wolf -- accounts read stale minutes after
        every fetch and dragged health_check to degraded on a healthy live session.
        Non-always-live types report their plain TTL freshness.
        """
        if self._ttl_for(data_type) == 0:
            return self._last_refresh(data_type) is not None
        return self.is_fresh(data_type)

    def freshness(self, data_type: str) -> dict:
        """Freshness metadata for one data type, for surfacing to the caller.

        Returns as_of (ISO-8601 UTC of last refresh, or None if never stored),
        age_seconds, and fresh (the reported-fresh value; see _reported_fresh).
        """
        lr = self._last_refresh(data_type)
        as_of = (
            None
            if lr is None
            else datetime.datetime.fromtimestamp(lr, tz=datetime.timezone.utc).isoformat()
        )
        return {
            "as_of": as_of,
            "age_seconds": self.age_seconds(data_type),
            "fresh": self._reported_fresh(data_type),
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
                    # reported freshness (see _reported_fresh), so an always-live
                    # type like `accounts` -- refetched on every read -- doesn't
                    # read as stale here and wrongly flip health_check to degraded.
                    "fresh": self._reported_fresh(data_type),
                    "source": source,
                    "count": len(json.loads(payload)),
                }
            )
        return out
