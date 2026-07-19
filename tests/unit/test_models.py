"""Canonical model invariants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from handoff_forge.models import (
    ConfidenceAssessment,
    ConfidenceLevel,
    HandoffMode,
    HandoffPackage,
    HandoffSection,
    ModelRoute,
    TemplateProfile,
)


def _sections() -> list[HandoffSection]:
    return [
        HandoffSection(
            id=section_id,
            title=f"Section {section_id}",
            content="Verified content",
            confidence=ConfidenceLevel.HIGH,
            freshness_basis="current fixture",
        )
        for section_id in range(1, 13)
    ]


def _assessments() -> list[ConfidenceAssessment]:
    return [
        ConfidenceAssessment(
            section_id=section_id,
            confidence=ConfidenceLevel.HIGH,
            basis="current fixture",
        )
        for section_id in range(1, 12)
    ]


def test_section_twelve_does_not_assess_itself() -> None:
    package = HandoffPackage(
        id="pkg_1",
        project_id="prj_1",
        project_name="Project",
        purpose="Continue safely",
        mode=HandoffMode.PRE_COMPACT,
        profile=TemplateProfile.GOAL_V1,
        created_at=datetime.now(UTC),
        sections=_sections(),
        confidence_assessments=_assessments(),
    )
    assert [item.section_id for item in package.confidence_assessments] == list(range(1, 12))


def test_reordered_or_recursive_assessment_contract_is_rejected() -> None:
    bad = _assessments()
    bad[-1] = bad[-1].model_copy(update={"section_id": 12})
    with pytest.raises(ValidationError, match="Sections 1 through 11"):
        HandoffPackage(
            id="pkg_1",
            project_id="prj_1",
            project_name="Project",
            purpose="Continue safely",
            mode=HandoffMode.PRE_COMPACT,
            profile=TemplateProfile.GOAL_V1,
            sections=_sections(),
            confidence_assessments=bad,
        )


def test_scheduled_package_requires_next_run_mode() -> None:
    with pytest.raises(ValidationError, match="next_run_mode"):
        HandoffPackage(
            id="pkg_1",
            project_id="prj_1",
            project_name="Project",
            purpose="Continue safely",
            mode=HandoffMode.PRE_COMPACT,
            profile=TemplateProfile.CODEX_PRECOMPACT_V1,
            sections=_sections(),
            confidence_assessments=_assessments(),
            scheduled=True,
        )


def test_model_routes_are_normalized_and_markdown_safe() -> None:
    route = ModelRoute(provider=" OpenAI ", model="gpt-5.2:reasoning")

    assert route.provider == "openai"
    assert route.model == "gpt-5.2:reasoning"
    assert route.include_visual_evidence is False
    assert (
        ModelRoute(
            provider="openai",
            model="gpt-5.2:reasoning",
            include_visual_evidence=True,
        ).include_visual_evidence
        is True
    )
    with pytest.raises(ValidationError, match="String should match pattern"):
        ModelRoute(provider="openai", model="gpt-safe`\n# injected")
