from __future__ import annotations

import pytest

from handoff_forge.application import HandoffApplication
from handoff_forge.harnesses.lifecycle import (
    CodexLifecycleAdapter,
    LifecycleStateStore,
    lifecycle_job_id,
)
from handoff_forge.models import GenerationJob, HandoffMode, TemplateProfile


@pytest.mark.parametrize(
    ("event", "profile"),
    [
        (HandoffMode.PRE_COMPACT, TemplateProfile.CODEX_PRECOMPACT_V1),
        (HandoffMode.POST_TASK, TemplateProfile.CODEX_POST_CHAT_V1),
    ],
)
def test_real_application_lifecycle_generation_is_exactly_once_and_restart_readable(
    settings,
    event: HandoffMode,
    profile: TemplateProfile,
) -> None:
    application = HandoffApplication(settings=settings)
    project = application.create_project("Lifecycle proof", "Preserve current work offline.")
    application.ingest_bytes(
        project.id,
        "current-state.md",
        b"# Current state\n\nTests pass. Next step: preserve this context.\n",
    )
    workspace = settings.data_root.parent / "workspace"
    workspace.mkdir()
    transcript = workspace / "codex-transcript.jsonl"
    transcript.write_text('{"type":"fixture"}\n', encoding="utf-8")
    state = LifecycleStateStore(settings.data_root)
    binding = state.create_binding(project_id=project.id, workspace=workspace)

    def generate(selected_binding, selected_event, event_id):
        return application.generate_lifecycle_handoff(
            selected_binding.project_id,
            event=selected_event,
            lifecycle_event_id=event_id,
        )

    adapter = CodexLifecycleAdapter(state_store=state, generator=generate)
    payload = {
        "session_id": "session-integration",
        "turn_id": "turn-integration",
        "cwd": str(workspace),
        "hook_event_name": "PreCompact",
        "trigger": "auto",
        "model": "fixture-model",
        "transcript_path": str(transcript),
    }

    if event is HandoffMode.PRE_COMPACT:
        first = adapter.handle(payload, binding.id)
        second = adapter.handle(payload, binding.id)
    else:
        first = adapter.run_explicit(
            event=event,
            binding_id=binding.id,
            cwd=workspace,
            event_key="post-task-integration",
        )
        second = adapter.run_explicit(
            event=event,
            binding_id=binding.id,
            cwd=workspace,
            event_key="post-task-integration",
        )
    outputs = application.list_outputs(project.id)

    assert first == second
    assert len(outputs) == 1
    assert str(outputs[0].stored_path.resolve()) in first["systemMessage"]
    assert application.validate_output(
        project.id,
        outputs[0].id,
        profile,
    ).valid

    # Simulate a crash after the output manifest committed but before the job stored its path.
    receipt = state.list_receipts()[0]
    job_id = lifecycle_job_id(receipt.id)
    job = GenerationJob.model_validate(application.store.read_job_checkpoint(project.id, job_id))
    application.store.write_job_checkpoint(
        project.id,
        job_id,
        job.model_copy(update={"output_path": None}),
    )

    replayed = application.generate_lifecycle_handoff(
        project.id,
        event=event,
        lifecycle_event_id=receipt.id,
    )
    restarted = HandoffApplication(settings=settings)

    assert replayed.output_id == outputs[0].id
    assert len(restarted.list_outputs(project.id)) == 1
    assert restarted.store.get_output(project.id, replayed.output_id).sha256 == replayed.sha256
