from __future__ import annotations

from pathlib import Path

import pytest

from handoff_forge.application import HandoffApplication
from handoff_forge.config import HandoffSettings
from handoff_forge.errors import CapabilityError, HandoffValidationError
from handoff_forge.models import (
    BlockKind,
    ContentBlock,
    HandoffMode,
    ModelRoute,
    ParsedDocument,
    TemplateProfile,
)
from handoff_forge.retrieval.index import RetrievalHit

ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - dry-run argv validation only.


class MemoryRetrieval:
    def __init__(self) -> None:
        self.documents: dict[str, list[object]] = {}

    def index_document(self, document: object) -> int:
        artifact = document.artifact  # type: ignore[attr-defined]
        blocks = list(document.blocks)  # type: ignore[attr-defined]
        self.documents.setdefault(artifact.project_id, []).extend(blocks)
        return len(blocks)

    def search(self, project_id: str, query: str, *, limit: int = 5) -> list[RetrievalHit]:
        query_terms = set(query.casefold().split())
        hits: list[RetrievalHit] = []
        for block in self.documents.get(project_id, []):
            text = block.text  # type: ignore[attr-defined]
            score = len(query_terms.intersection(text.casefold().split())) / max(
                1, len(query_terms)
            )
            hits.append(
                RetrievalHit(
                    node_id=block.id,  # type: ignore[attr-defined]
                    project_id=project_id,
                    text=text,
                    score=score,
                    metadata={
                        "block_id": block.id,  # type: ignore[attr-defined]
                        "artifact_id": block.artifact_id,  # type: ignore[attr-defined]
                    },
                )
            )
        return sorted(hits, key=lambda item: (-item.score, item.node_id))[:limit]

    def rebuild(self, project_id: str, documents: object = None) -> int:
        self.documents[project_id] = [
            block for document in list(documents or []) for block in document.blocks
        ]
        return len(self.documents[project_id])

    def delete_project(
        self,
        project_id: str,
        *,
        include_canonical_sources: bool = False,
    ) -> None:
        self.documents.pop(project_id, None)

    def count(self, project_id: str) -> int:
        return len(self.documents.get(project_id, []))


def _application(tmp_path: Path) -> HandoffApplication:
    settings = HandoffSettings(
        data_root=tmp_path / "data",
        offline=True,
        allow_network=False,
    )
    return HandoffApplication(settings=settings, retrieval=MemoryRetrieval())


def test_application_crud_ingest_inspect_and_rebuild(tmp_path: Path) -> None:
    application = _application(tmp_path)
    project = application.create_project("Release Handoff", "Offline release proof")

    ingested = application.ingest_bytes(
        project.id,
        "context.MDC",
        b"# Current state\n\nValidation passed. Next task: package the release.\n",
    )
    inspection = application.inspect_project(project.id)

    assert ingested.artifact.project_id == project.id
    assert ingested.block_count >= 2
    assert inspection.project.name == "Release Handoff"
    assert inspection.artifact_count == 1
    assert inspection.block_count == ingested.block_count
    assert application.rebuild_index(project.id) == ingested.block_count
    assert application.resolve_project("release-handoff").id == project.id


def test_visual_blocks_remain_text_evidence_without_image_attestation(tmp_path: Path) -> None:
    application = _application(tmp_path)
    project = application.create_project("Visual text selection")
    artifact = application.store.put_upload(
        "visual.md",
        b"# Visual context\n",
        project_id=project.id,
    )
    visual = ContentBlock(
        id="visual-context-block",
        project_id=project.id,
        artifact_id=artifact.id,
        artifact_sha256=artifact.sha256,
        kind=BlockKind.PAGE_RENDER,
        text="Same-page context: deployment approval is blocked.",
        order=0,
        artifact_path=application.store.project_dir(project.id) / "derived" / "missing.png",
        extraction_method="fixture",
    )
    document = ParsedDocument(
        artifact=artifact,
        blocks=[visual],
        parser_profile="fixture-v1",
    )
    retrieval = application.retrieval
    assert isinstance(retrieval, MemoryRetrieval)
    retrieval.documents[project.id] = [visual]
    routes = {
        section_id: ModelRoute(provider="offline", model="extractive-v1")
        for section_id in range(1, 13)
    }

    evidence = application._evidence_by_section(project.id, [document], routes)

    assert set(evidence) == set(range(1, 13))
    assert all(blocks == [visual] for blocks in evidence.values())


def test_offline_generation_is_saved_validated_and_previewable(tmp_path: Path) -> None:
    application = _application(tmp_path)
    project = application.create_project("Generation Demo")
    application.ingest_bytes(
        project.id,
        "evidence.md",
        b"# Goal\n\nShip a local-first handoff. Tests pass. Preserve security boundaries.\n",
    )

    outcome = application.generate_handoff(project.id, mode=HandoffMode.PRE_COMPACT)

    assert outcome.job.status.value == "complete"
    assert outcome.output is not None
    assert outcome.validation is not None and outcome.validation.valid
    assert outcome.package is not None
    assert outcome.package.profile is TemplateProfile.CODEX_PRECOMPACT_V1
    assert outcome.output.stored_path.name.endswith(".mdc")
    assert application.list_outputs(project.id)[0].id == outcome.output.id

    report = application.validate_output(
        project.id,
        outcome.output.id,
        TemplateProfile.CODEX_PRECOMPACT_V1,
    )
    preview = application.copy_output(project.id, outcome.output.id, execute=False)
    assert report.valid
    assert preview.executed is False
    assert str(outcome.output.stored_path) in preview.message


