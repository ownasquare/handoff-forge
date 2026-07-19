"""Evidence-based confidence and freshness assessment."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from handoff_forge.models import (
    ConfidenceAssessment,
    ConfidenceLevel,
    ContentBlock,
    HandoffSection,
)


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    """Verification state used to classify a section without provider assumptions."""

    source_id: str
    verified: bool
    current_session: bool
    observed_at: datetime | None = None
    needs_revalidation: bool = False


def assess_section_confidence(
    section_id: int,
    observations: Iterable[EvidenceObservation],
    *,
    now: datetime | None = None,
) -> ConfidenceAssessment:
    """Classify freshness conservatively.

    High is deliberately impossible without verified current-session evidence.
    An explicit revalidation requirement wins over otherwise-current evidence.
    """

    if not 1 <= section_id <= 11:
        raise ValueError("confidence assessments cover Sections 1 through 11 only")
    checked = tuple(observations)
    current = now or datetime.now(UTC)
    if not checked or any(item.needs_revalidation for item in checked):
        return ConfidenceAssessment(
            section_id=section_id,
            confidence=ConfidenceLevel.LOW,
            basis="Evidence is missing or explicitly needs re-validation.",
        )
    if all(item.verified and item.current_session for item in checked):
        return ConfidenceAssessment(
            section_id=section_id,
            confidence=ConfidenceLevel.HIGH,
            basis=(
                "Recently verified in the current session from "
                f"{len(checked)} evidence reference(s) as of {current.isoformat()}."
            ),
        )
    if all(item.verified for item in checked):
        newest = max(
            (item.observed_at for item in checked if item.observed_at is not None),
            default=None,
        )
        suffix = f" Newest observation: {newest.isoformat()}." if newest else ""
        return ConfidenceAssessment(
            section_id=section_id,
            confidence=ConfidenceLevel.MEDIUM,
            basis="Verified evidence exists, but it is from an earlier session or older source."
            + suffix,
        )
    return ConfidenceAssessment(
        section_id=section_id,
        confidence=ConfidenceLevel.LOW,
        basis="Evidence exists but has not been verified; re-validation is required.",
    )


def explicit_verification_source_ids(
    evidence: Iterable[ContentBlock],
) -> tuple[set[str], set[str], set[str]]:
    """Return artifact IDs supported by explicit per-block verification metadata.

    Every selected block for an artifact must explicitly carry the relevant true flag before
    that artifact is considered verified or current-session. Any revalidation flag wins.
    Truthy strings are deliberately rejected; only the boolean value ``True`` is accepted.
    """

    grouped: dict[str, list[ContentBlock]] = defaultdict(list)
    for block in evidence:
        grouped[block.artifact_id].append(block)
    verified: set[str] = set()
    current_session: set[str] = set()
    needs_revalidation: set[str] = set()
    for source_id, blocks in grouped.items():
        if any(_verification_flag(block, "needs_revalidation") for block in blocks):
            needs_revalidation.add(source_id)
            continue
        if all(_verification_flag(block, "verified") for block in blocks):
            verified.add(source_id)
        if all(_verification_flag(block, "current_session") for block in blocks):
            current_session.add(source_id)
    return verified, current_session, needs_revalidation


def _verification_flag(block: ContentBlock, key: str) -> bool:
    direct = block.metadata.get(key)
    nested = block.metadata.get("verification")
    if direct is True:
        return True
    return isinstance(nested, Mapping) and nested.get(key) is True


def assessments_from_sections(sections: Iterable[HandoffSection]) -> list[ConfidenceAssessment]:
    """Create canonical Section 1-11 assessments from composed section metadata."""

    by_id = {section.id: section for section in sections if section.id <= 11}
    if set(by_id) != set(range(1, 12)):
        raise ValueError("sections must contain IDs 1 through 11 before confidence assessment")
    return [
        ConfidenceAssessment(
            section_id=section_id,
            confidence=by_id[section_id].confidence,
            basis=by_id[section_id].freshness_basis,
        )
        for section_id in range(1, 12)
    ]
