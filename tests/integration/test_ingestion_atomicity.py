from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas

from handoff_forge.application import HandoffApplication, RetrievalProtocol
from handoff_forge.config import HandoffSettings
from handoff_forge.errors import ParseError, UnsafeUploadError
from handoff_forge.models import BlockKind, HandoffMode, ParsedDocument
from handoff_forge.retrieval.index import RetrievalHit
from handoff_forge.security import sha256_bytes


class FailAfterFirstIndex:
    """Delegate that simulates a provider failure after Chroma has persisted nodes."""

    def __init__(self, delegate: RetrievalProtocol) -> None:
        self.delegate = delegate
        self.fail_next = True

    def index_document(self, document: ParsedDocument) -> int:
        indexed = self.delegate.index_document(document)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated post-upsert failure")
        return indexed

    def search(self, project_id: str, query: str, *, limit: int = 5) -> list[RetrievalHit]:
        return self.delegate.search(project_id, query, limit=limit)

    def rebuild(
        self,
        project_id: str,
        documents: Sequence[ParsedDocument] | None = None,
    ) -> int:
        return self.delegate.rebuild(project_id, documents)

    def delete_project(
        self,
        project_id: str,
        *,
        include_canonical_sources: bool = True,
    ) -> None:
        self.delegate.delete_project(
            project_id,
            include_canonical_sources=include_canonical_sources,
        )

    def delete_artifact(self, project_id: str, artifact_id: str) -> None:
        self.delegate.delete_artifact(project_id, artifact_id)


def _settings(root: Path, **updates: object) -> HandoffSettings:
    return HandoffSettings(
        data_root=root,
        offline=True,
        allow_network=False,
        embedding_dimensions=64,
        **updates,
    )


def _encrypted_pdf(path: Path) -> bytes:
    encryption = StandardEncryption(
        "private-reader-password",
        ownerPassword="private-owner-password",
        canPrint=0,
        canModify=0,
        canCopy=0,
        canAnnotate=0,
    )
    document = canvas.Canvas(str(path), encrypt=encryption)
    document.drawString(72, 720, "Encrypted continuation evidence.")
    document.save()
    return path.read_bytes()


def _assert_no_ingestion_residue(application: HandoffApplication, project_id: str) -> None:
    project = application.store.load_project(project_id)
    project_directory = application.store.project_dir(project_id)

    assert project.artifact_ids == []
    assert application.list_artifacts(project_id) == []
    assert list((project_directory / "originals").iterdir()) == []
    assert list((project_directory / "derived").iterdir()) == []
    assert list((project_directory / "parsed").iterdir()) == []
    assert list((project_directory / "manifests" / "artifacts").iterdir()) == []
    assert not list(project_directory.glob(".artifact-delete-*"))
    assert application.search(project_id, "failed evidence") == []
    assert application.rebuild_index(project_id) == 0


