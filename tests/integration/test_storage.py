from __future__ import annotations

import stat

import pytest

from handoff_forge.errors import StorageError, UnsafeUploadError
from handoff_forge.models import BlockKind, ContentBlock, ParsedDocument
from handoff_forge.storage import ContentAddressedStore


def test_content_addressed_storage_restart_output_readback_and_deletion(settings) -> None:
    store = ContentAddressedStore(settings)
    project = store.create_project("Parser migration")
    artifact = store.put_upload(
        "../résumé #1.MDC",
        b"---\nalwaysApply: false\n---\n# Goal\nPreserve evidence.\n",
        project_id=project.id,
    )

    assert artifact.sha256 in artifact.stored_path.name
    assert artifact.stored_path.resolve().is_relative_to(store.root)
    assert artifact.file_uri.startswith("file://")
    assert artifact.display_name == "résumé #1.MDC"
    assert stat.S_IMODE(artifact.stored_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.project_dir(project.id).stat().st_mode) == 0o700

    duplicate = store.put_upload(
        "copy.mdc",
        artifact.stored_path.read_bytes(),
        project_id=project.id,
    )
    assert duplicate.id == artifact.id

    block = ContentBlock(
        id="blk_test",
        project_id=project.id,
        artifact_id=artifact.id,
        artifact_sha256=artifact.sha256,
        kind=BlockKind.TEXT,
        text="Preserve evidence.",
        order=0,
        line_start=5,
        line_end=5,
        extraction_method="test",
    )
    parsed = ParsedDocument(
        artifact=artifact,
        blocks=[block],
        parser_profile="test-v1",
    )
    store.save_parsed_document(parsed)
    output_path = store.put_output(
        project.id,
        "continuation.mdc",
        "api_key=" + "sk-" + "example123456789\n# Continue",
    )
    assert "example123" not in output_path.read_text(encoding="utf-8")

    restarted = ContentAddressedStore(settings)
    assert restarted.list_artifacts(project.id) == [artifact]
    assert restarted.load_parsed_document(project.id, artifact.id).blocks == [block]
    outputs = restarted.list_outputs(project.id)
    assert len(outputs) == 1
    assert restarted.get_output(project.id, outputs[0].id) == outputs[0]
    assert restarted.read_output(project.id, outputs[0].id).endswith(b"# Continue")

    byte_output = restarted.put_output(
        project.id,
        "byte-continuation.mdc",
        ("token=" + "abcdefghijklmnop\n# Continue safely").encode(),
    )
    assert b"abcdefghijklmnop" not in byte_output.read_bytes()
    assert b"[REDACTED]" in byte_output.read_bytes()

    restarted.delete_project(project.id)
    assert not restarted.project_dir(project.id).exists()
    assert restarted.list_projects() == []


def test_store_rejects_spoofed_pdf(settings) -> None:
    store = ContentAddressedStore(settings)
    project = store.create_project("Unsafe upload")

    with pytest.raises(UnsafeUploadError, match="PDF signature"):
        store.put_upload("project.pdf", b"not a pdf", project_id=project.id)


