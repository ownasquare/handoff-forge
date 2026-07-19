"""Shared application service used by the CLI and Streamlit workbench."""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from importlib import resources
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from handoff_forge.config import HandoffSettings
from handoff_forge.diagnostics import DiagnosticCheck, diagnostics_ready, run_diagnostics
from handoff_forge.errors import CapabilityError, StorageError, UnsafeUploadError
from handoff_forge.extensions import (
    ExtensionEntryPoint,
    ExtensionInfo,
    ExtensionMetadata,
    describe_extensions,
    discover_extensions,
    load_enabled_extensions,
    normalize_extension_names,
)
from handoff_forge.handoffs.catalog import HANDOFF_SECTION_SPECS
from handoff_forge.handoffs.composer import OfflineHandoffComposer
from handoff_forge.handoffs.jobs import GenerationJobRunner
from handoff_forge.handoffs.profiles import handoff_filename, render_handoff
from handoff_forge.handoffs.validator import validate_handoff
from handoff_forge.harnesses.base import ActionResult, LaunchResult
from handoff_forge.harnesses.launcher import HarnessLauncher
from handoff_forge.harnesses.lifecycle import LifecycleArtifact, lifecycle_job_id
from handoff_forge.harnesses.platform import PlatformActions
from handoff_forge.harnesses.registry import build_default_harness_registry
from handoff_forge.ingestion.nodes import NodeBuilder
from handoff_forge.models import (
    ArtifactKind,
    ContentBlock,
    GenerationJob,
    HandoffMode,
    HandoffPackage,
    HandoffValidationReport,
    JobStatus,
    MergedPlan,
    ModelRoute,
    ParsedDocument,
    ParseWarning,
    ProjectRecord,
    SourceArtifact,
    TemplateProfile,
)
from handoff_forge.parsing.markdown import MarkdownParser
from handoff_forge.parsing.registry import ParserRegistry
from handoff_forge.providers.base import ProviderStatus
from handoff_forge.providers.registry import ProviderRegistry, build_default_registry
from handoff_forge.retrieval.embeddings import DeterministicHashEmbedding
from handoff_forge.retrieval.index import ChromaIndex, RetrievalHit
from handoff_forge.retrieval.service import RetrievalService
from handoff_forge.security import read_regular_file_bounded, sanitize_parsed_document
from handoff_forge.storage import ContentAddressedStore, StoredOutput


class RetrievalProtocol(Protocol):
    def index_document(self, document: ParsedDocument) -> int: ...

    def search(self, project_id: str, query: str, *, limit: int = 5) -> list[RetrievalHit]: ...

    def rebuild(
        self,
        project_id: str,
        documents: Sequence[ParsedDocument] | None = None,
    ) -> int: ...

    def delete_project(
        self,
        project_id: str,
        *,
        include_canonical_sources: bool = True,
    ) -> None: ...

    def delete_artifact(self, project_id: str, artifact_id: str) -> None: ...


class IngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: SourceArtifact
    parsed_path: Path
    block_count: int
    indexed_nodes: int
    warnings: list[ParseWarning] = Field(default_factory=list)


class ProjectInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRecord
    artifact_count: int
    output_count: int
    block_count: int
    indexed_nodes: int
    artifacts: list[SourceArtifact]
    outputs: list[StoredOutput]
    warnings: list[str] = Field(default_factory=list)


class GenerationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: GenerationJob
    package: HandoffPackage | None = None
    output: StoredOutput | None = None
    validation: HandoffValidationReport | None = None


class MergeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: MergedPlan
    output: StoredOutput


class DemoOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRecord
    ingested: list[IngestResult]
    generation: GenerationOutcome


class UIProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argv: tuple[str, ...]
    shell: Literal[False] = False
    executed: bool = False
    pid: int | None = None
    returncode: int | None = None


class _StoreCheckpointAdapter:
    """Bind the generic job checkpoint protocol to one canonical project."""

    def __init__(self, store: ContentAddressedStore, project_id: str) -> None:
        self.store = store
        self.project_id = project_id

    def save(self, job: GenerationJob) -> None:
        if job.project_id != self.project_id:
            raise StorageError("job checkpoint belongs to a different project")
        self.store.write_job_checkpoint(self.project_id, job.id, job)

    def load(self, job_id: str) -> GenerationJob:
        return GenerationJob.model_validate(self.store.read_job_checkpoint(self.project_id, job_id))


