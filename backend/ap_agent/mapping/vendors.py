"""Vendor entity resolution: a printed name -> a single vendor ID (FR-3.3).

An invoice never carries a vendor ID, only whatever name the vendor chose to
print — which drifts by punctuation ("Acme Corp." vs "Acme Corp"), by legal
suffix ("Acme Corporation" vs "Acme Corp Inc"), and by abbreviation ("ACME").
Paying against the wrong vendor row pays the wrong bank account, so resolution
sits squarely inside the class of decisions this project treats as
**deterministic-where-exact, escalate-where-not**.

Two decisions shape this module.

**Confirmed aliases are looked up before anything fuzzy runs.** A name seen
once and confirmed by a human resolves exactly and repeatably on every later
invoice — no re-guessing, no risk that a closer fixture on some other vendor
changes the answer next time (ADR-009).

**A close match to more than one vendor is an escalation, not a tie-break.**
This is the load-bearing case: "Acme Corp" is close to both "Acme Corporation"
(V-1001) and "Acme Industries LLC" (V-1002) — two real, distinct companies with
different bank accounts. Picking the higher-scoring one because it scored
higher is exactly the over-merge this module exists to prevent. When the gap
between the best and second-best match is not decisive, the result is
`AMBIGUOUS`, never a guess.

Matching itself runs in two passes for different reasons:

* **pg_trgm `similarity()`** does the candidate search. It is set-based (all
  trigrams shared, symmetric) and already indexed on `vendors.legal_name`
  (`ix_vendors_legal_name_trgm`), so it finds candidates across a large vendor
  table without scanning it.
* **RapidZZZ's `token_sort_ratio`** re-scores the pg_trgm candidates. It sorts
  words before comparing, so "LLC Acme Industries" and "Acme Industries LLC"
  score identically — pg_trgm's trigram overlap already rewards that case, but
  token-sort makes the reordering-invariance explicit and cheap to reason
  about, rather than relying on trigram overlap doing so as a side effect.

Neither pass alone is what decides a merge: the *margin* between candidates is.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz
from sqlalchemy import text
from sqlalchemy.orm import Session

from ap_agent.persistence.models import VendorAlias

# --- thresholds ---------------------------------------------------------------
#
# Calibrated against the project's own confusable-pair fixture: "Acme
# Corporation" (V-1001) vs "Acme Industries LLC" (V-1002), and the ambiguous
# printed forms "Acme Corp", "ACME", "Acme Co.", "Acme Corp.", and
# "acme corporation ltd" that must resolve to neither with confidence.

PG_TRGM_CANDIDATE_THRESHOLD: Final[float] = 0.3
"""Minimum `similarity()` for pg_trgm to surface a row as a candidate at all.

Below the pg_trgm module default (0.3) a match is not worth re-scoring — at
that point two names share almost no trigrams and RapidZZZ would only be
polishing noise.
"""

AUTO_RESOLVE_SCORE: Final[float] = 0.90
"""Minimum re-scored similarity for the top candidate to resolve automatically.

Below this, the reading is closer to "resembles a vendor" than "is that
vendor," and guessing risks paying the wrong company.
"""

AMBIGUITY_MARGIN: Final[float] = 0.05
"""Minimum score gap the top candidate must hold over the runner-up.

