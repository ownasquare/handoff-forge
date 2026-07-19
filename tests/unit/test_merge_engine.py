from __future__ import annotations

from datetime import UTC, datetime

import pytest

from handoff_forge.errors import MergeError
from handoff_forge.handoffs.catalog import HANDOFF_SECTION_SPECS
from handoff_forge.merge.engine import MergeEngine
from handoff_forge.merge.planner import render_unified_execution_plan
from handoff_forge.models import (
    ConfidenceAssessment,
    ConfidenceLevel,
    HandoffMode,
    HandoffPackage,
    HandoffSection,
    TemplateProfile,
)


def _package(
    package_id: str,
    *,
    project_id: str = "project-alpha",
    status: str,
    port: int,
    constraint: str,
    priority: str,
) -> HandoffPackage:
    sections = []
    for spec in HANDOFF_SECTION_SPECS:
        content = f"Shared section {spec.id} evidence."
        if spec.id == 2:
            content = f"API status is {status}. The API listens on port {port}."
        elif spec.id == 5:
            content = "Run pytest, profile validation, and rendered browser proof."
        elif spec.id == 8:
            content = constraint
        elif spec.id == 10:
            content = f"- [{priority}] Finish the API handoff validation."
        sections.append(
            HandoffSection(
                id=spec.id,
                title=spec.title,
                content=content,
                confidence=ConfidenceLevel.HIGH,
                freshness_basis="Recently verified in this session from current-session evidence.",
            )
        )
    assessments = [
        ConfidenceAssessment(
            section_id=section_id,
            confidence=ConfidenceLevel.HIGH,
            basis="Recently verified in this session from current-session evidence.",
        )
        for section_id in range(1, 12)
    ]
    return HandoffPackage(
        id=package_id,
        project_id=project_id,
        project_name="Project Alpha",
        purpose="Ship a safe continuation workflow.",
        mode=HandoffMode.POST_TASK,
        profile=TemplateProfile.GOAL_V1,
        created_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        sections=sections,
        confidence_assessments=assessments,
    )


def test_merge_requires_two_unique_handoffs() -> None:
    alpha = _package(
        "alpha",
        status="complete",
        port=8000,
        constraint="Do Not Touch: preserve security audit logs.",
        priority="P1",
    )
    with pytest.raises(MergeError, match="two unique handoffs"):
        MergeEngine().merge([alpha, alpha])


def test_merge_is_order_independent_and_preserves_conflicts_and_constraints() -> None:
    alpha = _package(
        "alpha",
        status="complete",
        port=8000,
        constraint="Do Not Touch: preserve security audit logs.",
        priority="P1",
    )
    beta = _package(
        "beta",
        status="blocked",
        port=9000,
        constraint="Never delete the rollback snapshot.",
        priority="P0",
    )

    first = MergeEngine().merge([alpha, beta])
    second = MergeEngine().merge([beta, alpha])

    assert first.content_hash == second.content_hash
    assert [conflict.model_dump() for conflict in first.conflicts] == [
        conflict.model_dump() for conflict in second.conflicts
    ]
    assert first.conflicts
    assert all(conflict.source_refs for conflict in first.conflicts)
    assert any("Do Not Touch" in item.text for item in first.preserved_constraints)
    assert any("rollback snapshot" in item.text for item in first.preserved_constraints)
    assert "[S1#2]" in first.package.sections[1].content
    assert "### Preserved constraints" in first.package.sections[7].content
    assert first.tasks[0].priority == "P0"
    plan = render_unified_execution_plan(first)
    assert "## Unified Execution Plan" in plan
    assert "### Immediate task" in plan
    assert "### Conflict decisions" in plan


def test_strict_merge_rejects_unrelated_projects() -> None:
    alpha = _package(
        "alpha",
        status="complete",
        port=8000,
        constraint="Do Not Touch: preserve security audit logs.",
        priority="P1",
    )
    unrelated = _package(
        "other",
        project_id="different-project",
        status="complete",
        port=8000,
        constraint="Preserve customer exports.",
        priority="P2",
    )
    with pytest.raises(MergeError, match="unrelated projects"):
        MergeEngine().merge([alpha, unrelated])