class HandoffApplication:
    """Local-first orchestration over canonical persistence and replaceable adapters."""

    def __init__(
        self,
        *,
        settings: HandoffSettings | None = None,
        store: ContentAddressedStore | None = None,
        parsers: ParserRegistry | None = None,
        retrieval: RetrievalProtocol | None = None,
        providers: ProviderRegistry | None = None,
        launcher: HarnessLauncher | None = None,
        platform_actions: PlatformActions | None = None,
        diagnostics_runner: Callable[[HandoffSettings], list[DiagnosticCheck]] = run_diagnostics,
        merge_engine: object | None = None,
        merge_renderer: Callable[[MergedPlan], str] | None = None,
        ui_executor: Callable[..., object] | None = None,
        enabled_extensions: Iterable[str] = (),
        extension_metadata: Iterable[ExtensionMetadata] | None = None,
    ) -> None:
        self.settings = settings or HandoffSettings()
        self.store = store or ContentAddressedStore(self.settings)
        self.parsers = parsers or ParserRegistry(self.settings)
        if retrieval is None:
            embedding = DeterministicHashEmbedding(self.settings.embedding_dimensions)
            index = ChromaIndex(self.settings.data_root / "indexes" / "chroma", embedding)
            retrieval = RetrievalService(
                index,
                node_builder=NodeBuilder(self.settings),
                store=self.store,
            )
        self.retrieval = retrieval
        self.providers = providers or build_default_registry(
            network_enabled=self.settings.network_enabled,
            managed_root=self.store.root,
            timeout_seconds=self.settings.provider_timeout_seconds,
            max_retries=self.settings.provider_max_retries,
        )
        self.providers.set_network_enabled(self.settings.network_enabled)
        self.launcher = launcher or HarnessLauncher(managed_root=self.store.root)
        self.platform_actions = platform_actions or PlatformActions(managed_root=self.store.root)
        self.enabled_extensions = normalize_extension_names(enabled_extensions)
        self._extension_metadata = (
            tuple(extension_metadata) if extension_metadata is not None else discover_extensions()
        )
        self._diagnostics_runner = diagnostics_runner
        self._merge_engine = merge_engine
        self._merge_renderer = merge_renderer
        # A CLI-launched server must remain attached so Docker's PID 1 stays alive.
        self._ui_executor = ui_executor or subprocess.run

    # Project and artifact lifecycle -------------------------------------------------

    def create_project(self, name: str, description: str = "") -> ProjectRecord:
        return self.store.create_project(name, description)

    def list_projects(self) -> list[ProjectRecord]:
        return self.store.list_projects()

    def list_artifacts(self, project_reference: str) -> list[SourceArtifact]:
        project = self.resolve_project(project_reference)
        return self.store.list_artifacts(project.id)

    def resolve_project(self, reference: str) -> ProjectRecord:
        normalized = reference.strip()
        if not normalized:
            raise StorageError("project reference cannot be empty")
        projects = self.store.list_projects()
        exact = [project for project in projects if project.id == normalized]
        if exact:
            return exact[0]
        folded = normalized.casefold()
        slug = _slug(normalized)
        matches = [
            project
            for project in projects
            if project.name.casefold() == folded or _slug(project.name) == slug
        ]
        if not matches:
            raise StorageError(f"unknown project: {normalized}")
        if len(matches) > 1:
            raise StorageError(f"project reference is ambiguous: {normalized}")
        return matches[0]

    def delete_project(self, project_reference: str) -> None:
        project = self.resolve_project(project_reference)
        self.retrieval.delete_project(
            project.id,
            include_canonical_sources=False,
        )
        self.store.delete_project(project.id)

    def ingest_paths(
        self,
        project_reference: str,
        paths: Sequence[Path],
    ) -> list[IngestResult]:
        results: list[IngestResult] = []
        for candidate in paths:
            raw = candidate.expanduser()
            if raw.is_symlink():
                raise UnsafeUploadError(f"source path cannot be a symlink: {raw.name}")
            resolved = raw.resolve(strict=True)
            if not resolved.is_file():
                raise UnsafeUploadError(f"source path is not a regular file: {resolved.name}")
            content = read_regular_file_bounded(
                resolved,
                max_bytes=self.settings.max_upload_bytes,
            )
            project = self.resolve_project(project_reference)
            prior_artifact_ids = set(self.store.load_project(project.id).artifact_ids)
            artifact = self.store.put_upload(
                resolved.name,
                content,
                project_id=project.id,
            )
            artifact_was_created = artifact.id not in prior_artifact_ids
            try:
                if artifact.kind in {ArtifactKind.MARKDOWN, ArtifactKind.MDC}:
                    parser = MarkdownParser(
                        self.settings,
                        artifact_dir=(self.store.project_dir(project.id) / "derived" / artifact.id),
                        reference_root=resolved.parent,
                    )
                    document = parser.parse(artifact, project_id=project.id)
                else:
                    document = self.parsers.parse(artifact)
            except Exception:
                self._rollback_ingestion(
                    artifact,
                    artifact_was_created=artifact_was_created,
                    index_may_have_changed=False,
                )
                raise
            results.append(
                self._persist_ingestion(
                    artifact,
                    document,
                    artifact_was_created=artifact_was_created,
                )
            )
        return results

    def ingest_bytes(
        self,
        project_reference: str,
        filename: str,
        content: bytes,
    ) -> IngestResult:
        project = self.resolve_project(project_reference)
        prior_artifact_ids = set(self.store.load_project(project.id).artifact_ids)
        artifact = self.store.put_upload(filename, content, project_id=project.id)
        artifact_was_created = artifact.id not in prior_artifact_ids
        try:
            document = self.parsers.parse(artifact)
        except Exception:
            self._rollback_ingestion(
                artifact,
                artifact_was_created=artifact_was_created,
                index_may_have_changed=False,
            )
            raise
        if artifact.kind in {ArtifactKind.MARKDOWN, ArtifactKind.MDC} and any(
            reference.kind == "missing" for reference in document.references
        ):
            document = document.model_copy(
                update={
                    "warnings": [
                        *document.warnings,
                        ParseWarning(
                            code="byte_upload_relative_assets_require_separate_upload",
                            message=(
                                "Byte uploads cannot discover sibling relative assets; "
                                "upload those assets separately or ingest from a local path."
                            ),
                        ),
                    ]
                }
            )
        return self._persist_ingestion(
            artifact,
            document,
            artifact_was_created=artifact_was_created,
        )

    def _persist_ingestion(
        self,
        artifact: SourceArtifact,
        document: ParsedDocument,
        *,
        artifact_was_created: bool,
    ) -> IngestResult:
        try:
            sanitized = sanitize_parsed_document(document)
            parsed_path = self.store.save_parsed_document(sanitized)
        except Exception:
            self._rollback_ingestion(
                artifact,
                artifact_was_created=artifact_was_created,
                index_may_have_changed=False,
            )
            raise
        try:
            indexed_nodes = self.retrieval.index_document(sanitized)
        except Exception:
            self._rollback_ingestion(
                artifact,
                artifact_was_created=artifact_was_created,
                index_may_have_changed=True,
            )
            raise
        return IngestResult(
            artifact=sanitized.artifact,
            parsed_path=parsed_path,
            block_count=len(sanitized.blocks),
            indexed_nodes=indexed_nodes,
            warnings=sanitized.warnings,
        )

    def _rollback_ingestion(
        self,
        artifact: SourceArtifact,
        *,
        artifact_was_created: bool,
        index_may_have_changed: bool,
    ) -> None:
        targeted_index_error: Exception | None = None
        if index_may_have_changed:
            try:
                self.retrieval.delete_artifact(artifact.project_id, artifact.id)
            except Exception as exc:
                targeted_index_error = exc
        if artifact_was_created:
            self.store.delete_artifact(artifact.project_id, artifact.id)
        if not index_may_have_changed:
            return
        documents = [
            self.store.load_parsed_document(artifact.project_id, remaining.id)
            for remaining in self.store.list_artifacts(artifact.project_id)
        ]
        try:
            self.retrieval.rebuild(artifact.project_id, documents)
        except Exception as exc:
            cause = targeted_index_error or exc
            raise StorageError("failed ingestion index rollback could not be verified") from cause

    def inspect_project(self, project_reference: str) -> ProjectInspection:
        project = self.resolve_project(project_reference)
        artifacts = self.store.list_artifacts(project.id)
        outputs = self.store.list_outputs(project.id)
        block_count = 0
        warnings: list[str] = []
        for artifact in artifacts:
            try:
                document = self.store.load_parsed_document(project.id, artifact.id)
            except StorageError:
                warnings.append(f"artifact has no parsed readback: {artifact.display_name}")
                continue
            block_count += len(document.blocks)
            warnings.extend(item.message for item in document.warnings)
        return ProjectInspection(
            project=project,
            artifact_count=len(artifacts),
            output_count=len(outputs),
            block_count=block_count,
            indexed_nodes=self._indexed_count(project.id),
            artifacts=artifacts,
            outputs=outputs,
            warnings=warnings,
        )

    def inspect_artifact(
        self,
        project_reference: str,
        artifact_reference: str,
    ) -> ParsedDocument:
        project = self.resolve_project(project_reference)
        artifact = self._resolve_artifact(project.id, artifact_reference)
        return self.store.load_parsed_document(project.id, artifact.id)

    def search(
        self,
        project_reference: str,
        query: str,
        *,
        limit: int = 5,
    ) -> list[RetrievalHit]:
        project = self.resolve_project(project_reference)
        return self.retrieval.search(project.id, query, limit=limit)

    def rebuild_index(self, project_reference: str) -> int:
        project = self.resolve_project(project_reference)
        documents = [
            self.store.load_parsed_document(project.id, artifact.id)
            for artifact in self.store.list_artifacts(project.id)
        ]
        return self.retrieval.rebuild(project.id, documents)

    # Handoff generation ------------------------------------------------------------

    def generate_handoff(
        self,
        project_reference: str,
        *,
        mode: HandoffMode = HandoffMode.PRE_COMPACT,
        profile: TemplateProfile | None = None,
        routes: Mapping[int, ModelRoute] | None = None,
    ) -> GenerationOutcome:
        return self._generate_handoff(
            project_reference,
            mode=mode,
            profile=profile,
            routes=routes,
        )

    def generate_lifecycle_handoff(
        self,
        project_reference: str,
        *,
        event: HandoffMode = HandoffMode.PRE_COMPACT,
        lifecycle_event_id: str,
    ) -> LifecycleArtifact:
        """Create or resume exactly one offline lifecycle output."""

        profile = (
            TemplateProfile.CODEX_PRECOMPACT_V1
            if event is HandoffMode.PRE_COMPACT
            else TemplateProfile.CODEX_POST_CHAT_V1
        )

        outcome = self._generate_handoff(
            project_reference,
            mode=event,
            profile=profile,
            routes=_offline_routes(),
            job_id=lifecycle_job_id(lifecycle_event_id),
        )
        if (
            outcome.job.status is not JobStatus.COMPLETE
            or outcome.output is None
            or outcome.validation is None
            or not outcome.validation.valid
        ):
            raise StorageError("lifecycle generation did not produce a validated output")
        output = self.store.get_output(outcome.output.project_id, outcome.output.id)
        if output.metadata.get("profile") != profile.value:
            raise StorageError("lifecycle output profile failed readback")
        self.validate_output(
            output.project_id,
            output.id,
            profile,
        )
        return LifecycleArtifact(
            output_id=output.id,
            project_id=output.project_id,
            path=output.stored_path,
            sha256=output.sha256,
            profile=profile.value,
        )

    def _generate_handoff(
        self,
        project_reference: str,
        *,
        mode: HandoffMode,
        profile: TemplateProfile | None,
        routes: Mapping[int, ModelRoute] | None,
        job_id: str | None = None,
    ) -> GenerationOutcome:
        project = self.resolve_project(project_reference)
        selected_profile = profile or _profile_for_mode(mode)
        route_matrix = dict(routes or _offline_routes())
        if set(route_matrix) != set(range(1, 13)):
            raise ValueError("route matrix must contain Sections 1 through 12")
        runner = self._job_runner(project, route_matrix)
        if job_id is not None and self.store.job_checkpoint_exists(project.id, job_id):
            existing = _StoreCheckpointAdapter(self.store, project.id).load(job_id)
            if (
                existing.mode != mode
                or existing.profile != selected_profile
                or existing.route_matrix != route_matrix
            ):
                raise StorageError("lifecycle job checkpoint does not match the requested profile")
            completed = (
                runner.run(job_id)
                if existing.status is JobStatus.COMPLETE
                else runner.resume(job_id)
            )
            return self._finalize_job(runner, completed)
        job = runner.create_job(
            mode=mode,
            profile=selected_profile,
            route_matrix=route_matrix,
            job_id=job_id,
        )
        return self._finalize_job(runner, runner.run(job.id))

    def resume_job(self, project_reference: str, job_id: str) -> GenerationOutcome:
        project = self.resolve_project(project_reference)
        checkpoint = _StoreCheckpointAdapter(self.store, project.id)
        existing = checkpoint.load(job_id)
        runner = self._job_runner(project, existing.route_matrix)
        return self._finalize_job(runner, runner.resume(job_id))

    def cancel_job(self, project_reference: str, job_id: str) -> GenerationJob:
        project = self.resolve_project(project_reference)
        checkpoint = _StoreCheckpointAdapter(self.store, project.id)
        existing = checkpoint.load(job_id)
        runner = self._job_runner(project, existing.route_matrix)
        return runner.request_cancel(job_id)

    def list_outputs(self, project_reference: str) -> list[StoredOutput]:
        project = self.resolve_project(project_reference)
        return self.store.list_outputs(project.id)

    def validate_output(
        self,
        project_reference: str,
        output_reference: str,
        profile: TemplateProfile,
    ) -> HandoffValidationReport:
        project = self.resolve_project(project_reference)
        output = self._resolve_output(project.id, output_reference)
        text = self.store.read_output(project.id, output.id).decode("utf-8")
        handoff_text = text.split("\n## Unified Execution Plan", 1)[0]
        return validate_handoff(handoff_text, profile)

    def validate_path(
        self,
        path: Path,
        profile: TemplateProfile,
    ) -> HandoffValidationReport:
        raw = path.expanduser()
        if raw.is_symlink():
            raise StorageError("validation path cannot be a symlink")
        resolved = raw.resolve(strict=True)
        if not resolved.is_file():
            raise StorageError("validation path is not a regular file")
        content = resolved.read_bytes()
        if len(content) > self.settings.max_markdown_characters * 4:
            raise StorageError("validation target exceeds the configured size limit")
        text = content.decode("utf-8")
        handoff_text = text.split("\n## Unified Execution Plan", 1)[0]
        return validate_handoff(handoff_text, profile)

    def merge_outputs(
        self,
        project_reference: str,
        output_references: Sequence[str],
        *,
        target_profile: TemplateProfile = TemplateProfile.GOAL_V1,
    ) -> MergeOutcome:
        """Compatibility wrapper for callers that already use output terminology."""

        return self.merge_handoffs(
            project_reference,
            output_references,
            target_profile=target_profile,
        )

    def merge_handoffs(
        self,
        project_reference: str,
        references: Sequence[str],
        *,
        target_profile: TemplateProfile = TemplateProfile.GOAL_V1,
    ) -> MergeOutcome:
        """Merge managed generated outputs or uploaded Markdown/MDC handoff artifacts."""

        project = self.resolve_project(project_reference)
        if len(references) < 2:
            raise CapabilityError("merge requires at least two managed handoffs")
        engine, renderer = self._merge_components()
        paths = [self._resolve_handoff_path(project.id, reference) for reference in references]
        plan = engine.merge_files(paths, target_profile=target_profile)
        if not isinstance(plan, MergedPlan):
            raise CapabilityError("merge engine returned an unsupported plan")
        rendered = renderer(plan)
        destination = self.store.put_output(
            project.id,
            f"{_slug(project.name)}-merged.handoff.mdc",
            rendered,
            metadata={
                "kind": "merged-handoff",
                "content_hash": plan.content_hash,
                "profile": target_profile.value,
            },
        )
        return MergeOutcome(plan=plan, output=self._output_for_path(project.id, destination))

    # Safe continuation and platform actions ---------------------------------------

    def launch_output(
        self,
        project_reference: str,
        output_reference: str,
        *,
        harness: str,
        model: str | None = None,
        working_directory: Path | None = None,
        execute: bool = False,
    ) -> LaunchResult:
        project = self.resolve_project(project_reference)
        output = self._resolve_output(project.id, output_reference)
        raw_profile = output.metadata.get("profile")
        try:
            profile = TemplateProfile(str(raw_profile))
        except (TypeError, ValueError):
            raise CapabilityError(
                "this output has no recognized handoff profile and cannot start a session"
            ) from None
        # Validation is an application boundary, not merely a UI affordance. This keeps
        # every caller—including the CLI—from preparing or executing a destination
        # command for a malformed or subsequently altered managed handoff.
        self.validate_output(project.id, output.id, profile)
        return self.launcher.launch(
            harness,
            output.stored_path,
            model=model,
            working_directory=working_directory,
            execute=execute,
        )

    def copy_output(
        self,
        project_reference: str,
        output_reference: str,
        *,
        as_uri: bool = False,
        execute: bool = False,
    ) -> ActionResult:
        project = self.resolve_project(project_reference)
        output = self._resolve_output(project.id, output_reference)
        payload = output.stored_path.as_uri() if as_uri else str(output.stored_path)
        if execute:
            return self.platform_actions.copy_path(output.stored_path, as_uri=as_uri)
        return ActionResult(
            action="copy",
            path=output.stored_path,
            payload=payload,
            executed=False,
            message=f"Preview only; copy this value when ready: {payload}",
        )

    def open_output(
        self,
        project_reference: str,
        output_reference: str,
        *,
        execute: bool = False,
    ) -> ActionResult:
        project = self.resolve_project(project_reference)
        output = self._resolve_output(project.id, output_reference)
        if execute:
            return self.platform_actions.reveal(output.stored_path)
        return ActionResult(
            action="reveal",
            path=output.stored_path,
            payload=str(output.stored_path),
            executed=False,
            message=f"Preview only; reveal this managed file: {output.stored_path}",
        )

    # Diagnostics, demo, and UI -----------------------------------------------------

    def doctor(self) -> dict[str, Any]:
        checks = self._diagnostics_runner(self.settings)
        return {
            "ready": diagnostics_ready(checks),
            "offline": self.settings.offline,
            "network_enabled": self.settings.network_enabled,
            "data_root": str(self.settings.data_root),
            "checks": [asdict(check) for check in checks],
            "providers": [status.model_dump(mode="json") for status in self.providers.statuses()],
            "enabled_extensions": list(self.enabled_extensions),
            "extensions": [asdict(item) for item in self.list_extensions()],
            "available_harnesses": list(self.available_harnesses()),
        }

    def provider_statuses(self) -> tuple[ProviderStatus, ...]:
        return self.providers.statuses()

    def list_extensions(self) -> tuple[ExtensionInfo, ...]:
        """List installed extension metadata without importing disabled extension code."""

        return describe_extensions(
            self.enabled_extensions,
            metadata_items=self._extension_metadata,
        )

    def available_harnesses(self) -> tuple[str, ...]:
        """Return only destination CLIs that are installed and executable."""

        return self.launcher.available_harnesses()

    def diagnostics(self) -> dict[str, Any]:
        return self.doctor()

    def materialize_demo(self) -> DemoOutcome:
        project = self.create_project(
            "Handoff Forge Demo",
            "Credential-free local workflow demonstration.",
        )
        ingested = [
            self.ingest_bytes(
                project.id,
                "project-context.md",
                (
                    b"# Project purpose\n\nBuild a local-first handoff with deterministic "
                    b"retrieval and explicit validation.\n"
                ),
            ),
            self.ingest_bytes(
                project.id,
                "current-state.mdc",
                (
                    b"---\nalwaysApply: false\n---\n# Current state\n\n"
                    b"Offline generation is enabled. Next task: inspect the generated handoff.\n"
                ),
            ),
            self.ingest_bytes(
                project.id,
                "northstar-continuity-review.pdf",
                _demo_pdf_bytes(),
            ),
        ]
        generation = self.generate_handoff(project.id, mode=HandoffMode.PRE_COMPACT)
        return DemoOutcome(project=project, ingested=ingested, generation=generation)

    def launch_ui(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8501,
        execute: bool = False,
    ) -> UIProcessResult:
        if not 1 <= port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        safe_host = _validate_host(host)
        app_path = Path(__file__).parent / "ui" / "app.py"
        argv = (
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.port",
            str(port),
            "--server.address",
            safe_host,
            "--server.headless",
            "true",
            "--server.maxUploadSize",
            str(max(1, (self.settings.max_upload_bytes + 1024**2 - 1) // 1024**2)),
            "--browser.gatherUsageStats",
            "false",
            "--client.toolbarMode",
            "minimal",
            "--theme.base",
            "light",
            "--theme.primaryColor",
            "#1D4ED8",
            "--theme.backgroundColor",
            "#F5F6F8",
            "--theme.secondaryBackgroundColor",
            "#F0F2F5",
            "--theme.textColor",
            "#1F2937",
            "--theme.font",
            "sans serif",
        )
        if not execute:
            return UIProcessResult(argv=argv)
        if not app_path.is_file():
            raise CapabilityError("Streamlit application module is not available")
        child_environment = os.environ.copy()
        child_environment.update(
            {
                "HANDOFF_FORGE_DATA_ROOT": str(self.settings.data_root),
                "HANDOFF_FORGE_OFFLINE": str(self.settings.offline).lower(),
                "HANDOFF_FORGE_ALLOW_NETWORK": str(self.settings.allow_network).lower(),
                "HANDOFF_FORGE_ENABLED_EXTENSIONS": ",".join(self.enabled_extensions),
            }
        )
        try:
            process = self._ui_executor(
                list(argv),
                cwd=str(self.settings.data_root),
                env=child_environment,
                shell=False,
            )
        except Exception as error:
            raise CapabilityError(f"could not start Streamlit ({type(error).__name__})") from None
        pid = getattr(process, "pid", None)
        returncode = getattr(process, "returncode", None)
        if isinstance(returncode, int) and returncode != 0:
            raise CapabilityError(f"Streamlit exited with status {returncode}")
        return UIProcessResult(
            argv=argv,
            executed=True,
            pid=pid if isinstance(pid, int) else None,
            returncode=returncode if isinstance(returncode, int) else None,
        )

    # Internal assembly -------------------------------------------------------------

    def _job_runner(
        self,
        project: ProjectRecord,
        routes: Mapping[int, ModelRoute],
    ) -> GenerationJobRunner:
        sources = self.store.list_artifacts(project.id)
        documents = [self.store.load_parsed_document(project.id, source.id) for source in sources]
        evidence = self._evidence_by_section(project.id, documents, routes)
        composer = OfflineHandoffComposer(
            project_id=project.id,
            project_name=project.name,
            purpose=project.description or "Continue the documented project work safely.",
        )
        return GenerationJobRunner(
            generator=self.providers,
            checkpoint_store=_StoreCheckpointAdapter(self.store, project.id),
            composer=composer,
            evidence_by_section=evidence,
            sources=sources,
            max_retries=0,
        )

    def _evidence_by_section(
        self,
        project_id: str,
        documents: Sequence[ParsedDocument],
        routes: Mapping[int, ModelRoute],
    ) -> dict[int, list[ContentBlock]]:
        blocks = [block for document in documents for block in document.blocks]
        block_by_id = {block.id: block for block in blocks}
        ordered = sorted(blocks, key=lambda block: (block.order, block.artifact_id, block.id))
        evidence: dict[int, list[ContentBlock]] = {}
        for spec in HANDOFF_SECTION_SPECS:
            # Resolve the configured provider early, while keeping every visual block's
            # extracted text eligible for retrieval. The provider boundary decides whether
            # its managed artifact path may be read and uploaded for this exact route.
            self.providers.get(routes[spec.id].provider)
            candidates = ordered
            query = " ".join(spec.evidence_queries)
            hits = self.retrieval.search(project_id, query, limit=8)
            selected_ids = [
                str(hit.metadata.get("block_id")) for hit in hits if hit.metadata.get("block_id")
            ]
            selected = [
                block_by_id[block_id]
                for block_id in selected_ids
                if block_id in block_by_id and block_by_id[block_id] in candidates
            ]
            evidence[spec.id] = selected or candidates[:8]
        return evidence

    def _finalize_job(
        self,
        runner: GenerationJobRunner,
        job: GenerationJob,
    ) -> GenerationOutcome:
        if job.status is not JobStatus.COMPLETE:
            return GenerationOutcome(job=job)
        package = runner.package(job.id)
        rendered = render_handoff(package, package.profile)
        validation = validate_handoff(rendered, package.profile)
        if job.output_path is not None and Path(job.output_path).is_file():
            output = self._output_for_path(job.project_id, Path(job.output_path))
        else:
            destination = self.store.put_output(
                job.project_id,
                handoff_filename(package, package.profile),
                rendered,
                metadata={
                    "kind": "generated-handoff",
                    "job_id": job.id,
                    "package_id": package.id,
                    "mode": package.mode.value,
                    "profile": package.profile.value,
                },
                idempotency_key=f"generation-job:{job.id}",
            )
            output = self._output_for_path(job.project_id, destination)
            job.output_path = output.stored_path
            job.updated_at = output.created_at
            runner.store.save(job)
        return GenerationOutcome(
            job=job,
            package=package,
            output=output,
            validation=validation,
        )

    def _resolve_artifact(self, project_id: str, reference: str) -> SourceArtifact:
        artifacts = self.store.list_artifacts(project_id)
        matches = [
            artifact
            for artifact in artifacts
            if artifact.id == reference or artifact.display_name == reference
        ]
        if not matches:
            raise StorageError(f"unknown artifact: {reference}")
        if len(matches) > 1:
            raise StorageError(f"artifact reference is ambiguous: {reference}")
        return matches[0]

    def _resolve_output(self, project_id: str, reference: str) -> StoredOutput:
        outputs = self.store.list_outputs(project_id)
        matches = [
            output
            for output in outputs
            if output.id == reference
            or output.display_name == reference
            or output.stored_path.name == reference
            or str(output.stored_path) == reference
        ]
        if not matches:
            raise StorageError(f"unknown output: {reference}")
        if len(matches) > 1:
            raise StorageError(f"output reference is ambiguous: {reference}")
        return matches[0]

    def _resolve_handoff_path(self, project_id: str, reference: str) -> Path:
        matches: list[Path] = []
        for output in self.store.list_outputs(project_id):
            if (
                output.id == reference
                or output.display_name == reference
                or output.stored_path.name == reference
                or str(output.stored_path) == reference
            ):
                matches.append(output.stored_path)
        for artifact in self.store.list_artifacts(project_id):
            if (
                artifact.id == reference
                or artifact.display_name == reference
                or artifact.stored_path.name == reference
                or str(artifact.stored_path) == reference
            ):
                matches.append(artifact.stored_path)
        unique = {str(path.resolve(strict=True)): path.resolve(strict=True) for path in matches}
        if not unique:
            raise StorageError(f"unknown managed handoff: {reference}")
        if len(unique) > 1:
            raise StorageError(f"managed handoff reference is ambiguous: {reference}")
        path = next(iter(unique.values()))
        if path.suffix.casefold() not in {".md", ".mdc"}:
            raise CapabilityError("merge inputs must be Markdown or MDC handoff files")
        return path

    def _output_for_path(self, project_id: str, path: Path) -> StoredOutput:
        resolved = path.resolve(strict=True)
        for output in self.store.list_outputs(project_id):
            if output.stored_path.resolve(strict=True) == resolved:
                return output
        raise StorageError("generated output manifest was not readable after write")

    def _indexed_count(self, project_id: str) -> int:
        direct = getattr(self.retrieval, "count", None)
        if callable(direct):
            return int(direct(project_id))
        index = getattr(self.retrieval, "index", None)
        counter = getattr(index, "count", None)
        if callable(counter):
            return int(counter(project_id=project_id))
        return 0

    def _merge_components(self) -> tuple[Any, Callable[[MergedPlan], str]]:
        if self._merge_engine is not None and self._merge_renderer is not None:
            return self._merge_engine, self._merge_renderer
        try:
            from handoff_forge.merge.engine import MergeEngine
            from handoff_forge.merge.planner import render_merged_handoff
        except ImportError as error:
            raise CapabilityError("merge support is not available in this installation") from error
        return self._merge_engine or MergeEngine(), self._merge_renderer or render_merged_handoff

    # Compact compatibility names for UI and embedding callers. --------------------

    def ingest(self, project_reference: str, filename: str, content: bytes) -> IngestResult:
        return self.ingest_bytes(project_reference, filename, content)

    def inspect(self, project_reference: str, artifact_reference: str) -> ParsedDocument:
        return self.inspect_artifact(project_reference, artifact_reference)

    def generate(
        self,
        project_reference: str,
        *,
        mode: HandoffMode = HandoffMode.PRE_COMPACT,
        profile: TemplateProfile | None = None,
        routes: Mapping[int, ModelRoute] | None = None,
    ) -> GenerationOutcome:
        return self.generate_handoff(
            project_reference,
            mode=mode,
            profile=profile,
            routes=routes,
        )

    def validate(
        self,
        target: str | Path,
        profile: TemplateProfile,
        *,
        project_reference: str | None = None,
    ) -> HandoffValidationReport:
        if project_reference is None:
            return self.validate_path(Path(target), profile)
        return self.validate_output(project_reference, str(target), profile)

    def merge(
        self,
        project_reference: str,
        references: Sequence[str],
        *,
        profile: TemplateProfile = TemplateProfile.GOAL_V1,
    ) -> MergeOutcome:
        return self.merge_handoffs(
            project_reference,
            references,
            target_profile=profile,
        )

    def copy_path(
        self,
        project_reference: str,
        output_reference: str,
        *,
        as_uri: bool = False,
        execute: bool = False,
    ) -> ActionResult:
        return self.copy_output(
            project_reference,
            output_reference,
            as_uri=as_uri,
            execute=execute,
        )

    def open_path(
        self,
        project_reference: str,
        output_reference: str,
        *,
        execute: bool = False,
    ) -> ActionResult:
        return self.open_output(project_reference, output_reference, execute=execute)

    def reveal(
        self,
        project_reference: str,
        output_reference: str,
        *,
        execute: bool = False,
    ) -> ActionResult:
        return self.open_output(project_reference, output_reference, execute=execute)

    def launch(
        self,
        project_reference: str,
        output_reference: str,
        *,
        harness: str,
        model: str | None = None,
        working_directory: Path | None = None,
        execute: bool = False,
    ) -> LaunchResult:
        return self.launch_output(
            project_reference,
            output_reference,
            harness=harness,
            model=model,
            working_directory=working_directory,
            execute=execute,
        )


def build_application(
    settings: HandoffSettings | None = None,
    *,
    enabled_extensions: Iterable[str] = (),
    extension_entry_points: Iterable[ExtensionEntryPoint] | None = None,
) -> HandoffApplication:
    """Build the canonical CLI/UI service and load only allowlisted extensions."""

    resolved_settings = settings or HandoffSettings()
    resolved_entry_points = (
        tuple(extension_entry_points) if extension_entry_points is not None else None
    )
    store = ContentAddressedStore(resolved_settings)
    providers = build_default_registry(
        network_enabled=resolved_settings.network_enabled,
        managed_root=store.root,
        timeout_seconds=resolved_settings.provider_timeout_seconds,
        max_retries=resolved_settings.provider_max_retries,
    )
    harnesses = build_default_harness_registry()
    enabled = load_enabled_extensions(
        enabled_extensions,
        settings=resolved_settings,
        managed_root=store.root,
        providers=providers,
        harnesses=harnesses,
        entry_points=resolved_entry_points,
    )
    extension_metadata = discover_extensions(entry_points=resolved_entry_points)
    launcher = HarnessLauncher(managed_root=store.root, registry=harnesses)
    return HandoffApplication(
        settings=resolved_settings,
        store=store,
        providers=providers,
        launcher=launcher,
        enabled_extensions=enabled,
        extension_metadata=extension_metadata,
    )


def _profile_for_mode(mode: HandoffMode) -> TemplateProfile:
    if mode is HandoffMode.PRE_COMPACT:
        return TemplateProfile.CODEX_PRECOMPACT_V1
    return TemplateProfile.CODEX_POST_CHAT_V1


def _offline_routes() -> dict[int, ModelRoute]:
    return {
        section_id: ModelRoute(provider="offline", model="extractive-v1")
        for section_id in range(1, 13)
    }


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "project"


def _validate_host(value: str) -> str:
    host = value.strip()
    if not host:
        raise ValueError("host cannot be empty")
    try:
        ip_address(host)
    except ValueError:
        if len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise ValueError("host must be an IP address or conservative DNS name") from None
        labels = host.rstrip(".").split(".")
        if any(
            not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
            for label in labels
        ):
            raise ValueError("host contains an invalid DNS label") from None
    return host


def _demo_pdf_bytes() -> bytes:
    """Read the bundled multimodal demo PDF in source and installed layouts."""

    name = "northstar-continuity-review.pdf"
    source_tree = Path(__file__).resolve().parents[2] / "examples" / name
    if source_tree.is_file() and not source_tree.is_symlink():
        return source_tree.read_bytes()
    try:
        packaged = resources.files("handoff_forge").joinpath("resources").joinpath(name)
        content = packaged.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        raise CapabilityError("bundled multimodal demo PDF is unavailable") from None
    if not content.startswith(b"%PDF-"):
        raise CapabilityError("bundled multimodal demo PDF has an invalid signature")
    return content


ApplicationService = HandoffApplication


__all__ = [
    "ApplicationService",
    "DemoOutcome",
    "GenerationOutcome",
    "HandoffApplication",
    "IngestResult",
    "MergeOutcome",
    "ProjectInspection",
    "UIProcessResult",
    "build_application",
]
