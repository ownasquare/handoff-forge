from __future__ import annotations

from datetime import UTC, datetime

import pytest

from handoff_forge.errors import HandoffValidationError
from handoff_forge.handoffs.composer import OfflineHandoffComposer
from handoff_forge.handoffs.jobs import GenerationJobRunner, InMemoryCheckpointStore
from handoff_forge.handoffs.profiles import render_handoff
from handoff_forge.models import (
    BlockKind,
    ContentBlock,
    GenerationJob,
    GenerationRequest,
    GenerationResult,
    HandoffMode,
    JobStatus,
    ModelRoute,
    TemplateProfile,
)
from handoff_forge.providers.base import evidence_prompt


class FailingOnceGenerator:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.failed = False

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request.section_id)
        if request.section_id == 4 and not self.failed:
            self.failed = True
            raise RuntimeError("temporary provider failure sk-test-secret")
        return GenerationResult(
            text=f"Verified generated content for Section {request.section_id}.",
            provider=request.route.provider,
            model=request.route.model,
        )


class RecordingGenerator:
    def __init__(self, *, fail_section: int | None = None) -> None:
        self.requests: list[GenerationRequest] = []
        self.fail_section = fail_section

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if request.section_id == self.fail_section:
            raise RuntimeError("focused provider failure")
        return GenerationResult(
            text=f"Generated content for Section {request.section_id}.",
            provider=request.route.provider,
            model=request.route.model,
        )


def _runner(
    generator: FailingOnceGenerator | RecordingGenerator,
    *,
    evidence_by_section: dict[int, list[ContentBlock]] | None = None,
) -> GenerationJobRunner:
    composer = OfflineHandoffComposer(
        project_id="project-1",
        project_name="Handoff Forge",
        purpose="Preserve continuation state.",
        created_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )
    return GenerationJobRunner(
        generator=generator,
        checkpoint_store=InMemoryCheckpointStore(),
        composer=composer,
        evidence_by_section=evidence_by_section or {},
        max_retries=0,
    )


def _block(
    block_id: str,
    text: str,
    *,
    order: int = 1,
    metadata: dict[str, object] | None = None,
) -> ContentBlock:
    return ContentBlock(
        id=block_id,
        project_id="project-1",
        artifact_id=f"artifact-{block_id}",
        artifact_sha256="a" * 64,
        kind=BlockKind.TEXT,
        text=text,
        order=order,
        extraction_method="fixture",
        metadata=metadata or {},
    )


def test_resume_keeps_successful_sections_and_sanitizes_errors() -> None:
    generator = FailingOnceGenerator()
    runner = _runner(generator)
    routes = {section_id: ModelRoute() for section_id in range(1, 13)}
    job = runner.create_job(
        mode=HandoffMode.POST_TASK,
        profile=TemplateProfile.GOAL_V1,
        route_matrix=routes,
    )

    failed = runner.run(job.id)
    assert failed.status is JobStatus.FAILED
    assert failed.completed_section_ids == [1, 2, 3]
    assert "sk-test-secret" not in (failed.error or "")
    with pytest.raises(HandoffValidationError, match="partial generation job"):
        runner.package(job.id)

    resumed = runner.resume(job.id)
    assert resumed.status is JobStatus.COMPLETE
    assert generator.calls.count(1) == 1
    assert generator.calls.count(4) == 2
    assert runner.package(job.id).sections[-1].id == 12


def test_cancel_stops_at_a_section_boundary() -> None:
    generator = FailingOnceGenerator()
    generator.failed = True
    runner = _runner(generator)
    job = runner.create_job(
        mode=HandoffMode.PRE_COMPACT,
        profile=TemplateProfile.CODEX_PRECOMPACT_V1,
        route_matrix={section_id: ModelRoute() for section_id in range(1, 13)},
    )
    runner.request_cancel(job.id)
    cancelled = runner.run(job.id)
    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.completed_sections == []


def test_inventory_is_bounded_and_checkpointed_before_section_ten_generation() -> None:
    first = _block(
        "backlog-1",
        "- [P1] Fix deterministic inventory checkpointing.\n"
        + "RAW_EVIDENCE_SENTINEL "
        + ("x" * 9_000),
    )
    second = _block("backlog-2", "Fix the omitted follow-up.", order=2)
    generator = RecordingGenerator(fail_section=10)
    runner = _runner(generator, evidence_by_section={10: [first, second]})
    job = runner.create_job(
        mode=HandoffMode.POST_TASK,
        profile=TemplateProfile.GOAL_V1,
        route_matrix={section_id: ModelRoute() for section_id in range(1, 13)},
    )

    failed = runner.run(job.id)
    request = next(item for item in generator.requests if item.section_id == 10)

    assert failed.status is JobStatus.FAILED
    assert len(failed.inventory) == 1
    assert failed.inventory[0].priority == "P1"
    assert failed.inventory[0].source_refs
    assert failed.inventory[0].id in request.user_prompt
    assert "RAW_EVIDENCE_SENTINEL" not in request.user_prompt
    assert evidence_prompt(request).count("RAW_EVIDENCE_SENTINEL") == 1
    assert sum(len(block.text) for block in request.evidence) <= 8_000
    assert "Omitted by evidence bounds: 1" in request.user_prompt
    assert "Truncated by the character budget: 1" in request.user_prompt


def test_retrieval_alone_is_low_and_explicit_verification_metadata_can_be_high() -> None:
    generator = RecordingGenerator()
    runner = _runner(
        generator,
        evidence_by_section={
            1: [_block("retrieved", "Retrieved project objective.")],
            2: [
                _block(
                    "verified",
                    "Verified current architecture.",
                    metadata={"verified": True, "current_session": True},
                )
            ],
        },
    )
    job = runner.create_job(
        mode=HandoffMode.POST_TASK,
        profile=TemplateProfile.GOAL_V1,
        route_matrix={section_id: ModelRoute() for section_id in range(1, 13)},
    )

    completed = runner.run(job.id)

    assert completed.status is JobStatus.COMPLETE
    assert completed.completed_sections[0].confidence.value == "low"
    assert completed.completed_sections[1].confidence.value == "high"


def test_job_json_to_rendered_mdc_retains_all_twelve_routes() -> None:
    generator = RecordingGenerator()
    runner = _runner(generator)
    routes = {
        section_id: ModelRoute(
            provider=f"provider-{section_id}",
            model=f"model-{section_id}",
            allow_cloud_upload=section_id % 2 == 0,
        )
        for section_id in range(1, 13)
    }
    job = runner.create_job(
        mode=HandoffMode.POST_TASK,
        profile=TemplateProfile.CODEX_POST_CHAT_V1,
        route_matrix=routes,
    )
    completed = runner.run(job.id)
    restored = GenerationJob.model_validate_json(completed.model_dump_json())
    runner.store.save(restored)

    rendered = render_handoff(runner.package(job.id))

    assert rendered.count("**Generation route:**") == 12
    for section_id in range(1, 13):
        assert f"provider-{section_id}" in rendered
        assert f"model-{section_id}" in rendered
