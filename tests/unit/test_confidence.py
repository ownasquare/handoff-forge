from __future__ import annotations

from datetime import UTC, datetime, timedelta

from handoff_forge.handoffs.confidence import (
    EvidenceObservation,
    assess_section_confidence,
)
from handoff_forge.models import ConfidenceLevel

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def test_high_requires_verified_current_session_evidence() -> None:
    assessment = assess_section_confidence(
        1,
        [EvidenceObservation("source-1", verified=True, current_session=True, observed_at=NOW)],
        now=NOW,
    )
    assert assessment.confidence is ConfidenceLevel.HIGH
    assert "current session" in assessment.basis


def test_older_verified_evidence_is_medium_and_missing_evidence_is_low() -> None:
    older = assess_section_confidence(
        2,
        [
            EvidenceObservation(
                "source-2",
                verified=True,
                current_session=False,
                observed_at=NOW - timedelta(days=2),
            )
        ],
        now=NOW,
    )
    missing = assess_section_confidence(3, [], now=NOW)
    assert older.confidence is ConfidenceLevel.MEDIUM
    assert missing.confidence is ConfidenceLevel.LOW
    assert "re-validation" in missing.basis


def test_explicit_revalidation_pressure_prevents_high_confidence() -> None:
    assessment = assess_section_confidence(
        4,
        [
            EvidenceObservation(
                "source-3",
                verified=True,
                current_session=True,
                observed_at=NOW,
                needs_revalidation=True,
            )
        ],
        now=NOW,
    )
    assert assessment.confidence is ConfidenceLevel.LOW


def test_mixed_verified_and_merely_retrieved_evidence_stays_low() -> None:
    assessment = assess_section_confidence(
        5,
        [
            EvidenceObservation("verified", verified=True, current_session=True, observed_at=NOW),
            EvidenceObservation("retrieved", verified=False, current_session=False),
        ],
        now=NOW,
    )

    assert assessment.confidence is ConfidenceLevel.LOW