def _serialized_hits(hits: list[RetrievalHit]) -> str:
    return json.dumps(
        [{"text": hit.text, "metadata": hit.metadata} for hit in hits],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _assert_canary_absent_from_derived(
    application: HandoffApplication,
    project_id: str,
    canary: str,
) -> None:
    project_directory = application.store.project_dir(project_id)
    for root in (
        project_directory / "derived",
        project_directory / "parsed",
        project_directory / "outputs",
        project_directory / "jobs",
    ):
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if candidate.is_file() and not candidate.is_symlink():
                assert canary.encode() not in candidate.read_bytes()
    document_store = application.settings.data_root / "indexes" / "chroma" / "chroma.sqlite3"
    if document_store.exists():
        assert canary.encode() not in document_store.read_bytes()


def test_parsed_content_is_sanitized_before_save_index_restart_and_rebuild(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "sanitized-data")
    application = HandoffApplication(settings=settings)
    project = application.create_project("Sanitized ingestion")
    canary = "quoted-json-" + "credential-canary-123456"
    quoted_credential = json.dumps(
        {"api_" + "key": canary},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    content = (
        "---\n"
        f"token: {canary}\n"
        "---\n"
        "# Credential handling\n\n"
        "The following quoted JSON credential must be redacted from every derived store.\n\n"
        "```json\n"
        f"{quoted_credential}\n"
        "```\n"
    ).encode()

    result = application.ingest_bytes(project.id, "credential-context.md", content)
    original = application.store.read_artifact(project.id, result.artifact.id)
    parsed = application.inspect_artifact(project.id, result.artifact.id)
    parsed_json = json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, default=str)
    hits = application.search(project.id, "credential handling", limit=10)

    assert canary.encode() in original
    assert canary not in result.parsed_path.read_text(encoding="utf-8")
    assert canary not in parsed_json
    assert parsed.frontmatter["token"] == "[REDACTED]"
    assert hits
    assert canary not in _serialized_hits(hits)
    assert "[REDACTED]" in _serialized_hits(hits)
    _assert_canary_absent_from_derived(application, project.id, canary)

    restarted = HandoffApplication(settings=settings)
    restarted_hits = restarted.search(project.id, "credential handling", limit=10)
    assert restarted_hits
    assert canary not in _serialized_hits(restarted_hits)
    assert "[REDACTED]" in _serialized_hits(restarted_hits)
    _assert_canary_absent_from_derived(restarted, project.id, canary)

    assert restarted.rebuild_index(project.id) > 0
    rebuilt_hits = restarted.search(project.id, "credential handling", limit=10)
    assert rebuilt_hits
    assert canary not in _serialized_hits(rebuilt_hits)
    assert "[REDACTED]" in _serialized_hits(rebuilt_hits)
    _assert_canary_absent_from_derived(restarted, project.id, canary)

    generation = restarted.generate_handoff(project.id, mode=HandoffMode.PRE_COMPACT)
    assert generation.output is not None
    assert canary not in restarted.store.read_output(project.id, generation.output.id).decode()
    checkpoint = restarted.store.write_job_checkpoint(
        project.id,
        "job_quoted_credential",
        {
            "detail": quoted_credential,
            "api_" + "key": canary,
        },
    )
    checkpoint_text = checkpoint.read_text(encoding="utf-8")
    assert canary not in checkpoint_text
    assert "[REDACTED]" in checkpoint_text
    _assert_canary_absent_from_derived(restarted, project.id, canary)


def test_parse_and_size_failures_leave_no_residue_and_do_not_poison_recovery(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "atomic-data")
    application = HandoffApplication(settings=settings)
    project = application.create_project("Atomic ingestion")
    invalid_inputs = (
        ("malformed.pdf", b"%PDF-1.7\nthis is not a valid PDF body"),
        ("encrypted.pdf", _encrypted_pdf(tmp_path / "encrypted.pdf")),
    )

    for filename, payload in invalid_inputs:
        with pytest.raises(ParseError):
            application.ingest_bytes(project.id, filename, payload)
        _assert_no_ingestion_residue(application, project.id)

    limited_settings = _settings(tmp_path / "limited-data", max_upload_bytes=32)
    limited = HandoffApplication(settings=limited_settings)
    limited_project = limited.create_project("Bounded ingestion")
    with pytest.raises(UnsafeUploadError, match="byte limit"):
        limited.ingest_bytes(
            limited_project.id,
            "oversized.md",
            b"# Oversized\n" + (b"x" * 64),
        )
    _assert_no_ingestion_residue(limited, limited_project.id)

    ingested = application.ingest_bytes(
        project.id,
        "recovered.md",
        b"# Recovery\n\nValid ingestion, rebuild, and generation remain available.\n",
    )
    assert ingested.indexed_nodes > 0
    assert application.rebuild_index(project.id) == ingested.indexed_nodes
    generation = application.generate_handoff(project.id, mode=HandoffMode.PRE_COMPACT)
    assert generation.output is not None
    assert generation.validation is not None and generation.validation.valid

    restarted = HandoffApplication(settings=settings)
    assert [artifact.id for artifact in restarted.list_artifacts(project.id)] == [
        ingested.artifact.id
    ]
    assert restarted.search(project.id, "recovery generation")


def test_post_upsert_failure_rolls_back_canonical_and_index_state(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "index-rollback-data")
    application = HandoffApplication(settings=settings)
    project = application.create_project("Index rollback")
    application.retrieval = FailAfterFirstIndex(application.retrieval)

    with pytest.raises(RuntimeError, match="post-upsert failure"):
        application.ingest_bytes(
            project.id,
            "failed-index.md",
            b"# Failed evidence\n\nThis node is written before the simulated failure.\n",
        )

    _assert_no_ingestion_residue(application, project.id)
    recovered = application.ingest_bytes(
        project.id,
        "working-index.md",
        b"# Working evidence\n\nAtomic recovery succeeds after index rollback.\n",
    )
    assert recovered.indexed_nodes > 0
    assert application.search(project.id, "atomic recovery")

    restarted = HandoffApplication(settings=settings)
    assert restarted.rebuild_index(project.id) == recovered.indexed_nodes
    assert restarted.search(project.id, "atomic recovery")


def test_path_ingestion_parses_the_immutable_snapshot_after_source_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path / "path-snapshot-data")
    application = HandoffApplication(settings=settings)
    project = application.create_project("Immutable path snapshot")
    image = tmp_path / "diagram.png"
    image_bytes = b"\x89PNG\r\n\x1a\n" + (b"snapshot-image" * 4)
    image.write_bytes(image_bytes)
    source = tmp_path / "continuation.md"
    original = (
        b"# Immutable source\n\n"
        b"The canonical stable marker is snapshot-original-evidence.\n\n"
        b"![Diagram](diagram.png)\n"
    )
    mutated = b"# Mutated source\n\nThis must never become canonical: mutation-race-evidence.\n"
    source.write_bytes(original)
    real_put_upload = application.store.put_upload

    def put_then_mutate(
        filename: str,
        content: bytes,
        *,
        project_id: str | None = None,
    ):
        artifact = real_put_upload(filename, content, project_id=project_id)
        source.write_bytes(mutated)
        return artifact

    monkeypatch.setattr(application.store, "put_upload", put_then_mutate)

    result = application.ingest_paths(project.id, [source])[0]
    parsed = application.inspect_artifact(project.id, result.artifact.id)
    serialized = json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, default=str)
    hits = application.search(project.id, "stable marker", limit=10)

    assert source.read_bytes() == mutated
    assert application.store.read_artifact(project.id, result.artifact.id) == original
    assert result.artifact.sha256 == sha256_bytes(original)
    assert parsed.artifact.sha256 == result.artifact.sha256
    assert {block.artifact_sha256 for block in parsed.blocks} == {result.artifact.sha256}
    assert "snapshot-original-evidence" in serialized
    assert "mutation-race-evidence" not in serialized
    assert "mutation-race-evidence" not in _serialized_hits(hits)
    visual = next(block for block in parsed.blocks if block.kind is BlockKind.IMAGE)
    assert visual.artifact_path is not None
    assert visual.artifact_path.is_relative_to(application.store.project_dir(project.id))
    assert visual.artifact_path.read_bytes() == image_bytes


def test_path_ingestion_mutation_does_not_bypass_failure_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path / "path-rollback-data")
    application = HandoffApplication(settings=settings)
    project = application.create_project("Immutable path rollback")
    source = tmp_path / "invalid.mdc"
    invalid_snapshot = b"---\nitems: [unterminated\n---\n# Invalid snapshot\n"
    valid_mutation = b"# Valid only after the snapshot\n\nmutation-must-not-bypass-parse-failure\n"
    source.write_bytes(invalid_snapshot)
    real_put_upload = application.store.put_upload

    def put_then_mutate(
        filename: str,
        content: bytes,
        *,
        project_id: str | None = None,
    ):
        artifact = real_put_upload(filename, content, project_id=project_id)
        source.write_bytes(valid_mutation)
        return artifact

    monkeypatch.setattr(application.store, "put_upload", put_then_mutate)

    with pytest.raises(ParseError, match="frontmatter"):
        application.ingest_paths(project.id, [source])

    assert source.read_bytes() == valid_mutation
    _assert_no_ingestion_residue(application, project.id)
