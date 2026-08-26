# financial_mcp/cache/undo_journal.py
from __future__ import annotations

import datetime
import sqlite3
import time
from collections.abc import Callable

REVERSIBLE_OPS = ("recategorize", "rename_category", "delete_category", "rename_merchant", "create_rule", "create_category_rule")


class UndoJournal:
    """Local, compensating-write undo log for mutating tools. A general op-log:
    each row is one operation with an op_type the dispatcher reverses. Lives in
    its own `write_journal` table in the cache DB, not under SCHEMA_VERSION."""

    def __init__(self, db_path: str, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(db_path)
        self._migrate()
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS write_journal (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                op_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT NOT NULL,
                label_old TEXT,
                label_new TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0
            )
            """)
        self._conn.commit()

    def _migrate(self) -> None:
        """One-time in-place migration from the Slice-1 recat-only schema."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(write_journal)")}
        if not cols or "op_type" in cols:
            return  # fresh install or already migrated
        # Old schema present: copy rows into the generalized shape, preserving seq.
        self._conn.execute("ALTER TABLE write_journal RENAME TO write_journal_old")
        self._conn.execute("""
            CREATE TABLE write_journal (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL, op_type TEXT NOT NULL, target_id TEXT NOT NULL,
                old_value TEXT, new_value TEXT NOT NULL, label_old TEXT, label_new TEXT NOT NULL,
                timestamp TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0)""")
        self._conn.execute("""
            INSERT INTO write_journal
                (seq, batch_id, op_type, target_id, old_value, new_value,
                 label_old, label_new, timestamp, undone, succeeded)
            SELECT seq, batch_id, 'recategorize', transaction_id, old_category_id,
                   new_category_id, old_category_name, new_category_name,
                   timestamp, undone, succeeded
            FROM write_journal_old""")
        self._conn.execute("DROP TABLE write_journal_old")
        self._conn.commit()

    def record(self, batch_id, op_type, target_id, old_value,
               new_value, label_old, label_new) -> None:
        ts = datetime.datetime.fromtimestamp(
            self._clock(), tz=datetime.timezone.utc
        ).isoformat()
        self._conn.execute(
            """INSERT INTO write_journal
               (batch_id, op_type, target_id, old_value, new_value,
                label_old, label_new, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, op_type, target_id, old_value, new_value, label_old, label_new, ts),
        )
        self._conn.commit()

    def mark_succeeded(self, batch_id, target_id) -> None:
        self._conn.execute(
            "UPDATE write_journal SET succeeded=1 WHERE batch_id=? AND target_id=?",
            (batch_id, target_id),
        )
        self._conn.commit()

    def last_undoable_batch(self) -> str | None:
        placeholders = ",".join("?" for _ in REVERSIBLE_OPS)
        row = self._conn.execute(
            f"""SELECT batch_id FROM write_journal
                WHERE succeeded=1 AND undone=0 AND old_value IS NOT NULL
                  AND op_type IN ({placeholders})
                ORDER BY seq DESC LIMIT 1""",
            REVERSIBLE_OPS,
        ).fetchone()
        return row[0] if row else None

    def undoable_rows(self, batch_id) -> list[dict]:
        placeholders = ",".join("?" for _ in REVERSIBLE_OPS)
        rows = self._conn.execute(
            f"""SELECT target_id, op_type, old_value, new_value, label_old, label_new
                FROM write_journal
                WHERE batch_id=? AND succeeded=1 AND undone=0 AND old_value IS NOT NULL
                  AND op_type IN ({placeholders})
                ORDER BY seq""",
            (batch_id, *REVERSIBLE_OPS),
        ).fetchall()
        return [
            {"target_id": r[0], "op_type": r[1], "old_value": r[2],
             "new_value": r[3], "label_old": r[4], "label_new": r[5]}
            for r in rows
        ]

    def pending_dependents(self, category_id: str) -> int:
        """Count of un-undone, succeeded, reversible rows that reference this
        category id as either the object acted on (target_id) or the value to
        restore into (old_value). Deleting such a category would strand those
        undos, so the delete tool refuses when this is > 0."""
        placeholders = ",".join("?" for _ in REVERSIBLE_OPS)
        row = self._conn.execute(
            f"""SELECT COUNT(*) FROM write_journal
                WHERE succeeded=1 AND undone=0
                  AND op_type IN ({placeholders})
                  AND (target_id=? OR old_value=?)""",
            (*REVERSIBLE_OPS, category_id, category_id),
        ).fetchone()
        return row[0] if row else 0

    def mark_undone(self, batch_id, target_id=None) -> None:
        """Mark rows as reversed. With `target_id=None` (default), marks
        every row in the batch (whole-batch close). With a `target_id`,
        marks ONLY that row — used when a compensating write succeeds for one
        row of a batch but not others, so the still-open rows remain undoable
        and can be retried on a subsequent `undo_last` call."""
        if target_id is None:
            self._conn.execute(
                "UPDATE write_journal SET undone=1 WHERE batch_id=?", (batch_id,)
            )
        else:
            self._conn.execute(
                "UPDATE write_journal SET undone=1 WHERE batch_id=? AND target_id=?",
                (batch_id, target_id),
            )
        self._conn.commit()

    def history(self, limit: int = 20) -> list[dict]:
        from collections import Counter
        rows = self._conn.execute(
            """SELECT batch_id, MAX(seq) AS mseq FROM write_journal
               GROUP BY batch_id ORDER BY mseq DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for batch_id, _mseq in rows:
            detail = self._conn.execute(
                """SELECT op_type, label_old, label_new, timestamp, undone
                   FROM write_journal WHERE batch_id=? ORDER BY seq""",
                (batch_id,),
            ).fetchall()
            pairs = Counter((d[0], d[1], d[2]) for d in detail)
            (op_type, old_n, new_n), n = pairs.most_common(1)[0]
            if op_type == "create_category":
                summary = f"＋{new_n} (created) ×{n}"
            elif op_type == "delete_category":
                summary = f"✕ {old_n} (deleted) ×{n}"
            elif op_type == "rename_merchant":
                summary = f"{old_n}→{new_n} (merchant) ×{n}"
            elif op_type == "create_rule":
                summary = f"Rule: contains '{old_n}' → '{new_n}' ×{n}"
            elif op_type == "create_category_rule":
                summary = f"Rule: contains '{old_n}' → category '{new_n}' ×{n}"
            else:
                summary = f"{old_n}→{new_n} ×{n}"
            out.append({
                "batch_id": batch_id,
                "when": detail[0][3],
                "count": len(detail),
                "summary": summary,
                "undone": all(d[4] == 1 for d in detail),
            })
        return out
