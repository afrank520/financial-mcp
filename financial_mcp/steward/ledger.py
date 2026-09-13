"""The findings ledger — SQLite at ~/.financial_mcp/findings.db (spec §5.2).

Gives findings memory across runs:

* **Stable upsert.** A re-detected issue (same `Finding.id`) updates one row —
  first_seen is preserved, last_seen/dollars/priority refreshed, times_shown++.
* **Suppression.** A finding dismissed-with-reason stays hidden until its dollars
  materially change (+50% *and* ≥ $250 vs the amount at dismissal). A snoozed
  finding reappears strictly after its snooze_until date.
* **Auto-resolve.** When a detector that ran no longer produces a finding it
  previously did, that finding is marked resolved (`resolved_by: "data"`). Only
  detectors that actually ran this cycle can auto-resolve their findings.
* **Backlog.** A finding surfaced 3× while still unactioned (new/open) drops to
  backlog — returned separately so the brief's top-N stays uncluttered.

It also remembers each account's carry-forward flag between runs so I-3 can spot
a false→true transition.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from collections.abc import Callable

from financial_mcp.config import ensure_parent
from financial_mcp.steward.findings import Finding

SCHEMA_VERSION = 1

# suppression re-surface trigger (spec §5.2)
_RESURFACE_PCT = 0.5
_RESURFACE_MIN_ABS = 250.0

# backlog demotion threshold (spec §5.2)
_BACKLOG_SHOWINGS = 3
_ACTIVE_STATUSES = ("new", "open", "approved", "needs_input")

# the lifecycle statuses a manual update may set — suppression/auto-resolve/backlog
# all key off exact status strings, so a typo must be rejected, not stored.
_VALID_STATUSES = frozenset(
    {"new", "open", "approved", "resolved", "dismissed", "snoozed", "needs_input"}
)


def _default_now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


class Ledger:
    def __init__(
        self,
        db_path: str,
        now: Callable[[], datetime.datetime] = _default_now,
    ) -> None:
        ensure_parent(db_path)
        self._now = now
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS findings (
                id TEXT PRIMARY KEY,
                detector TEXT NOT NULL,
                layer TEXT NOT NULL,
                status TEXT NOT NULL,
                priority REAL NOT NULL,
                dollars_at_stake REAL NOT NULL,
                confidence TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                times_shown INTEGER NOT NULL,
                dismissed_dollars REAL,
                snooze_until TEXT,
                dismiss_reason TEXT,
                resolved_by TEXT,
                payload TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_state (
                account_id TEXT PRIMARY KEY,
                is_carry_forward INTEGER NOT NULL,
                observed_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    # --- helpers ----------------------------------------------------------
    def _iso(self) -> str:
        return self._now().isoformat()

    def _get_row(self, finding_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM findings WHERE id=?", (finding_id,)
        ).fetchone()

    @staticmethod
    def _is_backlog(status: str, times_shown: int) -> bool:
        return status in ("new", "open") and times_shown >= _BACKLOG_SHOWINGS

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        # The payload holds the full Finding as last detected; ledger columns are
        # the source of truth for lifecycle fields, so overlay them.
        d = json.loads(row["payload"])
        d.update(
            id=row["id"],
            status=row["status"],
            priority=row["priority"],
            dollars_at_stake=row["dollars_at_stake"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )
        d["times_shown"] = row["times_shown"]
        d["dismissed_dollars"] = row["dismissed_dollars"]
        d["snooze_until"] = row["snooze_until"]
        d["dismiss_reason"] = row["dismiss_reason"]
        d["resolved_by"] = row["resolved_by"]
        d["is_backlog"] = self._is_backlog(row["status"], row["times_shown"])
        return d

    def get(self, finding_id: str) -> dict | None:
        row = self._get_row(finding_id)
        return self._row_to_dict(row) if row else None

    def list(
        self,
        status: str | None = None,
        detector: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        q = "SELECT * FROM findings"
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if detector:
            clauses.append("detector=?")
            params.append(detector)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY priority DESC, last_seen DESC"
        if limit is not None:
            q += " LIMIT ?"
            params.append(limit)
        return [self._row_to_dict(r) for r in self._conn.execute(q, params).fetchall()]

    # --- write side (manual status changes) -------------------------------
    def update(
        self,
        finding_id: str,
        status: str,
        reason: str | None = None,
        snooze_until: str | None = None,
    ) -> dict:
        row = self._get_row(finding_id)
        if row is None:
            raise KeyError(finding_id)
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"unknown status '{status}'; expected one of {sorted(_VALID_STATUSES)}"
            )
        if status == "dismissed" and not reason:
            raise ValueError("dismissal requires a reason")
        if status == "snoozed" and not snooze_until:
            raise ValueError("snooze requires a snooze_until date")

        dismissed_dollars = row["dismissed_dollars"]
        resolved_by = row["resolved_by"]
        if status == "dismissed":
            # record the dollars at dismissal so we can detect a material change
            dismissed_dollars = row["dollars_at_stake"]
        if status == "resolved":
            resolved_by = "user"

        self._conn.execute(
            "UPDATE findings SET status=?, dismiss_reason=?, snooze_until=?, "
            "dismissed_dollars=?, resolved_by=?, last_seen=? WHERE id=?",
            (
                status,
                reason if status == "dismissed" else row["dismiss_reason"],
                snooze_until if status == "snoozed" else None,
                dismissed_dollars,
                resolved_by,
                self._iso(),
                finding_id,
            ),
        )
        self._conn.commit()
        return self.get(finding_id)

    # --- run-time reconciliation ------------------------------------------
    def _insert_new(self, f: Finding) -> None:
        # Honor the detector's status (e.g. I-8 opens as "needs_input" so it lands
        # in the needs_input bucket and is exempt from backlog demotion); default
        # is "new" via the Finding model.
        now = self._iso()
        self._conn.execute(
            "INSERT INTO findings (id, detector, layer, status, priority, "
            "dollars_at_stake, confidence, first_seen, last_seen, times_shown, "
            "dismissed_dollars, snooze_until, dismiss_reason, resolved_by, "
            "payload, schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f.id, f.detector, f.layer, f.status, f.priority, f.dollars_at_stake,
                f.confidence, now, now, 1, None, None, None, None,
                f.model_dump_json(), SCHEMA_VERSION,
            ),
        )

    def _refresh(self, f: Finding, *, status: str, times_shown: int) -> None:
        """Update a re-detected finding's mutable fields (first_seen untouched)."""
        self._conn.execute(
            "UPDATE findings SET status=?, layer=?, priority=?, dollars_at_stake=?, "
            "confidence=?, last_seen=?, times_shown=?, payload=? WHERE id=?",
            (
                status, f.layer, f.priority, f.dollars_at_stake, f.confidence,
                self._iso(), times_shown, f.model_dump_json(), f.id,
            ),
        )

    def _suppression_lifts(self, row: sqlite3.Row, new_dollars: float) -> bool:
        base = row["dismissed_dollars"] or 0.0
        return new_dollars >= base * (1 + _RESURFACE_PCT) and (new_dollars - base) >= _RESURFACE_MIN_ABS

    def _snooze_expired(self, row: sqlite3.Row) -> bool:
        raw = row["snooze_until"]
        if not raw:
            return True
        until = datetime.date.fromisoformat(raw[:10])
        return self._now().date() > until

    def reconcile(self, findings: list[Finding], detectors_run: list[str]) -> dict:
        """Reconcile a fresh detection set against the ledger.

        Returns {surfaced, suppressed, auto_resolved, backlog} lists of finding
        dicts. Findings from a detector in `detectors_run` that are absent from
        `findings` and still active are auto-resolved.
        """
        surfaced, suppressed, backlog = [], [], []
        seen_ids = set()

        for f in findings:
            seen_ids.add(f.id)
            row = self._get_row(f.id)
            if row is None:
                self._insert_new(f)
                surfaced.append(self.get(f.id))
                continue

            status = row["status"]
            if status == "dismissed":
                if self._suppression_lifts(row, f.dollars_at_stake):
                    # material change -> re-open. Reset times_shown so this freshly
                    # re-important finding surfaces rather than routing straight to
                    # backlog on a stale prior count.
                    self._refresh(f, status="open", times_shown=1)
                    self._route(f.id, surfaced, backlog)
                else:
                    # stay suppressed: refresh current dollars for tracking, keep status
                    self._refresh(f, status="dismissed", times_shown=row["times_shown"])
                    suppressed.append(self.get(f.id))
            elif status == "snoozed":
                if self._snooze_expired(row):
                    self._clear_snooze(f.id)
                    self._refresh(f, status="open", times_shown=1)  # fresh window post-snooze
                    self._route(f.id, surfaced, backlog)
                else:
                    suppressed.append(self.get(f.id))
            elif status == "resolved":
                # the problem came back after resolution -> re-open with a fresh count
                self._refresh(f, status="open", times_shown=1)
                self._route(f.id, surfaced, backlog)
            else:  # new / open / approved / needs_input
                self._refresh(f, status=status, times_shown=row["times_shown"] + 1)
                self._route(f.id, surfaced, backlog)

        auto_resolved = self._auto_resolve(detectors_run, seen_ids)
        self._conn.commit()
        return {
            "surfaced": surfaced,
            "suppressed": suppressed,
            "auto_resolved": auto_resolved,
            "backlog": backlog,
        }

    def _route(self, finding_id: str, surfaced: list, backlog: list) -> None:
        d = self.get(finding_id)
        (backlog if d["is_backlog"] else surfaced).append(d)

    def _clear_snooze(self, finding_id: str) -> None:
        self._conn.execute(
            "UPDATE findings SET snooze_until=NULL WHERE id=?", (finding_id,)
        )

    def _auto_resolve(self, detectors_run: list[str], seen_ids: set[str]) -> list[dict]:
        run = set(detectors_run)
        rows = self._conn.execute(
            "SELECT * FROM findings WHERE status IN (%s)"
            % ",".join("?" * len(_ACTIVE_STATUSES)),
            _ACTIVE_STATUSES,
        ).fetchall()
        resolved: list[dict] = []
        for row in rows:
            if row["detector"] in run and row["id"] not in seen_ids:
                self._conn.execute(
                    "UPDATE findings SET status='resolved', resolved_by='data', "
                    "last_seen=? WHERE id=?",
                    (self._iso(), row["id"]),
                )
                resolved.append(self.get(row["id"]))
        return resolved

    # --- per-account carry-forward memory (for I-3) -----------------------
    def previous_carry_forward(self) -> dict[str, bool]:
        rows = self._conn.execute(
            "SELECT account_id, is_carry_forward FROM account_state"
        ).fetchall()
        return {r["account_id"]: bool(r["is_carry_forward"]) for r in rows}

    def record_account_state(self, states: dict[str, bool]) -> None:
        now = self._iso()
        for account_id, cf in states.items():
            self._conn.execute(
                "INSERT INTO account_state (account_id, is_carry_forward, observed_at) "
                "VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET "
                "is_carry_forward=excluded.is_carry_forward, observed_at=excluded.observed_at",
                (account_id, 1 if cf else 0, now),
            )
        self._conn.commit()