The one number doing the real work in this module. "Acme Corp" scores highly
against *both* Acme Corporation and Acme Industries LLC — both contain "Acme"
and a truncated legal suffix — so the top score alone cannot distinguish a
genuine match from a genuine ambiguity. Requiring a decisive margin over the
second-best candidate is what forces the ambiguous case to escalate instead of
being decided by whichever row happens to score a fraction higher.
"""

MAX_CANDIDATES: Final[int] = 8
"""How many pg_trgm candidates to re-score. Bounds the RapidZZZ pass; pg_trgm's
index already did the expensive part."""


class ResolutionOutcome(StrEnum):
    """What resolution concluded, for the caller to branch on."""

    ALIAS_MATCH = "alias_match"
    """A previously confirmed alias. Deterministic — no scoring involved."""

    RESOLVED = "resolved"
    """A single fuzzy candidate cleared both the score and margin bars."""

    AMBIGUOUS = "ambiguous"
    """Two or more candidates are both plausible. Escalate; do not guess."""

    NOT_FOUND = "not_found"
    """No candidate cleared even the pg_trgm floor. Likely a genuinely new vendor."""


class VendorCandidate(BaseModel):
    """One scored candidate, kept for the reviewer's benefit.

    Even a confident resolution keeps its runner-up: seeing *why* a name was not
    confused with a similar vendor is part of what makes the decision
    inspectable rather than asserted.
    """

    model_config = ConfigDict(frozen=True)

    vendor_id: str
    legal_name: str
    display_name: str | None = None
    score: float = Field(ge=0.0, le=1.0)


class VendorResolution(BaseModel):
    """The result of resolving one printed name."""

    model_config = ConfigDict(frozen=True)

    printed_name: str
    outcome: ResolutionOutcome
    vendor_id: str | None = None
    """Set only for ALIAS_MATCH and RESOLVED."""
    candidates: tuple[VendorCandidate, ...] = ()
    """All candidates considered, best first. Populated for every outcome except
    ALIAS_MATCH, where scoring never ran."""
    reasoning: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.outcome in (ResolutionOutcome.ALIAS_MATCH, ResolutionOutcome.RESOLVED)


def normalise_name(name: str) -> str:
    """Comparison form: casefolded, accents stripped, punctuation collapsed to
    spaces, whitespace collapsed.

    Used for both the alias table's `alias_norm` column and as a pre-filter
    before scoring — matching what the schema's unique constraint already
    normalises on, so a lookup here and a lookup at write time agree.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    collapsed = re.sub(r"[^\w\s]", " ", stripped, flags=re.UNICODE)
    return " ".join(collapsed.casefold().split())


def _lookup_alias(session: Session, tenant_id: str, printed_name: str) -> VendorAlias | None:
    """Exact, deterministic lookup against confirmed aliases.

    Runs before any scoring. A confirmed alias is not a hint to weigh against
    fuzzy candidates — it is the answer a human already gave for this exact
    string, and re-deriving it would let a later coincidental fuzzy match
    override a settled decision.
    """
    norm = normalise_name(printed_name)
    return (
        session.query(VendorAlias)
        .filter(VendorAlias.tenant_id == tenant_id, VendorAlias.alias_norm == norm)
        .one_or_none()
    )


def _pg_trgm_candidates(
    session: Session, tenant_id: str, printed_name: str
) -> list[tuple[str, str, str | None, float]]:
    """Candidate vendors via the trigram index, coarse-ranked.

    Returns (vendor_id, legal_name, display_name, pg_trgm_similarity). The
    coarse score is not the final answer — `_resolve` re-scores every row with
    RapidZZZ — but it is what lets this query use
    `ix_vendors_legal_name_trgm` instead of a sequential scan.
    """
    rows = session.execute(
        text(
            "SELECT vendor_id, legal_name, display_name, "
            "similarity(legal_name, :name) AS sim "
            "FROM vendors "
            "WHERE tenant_id = :tenant_id "
            "AND status != 'blocked' "
            "AND similarity(legal_name, :name) > :floor "
            "ORDER BY sim DESC "
            "LIMIT :limit"
        ),
        {
            "tenant_id": tenant_id,
            "name": printed_name,
            "floor": PG_TRGM_CANDIDATE_THRESHOLD,
            "limit": MAX_CANDIDATES,
        },
    ).all()
    return [(r.vendor_id, r.legal_name, r.display_name, float(r.sim)) for r in rows]


