"""Private, content-addressed persistence for Handoff Forge.

JSON manifests are authoritative. Chroma and framework objects are deliberately
kept outside this layer because they are rebuildable derived state.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, Field

from handoff_forge.config import HandoffSettings
from handoff_forge.errors import StorageError, UnsafeUploadError
from handoff_forge.models import ParsedDocument, ProjectRecord, SourceArtifact, utc_now
from handoff_forge.security import (
    FILE_MODE,
    classify_upload,
    confined_path,
    enforce_private_file,
    ensure_directory,
    normalize_display_name,
    redact_secrets,
    safe_file_uri,
    sanitize_json_value,
    sha256_bytes,
    sha256_file,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_ARTIFACT_DELETE_PREFIX = ".artifact-delete-"
_ARTIFACT_DELETE_TRANSACTION = "transaction.json"
_ARTIFACT_DELETE_TARGETS = {
    "artifact-manifest.json",
    "parsed-document.json",
    "derived",
    "original",
}


def _redact_json(value: Any) -> Any:
    return sanitize_json_value(value)


class StoredOutput(BaseModel):
    """Readback model for a generated local output."""

    id: str
    project_id: str
    display_name: str
    stored_path: Path
    file_uri: str
    sha256: str
    size_bytes: int
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContentAddressedStore:
    """Store immutable source bytes and atomic JSON manifests below one root."""

    schema_version = "handoff-forge-storage-v1"

    def __init__(self, root: Path | HandoffSettings) -> None:
        if isinstance(root, HandoffSettings):
            self.settings = root
            data_root = root.data_root
        else:
            data_root = Path(root)
            self.settings = HandoffSettings(data_root=data_root)
        self.root = data_root.expanduser().resolve()
        ensure_directory(self.root)
        self.projects_root = ensure_directory(self.root / "projects")
        self._lock_path = self.root / ".store.lock"
        self._lock = FileLock(str(self._lock_path), mode=FILE_MODE)
        with self._lock:
            self._retry_all_pending_artifact_deletions(strict=False)

    def create_project(self, name: str, description: str = "") -> ProjectRecord:
        """Create and persist a project with an opaque generated identifier."""

        safe_name = redact_secrets(name.strip()) or "Untitled project"
        project = ProjectRecord(
            id=f"prj_{uuid4().hex}",
            name=safe_name,
            description=redact_secrets(description),
        )
        with self._lock:
            directory = self.project_dir(project.id)
            if directory.exists():  # pragma: no cover - UUID collision defense
                raise StorageError("generated project identifier collided")
            self._initialize_project_directories(directory)
            self._write_model(directory / "project.json", project)
        return project

    def list_projects(self) -> list[ProjectRecord]:
        """Read all intact project manifests, sorted by creation time."""

        projects: list[ProjectRecord] = []
        for candidate in sorted(self.projects_root.iterdir()):
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            manifest = candidate / "project.json"
            if manifest.is_file() and not manifest.is_symlink():
                projects.append(ProjectRecord.model_validate(self._read_json(manifest)))
        return sorted(projects, key=lambda item: (item.created_at, item.id))

    def project_dir(self, project_id: str) -> Path:
        """Return a confined project path without requiring it to exist."""

        self._validate_id(project_id, label="project")
        return confined_path(self.projects_root, self.projects_root / project_id)

    def load_project(self, project_id: str) -> ProjectRecord:
        manifest = self.project_dir(project_id) / "project.json"
        return ProjectRecord.model_validate(self._read_json(manifest))

    def put_upload(
        self,
        filename: str,
        content: bytes | bytearray | BinaryIO,
        *,
        project_id: str | None = None,
    ) -> SourceArtifact:
        """Validate and preserve an upload by hash.

        When no project is supplied, a sole existing project is used; if the
        store is empty, a project is created from the display filename. This
        keeps the two-argument API convenient while multi-project callers can
        always scope explicitly.
        """

        payload = self._read_bounded_content(content)
        media_type, kind, display_name = classify_upload(filename, payload)
        project_id = project_id or self._default_project(display_name)
        digest = sha256_bytes(payload)
        suffix = Path(display_name).suffix.casefold()

        with self._lock:
            project_directory = self.project_dir(project_id)
            self._retry_pending_artifact_deletions(project_directory, strict=True)
            project = self.load_project(project_id)
            for artifact_id in project.artifact_ids:
                existing = self.get_artifact(project_id, artifact_id)
                if existing.sha256 == digest:
                    return existing

            artifact_id = f"art_{uuid4().hex}"
            originals = ensure_directory(project_directory / "originals")
            stored_path = confined_path(project_directory, originals / f"{digest}{suffix}")
            if stored_path.exists():
                if sha256_file(stored_path) != digest:
                    raise StorageError("content-addressed path contains unexpected bytes")
                enforce_private_file(stored_path)
            else:
                self._atomic_write_bytes(stored_path, payload)

            artifact = SourceArtifact(
                id=artifact_id,
                project_id=project_id,
                display_name=display_name,
                sha256=digest,
                media_type=media_type,
                size_bytes=len(payload),
                kind=kind,
                stored_path=stored_path,
                file_uri=safe_file_uri(self.root, stored_path),
                metadata={
                    "storage_schema": self.schema_version,
                    "untrusted_evidence": True,
                    "instruction_boundary": "content_only_never_control_text",
                },
            )
            artifact_manifest = ensure_directory(project_directory / "manifests" / "artifacts")
            self._write_model(artifact_manifest / f"{artifact.id}.json", artifact)
            updated = project.model_copy(
                update={
                    "artifact_ids": [*project.artifact_ids, artifact.id],
                    "updated_at": utc_now(),
                }
            )
            self._write_model(project_directory / "project.json", updated)
        return artifact

    def get_artifact(self, project_id: str, artifact_id: str) -> SourceArtifact:
        self._validate_id(artifact_id, label="artifact")
        project_directory = self.project_dir(project_id)
        manifest = project_directory / "manifests" / "artifacts" / f"{artifact_id}.json"
        artifact = SourceArtifact.model_validate(self._read_json(manifest))
        if artifact.project_id != project_id:
            raise StorageError("artifact manifest belongs to a different project")
        stored_path = confined_path(project_directory, artifact.stored_path, must_exist=True)
        if sha256_file(stored_path) != artifact.sha256:
            raise StorageError(f"artifact integrity check failed: {artifact.id}")
        return artifact.model_copy(
            update={"stored_path": stored_path, "file_uri": safe_file_uri(self.root, stored_path)}
        )

    def list_artifacts(self, project_id: str) -> list[SourceArtifact]:
        """Return verified artifact manifests in project order."""

        project = self.load_project(project_id)
        return [self.get_artifact(project_id, artifact_id) for artifact_id in project.artifact_ids]

    def read_artifact(self, project_id: str, artifact_id: str) -> bytes:
        artifact = self.get_artifact(project_id, artifact_id)
        if artifact.size_bytes > self.settings.max_upload_bytes:
            raise StorageError("artifact exceeds the configured read limit")
        return artifact.stored_path.read_bytes()

    def delete_artifact(self, project_id: str, artifact_id: str) -> None:
        """Atomically detach one artifact, remove all of its state, and prove readback."""

        self._validate_id(artifact_id, label="artifact")
        project_directory = self.project_dir(project_id)
        with self._lock:
            self._retry_pending_artifact_deletions(project_directory, strict=True)
            project = self.load_project(project_id)
            if artifact_id not in project.artifact_ids:
                return
            artifact = self.get_artifact(project_id, artifact_id)
            targets = {
                "artifact-manifest.json": (
                    project_directory / "manifests" / "artifacts" / f"{artifact_id}.json"
                ),
                "parsed-document.json": project_directory / "parsed" / f"{artifact_id}.json",
                "derived": project_directory / "derived" / artifact_id,
                "original": artifact.stored_path,
            }
            staging = ensure_directory(
                project_directory / f"{_ARTIFACT_DELETE_PREFIX}{artifact_id}-{uuid4().hex}"
            )
            transaction = {
                "schema_version": "handoff-forge-artifact-delete-v1",
                "project_id": project_id,
                "artifact_id": artifact_id,
                "phase": "staging",
                "targets": {
                    staged_name: str(
                        confined_path(project_directory, target).relative_to(project_directory)
                    )
                    for staged_name, target in targets.items()
                    if target.exists() or target.is_symlink()
                },
            }
            transaction_path = staging / _ARTIFACT_DELETE_TRANSACTION
            transaction_written = False
            try:
                self._atomic_write_json(transaction_path, transaction)
                transaction_written = True
                transaction_targets = transaction["targets"]
                if not isinstance(
                    transaction_targets, Mapping
                ):  # pragma: no cover - local type guard
                    raise StorageError("artifact deletion transaction targets are invalid")
                for staged_name, relative_target in transaction_targets.items():
                    confined_target = confined_path(
                        project_directory,
                        project_directory / str(relative_target),
                    )
                    staged_path = confined_path(project_directory, staging / staged_name)
                    os.replace(confined_target, staged_path)
                updated = project.model_copy(
                    update={
                        "artifact_ids": [
                            item for item in project.artifact_ids if item != artifact_id
                        ],
                        "updated_at": utc_now(),
                    }
                )
                self._write_model(project_directory / "project.json", updated)
                readback = self.load_project(project_id)
                if artifact_id in readback.artifact_ids:
                    raise StorageError(f"artifact deletion readback failed: {artifact_id}")
                residue = [
                    path.name for path in targets.values() if path.exists() or path.is_symlink()
                ]
                if residue:
                    raise StorageError(
                        f"artifact deletion left canonical residue for {artifact_id}: "
                        f"{', '.join(residue)}"
                    )
            except Exception:
                try:
                    self._write_model(project_directory / "project.json", project)
                    if transaction_written:
                        self._recover_artifact_delete_transaction(project_directory, staging)
                    elif staging.exists() or staging.is_symlink():
                        self._remove_tree_without_following_links(staging)
                except Exception as rollback_error:
                    raise StorageError(
                        f"artifact deletion rollback could not be verified: {artifact_id}"
                    ) from rollback_error
                raise

            # The new manifest has now been read back without the artifact and every canonical
            # target has been detached. From this commit point forward, never restore the old
            # manifest: cleanup failures leave a private, retryable tombstone instead.
            try:
                transaction["phase"] = "committed"
                self._atomic_write_json(transaction_path, transaction)
                self._purge_artifact_delete_tombstone(staging)
            except Exception as cleanup_error:
                raise StorageError(
                    f"artifact deletion committed but private cleanup is pending: {artifact_id}"
                ) from cleanup_error

            readback = self.load_project(project_id)
            if artifact_id in readback.artifact_ids:
                raise StorageError(f"artifact deletion readback failed: {artifact_id}")
            if staging.exists() or staging.is_symlink():
                raise StorageError(f"artifact deletion tombstone remains: {artifact_id}")

    def save_parsed_document(self, document: ParsedDocument) -> Path:
        """Persist canonical parsed blocks; framework nodes are never stored."""

        project_directory = self.project_dir(document.artifact.project_id)
        self.get_artifact(document.artifact.project_id, document.artifact.id)
        parsed_directory = ensure_directory(project_directory / "parsed")
        destination = confined_path(
            project_directory,
            parsed_directory / f"{document.artifact.id}.json",
        )
        with self._lock:
            self._write_model(destination, document)
        return destination

    def load_parsed_document(self, project_id: str, artifact_id: str) -> ParsedDocument:
        self._validate_id(artifact_id, label="artifact")
        path = self.project_dir(project_id) / "parsed" / f"{artifact_id}.json"
        parsed = ParsedDocument.model_validate(self._read_json(path))
        if parsed.artifact.project_id != project_id or parsed.artifact.id != artifact_id:
            raise StorageError("parsed document identity does not match its manifest path")
        return parsed

    def put_output(
        self,
        project_id: str,
        filename: str,
        content: str | bytes,
        *,
        metadata: Mapping[str, Any] | None = None,
        redact: bool = True,
    ) -> Path:
        """Persist a collision-resistant generated output and its manifest."""

        project = self.load_project(project_id)
        display_name = normalize_display_name(filename)
        suffix = Path(display_name).suffix.casefold()
        if suffix not in {".md", ".mdc", ".json", ".txt"}:
            raise StorageError("generated output must be Markdown, MDC, JSON, or text")
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(display_name).stem).strip("-.")
        stem = stem[:80] or "handoff"
        output_id = f"out_{uuid4().hex}"
        output_name = f"{stem}-{output_id[-12:]}{suffix}"
        if isinstance(content, str):
            rendered = redact_secrets(content) if redact else content
            payload = rendered.encode("utf-8")
        else:
            raw_payload = bytes(content)
            if redact:
                try:
                    decoded = raw_payload.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise StorageError("generated text output bytes must be valid UTF-8") from error
                payload = redact_secrets(decoded).encode("utf-8")
            else:
                payload = raw_payload
        if len(payload) > self.settings.max_markdown_characters * 4:
            raise StorageError("generated output exceeds the configured size limit")

        with self._lock:
            project_directory = self.project_dir(project_id)
            outputs = ensure_directory(project_directory / "outputs")
            destination = confined_path(project_directory, outputs / output_name)
            self._atomic_write_bytes(destination, payload)
            manifest = {
                "id": output_id,
                "project_id": project_id,
                "display_name": display_name,
                "stored_path": str(destination),
                "file_uri": safe_file_uri(self.root, destination),
                "sha256": sha256_bytes(payload),
                "size_bytes": len(payload),
                "created_at": utc_now().isoformat(),
                "metadata": dict(metadata or {}),
            }
            manifests = ensure_directory(project_directory / "manifests" / "outputs")
            self._atomic_write_json(manifests / f"{output_id}.json", manifest)
            updated = project.model_copy(
                update={"output_ids": [*project.output_ids, output_id], "updated_at": utc_now()}
            )
            self._write_model(project_directory / "project.json", updated)
        return destination

    def get_output(self, project_id: str, output_id: str) -> StoredOutput:
        """Return a sanitized output manifest after path and hash readback."""

        self._validate_id(output_id, label="output")
        project_directory = self.project_dir(project_id)
        manifest_path = project_directory / "manifests" / "outputs" / f"{output_id}.json"
        output = StoredOutput.model_validate(self._read_json(manifest_path))
        if output.project_id != project_id:
            raise StorageError("output manifest belongs to a different project")
        stored_path = confined_path(project_directory, output.stored_path, must_exist=True)
        if not stored_path.is_file() or sha256_file(stored_path) != output.sha256:
            raise StorageError(f"output integrity check failed: {output.id}")
        return output.model_copy(
            update={"stored_path": stored_path, "file_uri": safe_file_uri(self.root, stored_path)}
        )

    def list_outputs(self, project_id: str) -> list[StoredOutput]:
        """Return verified generated-output manifests in project order."""

        project = self.load_project(project_id)
        return [self.get_output(project_id, output_id) for output_id in project.output_ids]

    def read_output(self, project_id: str, output_id: str) -> bytes:
        output = self.get_output(project_id, output_id)
        return output.stored_path.read_bytes()

    def write_job_checkpoint(
        self,
        project_id: str,
        job_id: str,
        state: BaseModel | Mapping[str, Any],
    ) -> Path:
        self._validate_id(job_id, label="job")
        project_directory = self.project_dir(project_id)
        jobs = ensure_directory(project_directory / "jobs")
        destination = confined_path(project_directory, jobs / f"{job_id}.json")
        data = state.model_dump(mode="json") if isinstance(state, BaseModel) else dict(state)
        with self._lock:
            self._atomic_write_json(destination, data)
        return destination

    def read_job_checkpoint(self, project_id: str, job_id: str) -> dict[str, Any]:
        self._validate_id(job_id, label="job")
        return self._read_json(self.project_dir(project_id) / "jobs" / f"{job_id}.json")

    def delete_project(self, project_id: str) -> None:
        """Remove project state without following symlinks, then prove deletion."""

        directory = self.project_dir(project_id)
        with self._lock:
            if not directory.exists() and not directory.is_symlink():
                return
            self._remove_tree_without_following_links(directory)
            if directory.exists() or directory.is_symlink():
                raise StorageError(f"project deletion readback failed: {project_id}")

    def _default_project(self, display_name: str) -> str:
        projects = self.list_projects()
        if len(projects) == 1:
            return projects[0].id
        if len(projects) > 1:
            raise StorageError("project_id is required when the store contains multiple projects")
        return self.create_project(Path(display_name).stem or "Imported project").id

    def _initialize_project_directories(self, directory: Path) -> None:
        ensure_directory(directory)
        for relative in (
            "originals",
            "derived",
            "parsed",
            "outputs",
            "jobs",
            "manifests/artifacts",
            "manifests/outputs",
        ):
            ensure_directory(directory / relative)

    def _read_bounded_content(self, content: bytes | bytearray | BinaryIO) -> bytes:
        if isinstance(content, (bytes, bytearray)):
            payload = bytes(content)
        elif hasattr(content, "read"):
            payload = content.read(self.settings.max_upload_bytes + 1)
            if not isinstance(payload, bytes):
                raise UnsafeUploadError("upload stream must return bytes")
        else:
            raise UnsafeUploadError("upload content must be bytes or a binary stream")
        if len(payload) > self.settings.max_upload_bytes:
            raise UnsafeUploadError(
                f"upload exceeds the {self.settings.max_upload_bytes}-byte limit"
            )
        return payload

    def _write_model(self, path: Path, model: BaseModel) -> None:
        self._atomic_write_json(path, model.model_dump(mode="json"))

    def _atomic_write_json(self, path: Path, data: Mapping[str, Any]) -> None:
        payload = json.dumps(
            _redact_json(dict(data)),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        self._atomic_write_bytes(path, payload + b"\n")

    def _atomic_write_bytes(self, path: Path, content: bytes) -> None:
        parent = ensure_directory(path.parent)
        confined_path(self.root, path)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, FILE_MODE)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            enforce_private_file(path)
            self._fsync_directory(parent)
        except Exception:
            with suppress(OSError):
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise

    def _read_json(self, path: Path) -> dict[str, Any]:
        confined = confined_path(self.root, path, must_exist=True)
        if not confined.is_file():
            raise StorageError(f"manifest is not a regular file: {path}")
        try:
            data = json.loads(confined.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"could not read manifest: {path.name}") from exc
        if not isinstance(data, dict):
            raise StorageError(f"manifest root must be an object: {path.name}")
        return data

    def _retry_all_pending_artifact_deletions(self, *, strict: bool) -> None:
        for project_directory in sorted(self.projects_root.iterdir()):
            if project_directory.is_symlink() or not project_directory.is_dir():
                continue
            self._retry_pending_artifact_deletions(project_directory, strict=strict)

    def _retry_pending_artifact_deletions(
        self,
        project_directory: Path,
        *,
        strict: bool,
    ) -> None:
        for staging in sorted(project_directory.iterdir()):
            if not staging.name.startswith(_ARTIFACT_DELETE_PREFIX):
                continue
            try:
                transaction_path = staging / _ARTIFACT_DELETE_TRANSACTION
                if (
                    staging.is_dir()
                    and not staging.is_symlink()
                    and not transaction_path.exists()
                    and not any(staging.iterdir())
                ):
                    staging.rmdir()
                    continue
                self._recover_artifact_delete_transaction(project_directory, staging)
            except Exception as error:
                if strict:
                    raise StorageError(
                        "pending artifact deletion cleanup could not be recovered"
                    ) from error

    def _recover_artifact_delete_transaction(
        self,
        project_directory: Path,
        staging: Path,
    ) -> None:
        staging = confined_path(project_directory, staging, must_exist=True)
        transaction = self._read_json(staging / _ARTIFACT_DELETE_TRANSACTION)
        if transaction.get("schema_version") != "handoff-forge-artifact-delete-v1":
            raise StorageError("unknown artifact deletion transaction schema")
        project_id = str(transaction.get("project_id", ""))
        artifact_id = str(transaction.get("artifact_id", ""))
        self._validate_id(project_id, label="project")
        self._validate_id(artifact_id, label="artifact")
        if project_id != project_directory.name:
            raise StorageError("artifact deletion transaction belongs to another project")
        if transaction.get("phase") not in {"staging", "committed"}:
            raise StorageError("artifact deletion transaction phase is invalid")
        raw_targets = transaction.get("targets")
        if not isinstance(raw_targets, Mapping):
            raise StorageError("artifact deletion transaction targets are invalid")

        target_pairs: list[tuple[Path, Path]] = []
        for staged_name, relative_target in raw_targets.items():
            if staged_name not in _ARTIFACT_DELETE_TARGETS or not isinstance(relative_target, str):
                raise StorageError("artifact deletion transaction contains an invalid target")
            relative = Path(relative_target)
            if relative.is_absolute():
                raise StorageError("artifact deletion transaction target must be relative")
            staged_path = confined_path(project_directory, staging / staged_name)
            target_path = confined_path(project_directory, project_directory / relative)
            target_pairs.append((staged_path, target_path))

        project = self.load_project(project_id)
        if artifact_id in project.artifact_ids:
            for staged_path, target_path in target_pairs:
                staged_exists = staged_path.exists() or staged_path.is_symlink()
                target_exists = target_path.exists() or target_path.is_symlink()
                if staged_exists and target_exists:
                    raise StorageError("artifact deletion rollback found duplicate target state")
                if staged_exists:
                    ensure_directory(target_path.parent)
                    os.replace(staged_path, target_path)
                elif not target_exists:
                    raise StorageError("artifact deletion rollback found missing target state")
        else:
            # The manifest is authoritative after commit. Remove any transaction target that a
            # partially attempted rollback may have moved back before interruption.
            for _staged_path, target_path in target_pairs:
                if target_path.exists() or target_path.is_symlink():
                    self._remove_tree_without_following_links(target_path)
        self._purge_artifact_delete_tombstone(staging)

    def _purge_artifact_delete_tombstone(self, staging: Path) -> None:
        """Delete staged private data while retaining recovery metadata until the final step."""

        staging = confined_path(self.projects_root, staging, must_exist=True)
        transaction_path = staging / _ARTIFACT_DELETE_TRANSACTION
        for child in list(staging.iterdir()):
            if child.name == _ARTIFACT_DELETE_TRANSACTION:
                continue
            self._remove_tree_without_following_links(child)
        if transaction_path.exists() or transaction_path.is_symlink():
            self._remove_tree_without_following_links(transaction_path)
        staging.rmdir()

    @staticmethod
    def _validate_id(value: str, *, label: str) -> None:
        if not _SAFE_ID.fullmatch(value):
            raise StorageError(f"invalid {label} identifier")

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _remove_tree_without_following_links(self, path: Path) -> None:
        confined_path(self.projects_root, path)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            path.unlink()
            return
        for child in path.iterdir():
            self._remove_tree_without_following_links(child)
        path.rmdir()


# A short compatibility alias for callers that prefer the domain term.
ArtifactStore = ContentAddressedStore
