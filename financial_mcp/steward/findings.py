"""The Finding — the steward ledger's row and every detector's output.

Mirrors spec §5.3 and the Appendix B JSON Schema. Two invariants matter most:

* **Stable identity.** `id = sha1(detector + subject_key)` where `subject_key`
  is a stable key for the thing the finding is about (account id, normalized
  merchant root, category). The same issue re-detected on a later run produces
  the same id, so the ledger upserts one row instead of piling up duplicates.

* **Adam's priority metric (§5.4).** `priority = distortion × dollars_at_stake
  ÷ actions_to_fix`. `distortion` is boolean — does this land in the wrong
  bucket and change a number Adam would read? If not, priority is 0 (cosmetic,
  backlog). Integrity findings override ranking entirely (handled by the runner,
  not here).
"""
from __future__ import annotations

import datetime
import hashlib
from typing import Literal

from pydantic import BaseModel, Field

Layer = Literal["integrity", "fix", "notice", "decide"]
Confidence = Literal["high", "medium", "needs_input"]
Status = Literal[
    "new", "open", "approved", "resolved", "dismissed", "snoozed", "needs_input"
]

# ^(I|M|P|A)-\d+[a-z]?$ per Appendix B: an integrity/maintenance/pattern/advice
# detector number, with an optional letter suffix (e.g. M-5b).
DETECTOR_PATTERN = r"^(I|M|P|A)-\d+[a-z]?$"


def finding_id(detector: str, subject_key: str) -> str:
    """Stable finding identity: sha1 of detector id + a stable subject key."""
    return hashlib.sha1(f"{detector}{subject_key}".encode()).hexdigest()


def compute_priority(distortion: bool, dollars_at_stake: float, actions_to_fix: int) -> float:
    """Adam's metric: distortion × dollars ÷ actions. Zero when non-distorting.

    actions_to_fix is floored at 1 (the schema minimum) so a caller passing 0
    can never trigger a divide-by-zero; a real Finding is validated to >= 1.
    """
    if not distortion:
        return 0.0
    return dollars_at_stake / max(1, actions_to_fix)


def _now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


class Evidence(BaseModel):
    """The audit trail behind a finding — what to look at to verify it."""

    txn_ids: list[str] = Field(default_factory=list)
    account_ids: list[str] = Field(default_factory=list)
    merchants: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    figures: dict = Field(default_factory=dict)


class ProposedAction(BaseModel):
    """A reversible, dry-run-first write the skill could offer to apply."""

    tool: str
    args: dict = Field(default_factory=dict)
    # Const True: the steward never proposes a write that skips the dry run.
    dry_run_first: Literal[True] = True
    reversible: bool = True


class Finding(BaseModel):
    id: str
    detector: str = Field(pattern=DETECTOR_PATTERN)
    layer: Layer
    title: str
    summary: str = ""
    evidence: Evidence = Field(default_factory=Evidence)
    dollars_at_stake: float = 0.0
    distortion: bool = False
    actions_to_fix: int = Field(ge=1)
    priority: float = 0.0
    confidence: Confidence = "high"
    question: str | None = None
    proposed_action: ProposedAction | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    status: Status = "new"

    @classmethod
    def new(
        cls,
        *,
        detector: str,
        subject_key: str,
        layer: Layer,
        title: str,
        dollars_at_stake: float,
        distortion: bool,
        actions_to_fix: int,
        confidence: Confidence,
        summary: str = "",
        evidence: dict | Evidence | None = None,
        question: str | None = None,
        proposed_action: dict | ProposedAction | None = None,
        status: Status = "new",
    ) -> "Finding":
        """Build a Finding with a stable id, computed priority, and timestamps.

        Detectors call this rather than the raw constructor so id/priority are
        always derived consistently. The detector-pattern check runs here too
        (via the model), so a malformed detector id fails fast.
        """
        now = _now_iso()
        return cls(
            id=finding_id(detector, subject_key),
            detector=detector,
            layer=layer,
            title=title,
            summary=summary,
            evidence=evidence or Evidence(),
            dollars_at_stake=dollars_at_stake,
            distortion=distortion,
            actions_to_fix=actions_to_fix,
            priority=compute_priority(distortion, dollars_at_stake, actions_to_fix),
            confidence=confidence,
            question=question,
            proposed_action=proposed_action,
            first_seen=now,
            last_seen=now,
            status=status,
        )