def resolve_vendor(session: Session, tenant_id: str, printed_name: str) -> VendorResolution:
    """Resolve a printed vendor name to a single vendor, or explain why not.

    `NOT_FOUND` and `AMBIGUOUS` are both legitimate, common answers, not
    failures of the function — they are exactly the cases FR-3.3 requires a
    human to settle rather than software guessing.
    """
    printed = printed_name.strip()
    if not printed:
        return VendorResolution(
            printed_name=printed_name,
            outcome=ResolutionOutcome.NOT_FOUND,
            reasoning="Empty vendor name; nothing to resolve.",
        )

    alias = _lookup_alias(session, tenant_id, printed)
    if alias is not None:
        return VendorResolution(
            printed_name=printed_name,
            outcome=ResolutionOutcome.ALIAS_MATCH,
            vendor_id=alias.vendor_id,
            reasoning=(
                f"{printed_name!r} matches a confirmed alias for vendor "
                f"{alias.vendor_id!r} (confirmed {alias.confirmed_at})."
            ),
        )

    rows = _pg_trgm_candidates(session, tenant_id, printed)
    if not rows:
        return VendorResolution(
            printed_name=printed_name,
            outcome=ResolutionOutcome.NOT_FOUND,
            reasoning=(
                f"No vendor's legal name shares meaningful text with {printed_name!r} "
                f"(pg_trgm floor {PG_TRGM_CANDIDATE_THRESHOLD}). Likely a new vendor."
            ),
        )

    printed_norm = normalise_name(printed)
    scored = sorted(
        (
            VendorCandidate(
                vendor_id=vendor_id,
                legal_name=legal_name,
                display_name=display_name,
                score=fuzz.token_sort_ratio(printed_norm, normalise_name(legal_name)) / 100.0,
            )
            for vendor_id, legal_name, display_name, _pg_sim in rows
        ),
        key=lambda c: c.score,
        reverse=True,
    )

    best = scored[0]
    runner_up = scored[1] if len(scored) > 1 else None

    if best.score < AUTO_RESOLVE_SCORE:
        return VendorResolution(
            printed_name=printed_name,
            outcome=ResolutionOutcome.NOT_FOUND,
            candidates=tuple(scored),
            reasoning=(
                f"Best candidate {best.legal_name!r} scored {best.score:.2f}, below "
                f"the auto-resolve floor {AUTO_RESOLVE_SCORE}. Not confident enough "
                "to treat as a match."
            ),
        )

    if runner_up is not None and (best.score - runner_up.score) < AMBIGUITY_MARGIN:
        return VendorResolution(
            printed_name=printed_name,
            outcome=ResolutionOutcome.AMBIGUOUS,
            candidates=tuple(scored),
            reasoning=(
                f"{printed_name!r} matches both {best.legal_name!r} "
                f"({best.score:.2f}) and {runner_up.legal_name!r} "
                f"({runner_up.score:.2f}) within the ambiguity margin "
                f"({AMBIGUITY_MARGIN}). A confident guess here could pay the "
                "wrong company; escalating for human confirmation."
            ),
        )

    return VendorResolution(
        printed_name=printed_name,
        outcome=ResolutionOutcome.RESOLVED,
        vendor_id=best.vendor_id,
        candidates=tuple(scored),
        reasoning=(
            f"{printed_name!r} resolves to {best.legal_name!r} "
            f"(score {best.score:.2f}"
            + (f", runner-up {runner_up.score:.2f}" if runner_up else "")
            + ")."
        ),
    )


def confirm_alias(
    session: Session,
    *,
    tenant_id: str,
    vendor_id: str,
    printed_name: str,
    confirmed_by: str,
) -> VendorAlias:
    """Persist a human-confirmed alias (FR-3.2 applied to vendor identity).

    Idempotent on the normalised name: confirming the same printed name twice
    updates who/when confirmed it rather than raising on the unique
    constraint, since a re-confirmation is not an error.

    Not used to record an *automatic* `RESOLVED` outcome — only the caller's
    explicit human confirmation should mint a permanent alias, matching FR-3.2's
    propose-once-confirm-once-persist sequence for the analogous field-alias
    case.
    """
    norm = normalise_name(printed_name)
    existing = (
        session.query(VendorAlias)
        .filter(VendorAlias.tenant_id == tenant_id, VendorAlias.alias_norm == norm)
        .one_or_none()
    )
    now = datetime.now(UTC)
    if existing is not None:
        existing.vendor_id = vendor_id
        existing.confirmed_by = confirmed_by
        existing.confirmed_at = now
        return existing

    alias = VendorAlias(
        tenant_id=tenant_id,
        vendor_id=vendor_id,
        alias_raw=printed_name.strip(),
        alias_norm=norm,
        confirmed_by=confirmed_by,
        confirmed_at=now,
    )
    session.add(alias)
    return alias