@pytest.mark.parametrize("failure_point", ["payload", "manifest", "project-reference"])
def test_output_idempotency_recovers_each_commit_boundary_without_duplicates(
    settings,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    store = ContentAddressedStore(settings)
    project = store.create_project("Output crash recovery")
    project_directory = store.project_dir(project.id)
    real_write_bytes = store._atomic_write_bytes
    real_write_json = store._atomic_write_json
    real_write_model = store._write_model
    interrupted = False

    def interrupt_payload_write(path, content):
        nonlocal interrupted
        result = real_write_bytes(path, content)
        if failure_point == "payload" and path.parent.name == "outputs" and not interrupted:
            interrupted = True
            raise OSError("simulated payload interruption")
        return result

    def interrupt_manifest_write(path, data):
        nonlocal interrupted
        result = real_write_json(path, data)
        if (
            failure_point == "manifest"
            and path.parent.name == "outputs"
            and path.parent.parent.name == "manifests"
            and not interrupted
        ):
            interrupted = True
            raise OSError("simulated manifest interruption")
        return result

    def interrupt_project_update(path, model):
        nonlocal interrupted
        result = real_write_model(path, model)
        if (
            failure_point == "project-reference"
            and path.name == "project.json"
            and model.output_ids
            and not interrupted
        ):
            interrupted = True
            raise OSError("simulated project-reference interruption")
        return result

    monkeypatch.setattr(store, "_atomic_write_bytes", interrupt_payload_write)
    monkeypatch.setattr(store, "_atomic_write_json", interrupt_manifest_write)
    monkeypatch.setattr(store, "_write_model", interrupt_project_update)
    with pytest.raises(OSError, match=r"simulated .* interruption"):
        store.put_output(
            project.id,
            "lifecycle.mdc",
            "# First committed output\n",
            idempotency_key="generation-job:job-safe-fixture",
        )

    assert interrupted is True

    restarted = ContentAddressedStore(settings)
    recovered = restarted.put_output(
        project.id,
        "lifecycle.mdc",
        "# A retry must not create this second output\n",
        idempotency_key="generation-job:job-safe-fixture",
    )

    repaired = restarted.load_project(project.id)
    assert recovered.read_text(encoding="utf-8") == "# First committed output\n"
    assert len(repaired.output_ids) == 1
    assert len(restarted.list_outputs(project.id)) == 1
    assert restarted.list_outputs(project.id)[0].stored_path == recovered
    assert len(list((project_directory / "outputs").iterdir())) == 1
    assert len(list((project_directory / "manifests" / "outputs").glob("*.json"))) == 1


def test_committed_artifact_deletion_keeps_retryable_tombstone_on_partial_cleanup(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ContentAddressedStore(settings)
    project = store.create_project("Deletion recovery")
    artifact = store.put_upload(
        "private-evidence.md",
        b"# Private evidence\n\nDeletion must remain recoverable.\n",
        project_id=project.id,
    )
    block = ContentBlock(
        id="blk_delete_recovery",
        project_id=project.id,
        artifact_id=artifact.id,
        artifact_sha256=artifact.sha256,
        kind=BlockKind.TEXT,
        text="Deletion must remain recoverable.",
        order=0,
        extraction_method="test",
    )
    store.save_parsed_document(
        ParsedDocument(artifact=artifact, blocks=[block], parser_profile="test-v1")
    )
    derived = store.project_dir(project.id) / "derived" / artifact.id
    derived.mkdir()
    (derived / "private-render.png").write_bytes(b"private-render-canary")
    artifact_manifest = (
        store.project_dir(project.id) / "manifests" / "artifacts" / f"{artifact.id}.json"
    )
    parsed_path = store.project_dir(project.id) / "parsed" / f"{artifact.id}.json"
    real_remove = store._remove_tree_without_following_links
    fault_injected = False

    def fail_after_first_staged_child(path):
        nonlocal fault_injected
        if (
            not fault_injected
            and path.parent.name.startswith(".artifact-delete-")
            and path.name != "transaction.json"
        ):
            fault_injected = True
            real_remove(path)
            raise OSError("simulated cleanup interruption")
        real_remove(path)

    monkeypatch.setattr(
        store, "_remove_tree_without_following_links", fail_after_first_staged_child
    )

    with pytest.raises(StorageError, match="committed but private cleanup is pending"):
        store.delete_artifact(project.id, artifact.id)

    project_directory = store.project_dir(project.id)
    tombstones = list(project_directory.glob(".artifact-delete-*"))
    assert fault_injected is True
    assert store.load_project(project.id).artifact_ids == []
    assert store.list_artifacts(project.id) == []
    assert not artifact.stored_path.exists()
    assert not artifact_manifest.exists()
    assert not parsed_path.exists()
    assert not derived.exists()
    assert len(tombstones) == 1
    assert (tombstones[0] / "transaction.json").is_file()

    restarted = ContentAddressedStore(settings)

    assert restarted.load_project(project.id).artifact_ids == []
    assert restarted.list_artifacts(project.id) == []
    assert not list(project_directory.glob(".artifact-delete-*"))
    assert not artifact.stored_path.exists()
    assert not artifact_manifest.exists()
    assert not parsed_path.exists()
    assert not derived.exists()
