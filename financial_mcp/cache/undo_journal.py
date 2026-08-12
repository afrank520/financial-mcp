# financial_mcp/cache/undo_journal.py
from __future__ import annotations

import datetime
import sqlite3
import time
from collections import Counter
from collections.abc import Callable


class UndoJournal:
    """Local, compensating-write undo log for mutating tools. Lives in its own
    `write_journal` table in the cache DB — never touched by SqliteCache and not
    subject to SCHEMA_VERSION."""

    def __init__(self, db_path: str, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS write_journal (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                transaction_id TEXT NOT NULL,
                old_category_id TEXT,
                old_category_name TEXT NOT NULL,
                new_category_id TEXT NOT NULL,
                new_category_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0
            )
            """)
        self._conn.commit()

    def record(self, batch_id, transaction_id, old_category_id,
               old_category_name, new_category_id, new_category_name) -> None:
        ts = datetime.datetime.fromtimestamp(
            self._clock(), tz=datetime.timezone.utc
        ).isoformat()
        self._conn.execute(
            """INSERT INTO write_journal
               (batch_id, transaction_id, old_category_id, old_category_name,
                new_category_id, new_category_name, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, transaction_id, old_category_id, old_category_name,
             new_category_id, new_category_name, ts),
        )
        self._conn.commit()

    def mark_succeeded(self, batch_id, transaction_id) -> None:
        self._conn.execute(
            "UPDATE write_journal SET succeeded=1 WHERE batch_id=? AND transaction_id=?",
            (batch_id, transaction_id),
        )
        self._conn.commit()

    def last_undoable_batch(self) -> str | None:
        row = self._conn.execute(
            """SELECT batch_id FROM write_journal
               WHERE succeeded=1 AND undone=0 AND old_category_id IS NOT NULL
               ORDER BY seq DESC LIMIT 1"""
        ).fetchone()
        return row[0] if row else None

    def undoable_rows(self, batch_id) -> list[dict]:
        rows = self._conn.execute(
            """SELECT transaction_id, old_category_id, old_category_name, new_category_name
               FROM write_journal
               WHERE batch_id=? AND succeeded=1 AND undone=0 AND old_category_id IS NOT NULL
               ORDER BY seq""",
            (batch_id,),
        ).fetchall()
        return [
            {"transaction_id": r[0], "old_category_id": r[1],
             "old_category_name": r[2], "new_category_name": r[3]}
            for r in rows
        ]

    def mark_undone(self, batch_id, transaction_id=None) -> None:
        """Mark rows as reversed. With `transaction_id=None` (default), marks
        every row in the batch (whole-batch close). With a `transaction_id`,
        marks ONLY that row — used when a compensating write succeeds for one
        row of a batch but not others, so the still-open rows remain undoable
        and can be retried on a subsequent `undo_last` call."""
        if transaction_id is None:
            self._conn.execute(
                "UPDATE write_journal SET undone=1 WHERE batch_id=?", (batch_id,)
            )
        else:
            self._conn.execute(
                "UPDATE write_journal SET undone=1 WHERE batch_id=? AND transaction_id=?",
                (batch_id, transaction_id),
            )
        self._conn.commit()

    def history(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT batch_id, transaction_id, old_category_name, new_category_name,
                      timestamp, undone, MAX(seq) AS mseq
               FROM write_journal GROUP BY batch_id ORDER BY mseq DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for batch_id, _tx, _on, _nn, _ts, _undone, _mseq in rows:
            detail = self._conn.execute(
                """SELECT old_category_name, new_category_name, timestamp, undone
                   FROM write_journal WHERE batch_id=? ORDER BY seq""",
                (batch_id,),
            ).fetchall()
            pairs = Counter((d[0], d[1]) for d in detail)
            (old_n, new_n), n = pairs.most_common(1)[0]
            out.append({
                "batch_id": batch_id,
                "when": detail[0][2],
                "count": len(detail),
                "summary": f"{old_n}→{new_n} ×{n}",
                "undone": all(d[3] == 1 for d in detail),
            })
        return out