def test_launch_revalidates_managed_output_before_preparing_a_session(tmp_path: Path) -> None:
    application = _application(tmp_path)
    project = application.create_project("Launch safety")
    destination = application.store.put_output(
        project.id,
        "invalid.handoff.mdc",
        "# This is not a twelve-section handoff.\n",
        metadata={"profile": TemplateProfile.CODEX_PRECOMPACT_V1.value},
    )
    output = application._output_for_path(project.id, destination)

    with pytest.raises(HandoffValidationError, match="missing section"):
        application.launch_output(project.id, output.id, harness="codex")


def test_launch_rejects_output_without_an_authoritative_profile(tmp_path: Path) -> None:
    application = _application(tmp_path)
    project = application.create_project("Legacy launch safety")
    destination = application.store.put_output(
        project.id,
        "legacy.handoff.mdc",
        "# Legacy output\n",
    )
    output = application._output_for_path(project.id, destination)

    with pytest.raises(CapabilityError, match="no recognized handoff profile"):
        application.launch_output(project.id, output.id, harness="codex")


def test_restart_readback_and_delete_remove_canonical_and_index_state(tmp_path: Path) -> None:
    settings = HandoffSettings(data_root=tmp_path / "data", offline=True, allow_network=False)
    first = HandoffApplication(settings=settings, retrieval=MemoryRetrieval())
    project = first.create_project("Restart Proof")
    first.ingest_bytes(project.id, "proof.md", b"# Proof\n\nRestart readback works.\n")

    restarted = HandoffApplication(settings=settings, retrieval=MemoryRetrieval())
    assert restarted.resolve_project(project.id).name == "Restart Proof"
    assert restarted.rebuild_index(project.id) > 0

    restarted.delete_project(project.id)

    assert restarted.list_projects() == []


def test_demo_includes_multimodal_pdf_and_ui_preview_has_validated_host(tmp_path: Path) -> None:
    application = _application(tmp_path)

    demo = application.materialize_demo()
    artifacts = application.list_artifacts(demo.project.id)
    pdf = next(artifact for artifact in artifacts if artifact.display_name.endswith(".pdf"))
    parsed = application.inspect_artifact(demo.project.id, pdf.id)
    preview = application.launch_ui(host=ALL_INTERFACES, port=8765)

    assert len(demo.ingested) == 3
    assert parsed.blocks
    assert "--server.address" in preview.argv
    assert preview.argv[preview.argv.index("--server.address") + 1] == ALL_INTERFACES
    assert preview.argv[preview.argv.index("--server.port") + 1] == "8765"
    expected_streamlit_settings = {
        "--server.maxUploadSize": "50",
        "--browser.gatherUsageStats": "false",
        "--client.toolbarMode": "minimal",
        "--theme.base": "light",
        "--theme.primaryColor": "#1D4ED8",
        "--theme.backgroundColor": "#F5F6F8",
        "--theme.secondaryBackgroundColor": "#F0F2F5",
        "--theme.textColor": "#1F2937",
        "--theme.font": "sans serif",
    }
    for option, expected in expected_streamlit_settings.items():
        assert preview.argv[preview.argv.index(option) + 1] == expected
    assert preview.executed is False

    with pytest.raises(ValueError, match="host"):
        application.launch_ui(host="localhost;touch", port=8765)


def test_ui_launch_reports_an_immediate_server_failure(tmp_path: Path) -> None:
    settings = HandoffSettings(data_root=tmp_path / "data", offline=True, allow_network=False)
    application = HandoffApplication(
        settings=settings,
        retrieval=MemoryRetrieval(),
        ui_executor=lambda *_args, **_kwargs: type(
            "FailedProcess",
            (),
            {"pid": None, "returncode": 4},
        )(),
    )

    with pytest.raises(CapabilityError, match="Streamlit exited with status 4"):
        application.launch_ui(port=8765, execute=True)


def test_ui_launch_propagates_explicit_runtime_settings(tmp_path: Path) -> None:
    settings = HandoffSettings(
        data_root=tmp_path / "isolated-ui-data",
        offline=False,
        allow_network=True,
    )
    invocation: dict[str, object] = {}

    def capture_executor(argv: list[str], **kwargs: object) -> object:
        invocation.update({"argv": argv, **kwargs})
        return type("StoppedProcess", (), {"pid": 4321, "returncode": 0})()

    application = HandoffApplication(
        settings=settings,
        retrieval=MemoryRetrieval(),
        ui_executor=capture_executor,
    )

    result = application.launch_ui(port=8765, execute=True)

    environment = invocation["env"]
    assert isinstance(environment, dict)
    assert environment["HANDOFF_FORGE_DATA_ROOT"] == str(settings.data_root)
    assert environment["HANDOFF_FORGE_OFFLINE"] == "false"
    assert environment["HANDOFF_FORGE_ALLOW_NETWORK"] == "true"
    assert environment["HANDOFF_FORGE_ENABLED_EXTENSIONS"] == ""
    assert invocation["cwd"] == str(settings.data_root)
    assert invocation["shell"] is False
    assert result.executed is True
    assert result.pid == 4321
