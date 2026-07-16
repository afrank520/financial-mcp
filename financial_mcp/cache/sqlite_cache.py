from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable

from financial_mcp.config import CacheTTLs

SCHEMA_VERSION = 1


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
