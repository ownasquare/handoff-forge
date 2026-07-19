from __future__ import annotations

from datetime import UTC, datetime

from handoff_forge.handoffs.composer import OfflineHandoffComposer
from handoff_forge.handoffs.prompts import build_generation_request
from handoff_forge.models import (
    ArtifactKind,
    BlockKind,
    ContentBlock,
    HandoffMode,
    ModelRoute,
    SourceArtifact,
    TemplateProfile,
)
from handoff_forge.providers.base import evidence_prompt


def _source() -> SourceArtifact:
    return SourceArtifact(
        id="artifact-1",
        project_id="project-1",
        display_name="current-state.mdc",
        sha256="a" * 64,
        media_type="text/markdown",
        size_bytes=120,
        kind=ArtifactKind.MDC,
        stored_path="/tmp/current-state.mdc",
        file_uri="file:///tmp/current-state.mdc",
        created_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )


def _block(order: int, text: str) -> ContentBlock:
    return ContentBlock(
        id=f"block-{order}",
        project_id="project-1",
        artifact_id="artifact-1",
        artifact_sha256="a" * 64,
        kind=BlockKind.TEXT,
        text=text,
        order=order,
        extraction_method="markdown",
    )


def test_offline_composer_builds_all_sections_with_stable_provenance() -> None:
    composer = OfflineHandoffComposer(
        project_id="project-1",
        project_name="Handoff Forge",
        purpose="Preserve project continuity.",
        created_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )
    evidence = {
        1: [
            _block(2, "The project is a local-first continuation system."),
            _block(1, "Goal verified."),
        ],
        8: [_block(3, "Do Not Touch: retain the append-only audit ledger.")],
        10: [_block(4, "[P1] Finish the deterministic merge workflow.")],
    }

    package = composer.compose(
        mode=HandoffMode.POST_TASK,
        profile=TemplateProfile.GOAL_V1,
        sources=[_source()],
        evidence_by_section=evidence,
        current_session_source_ids={"artifact-1"},
    )

    assert [section.id for section in package.sections] == list(range(1, 13))
    assert "[artifact-1#block-1]" in package.sections[0].content
    assert "Needs re-validation" in package.sections[1].content
    assert package.confidence_assessments[0].confidence.value == "low"
    assert package.confidence_assessments[1].confidence.value == "low"


def test_explicit_verification_metadata_can_raise_current_evidence_to_high() -> None:
    composer = OfflineHandoffComposer(
        project_id="project-1",
        project_name="Handoff Forge",
        purpose="Preserve project continuity.",
        created_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )
    verified = _block(1, "Goal verified.").model_copy(
        update={"metadata": {"verified": True, "current_session": True}}
    )

    package = composer.compose(
        mode=HandoffMode.POST_TASK,
        profile=TemplateProfile.GOAL_V1,
        evidence_by_section={1: [verified]},
    )

    assert package.confidence_assessments[0].confidence.value == "high"


def test_generation_prompt_marks_evidence_untrusted_and_reports_omissions() -> None:
    request = build_generation_request(
        section_id=5,
        evidence=[_block(1, "pytest passed locally")],
        route=ModelRoute(provider="offline", model="extractive-v1"),
        omitted_source_count=3,
    )
    assert "untrusted evidence" in request.system_prompt.casefold()
    assert "omit" in request.user_prompt.casefold()
    assert "pytest passed locally" not in request.user_prompt
    assert evidence_prompt(request).count("pytest passed locally") == 1
    assert request.section_id == 5
    assert request.evidence[0].id == "block-1"
