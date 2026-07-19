"""Section-boundary checkpoints, retry, cancel, and resume behavior."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from handoff_forge.errors import HandoffValidationError
from handoff_forge.handoffs.catalog import SECTION_BY_ID
from handoff_forge.handoffs.composer import OfflineHandoffComposer
from handoff_forge.handoffs.confidence import explicit_verification_source_ids
from handoff_forge.handoffs.inventory import (
    InventoryScanResult,
    inventory_scan_marker,
    scan_section_ten_inventory,
)
from handoff_forge.handoffs.prompts import build_generation_request
from handoff_forge.models import (
    ContentBlock,
    GenerationJob,
    GenerationRequest,
    GenerationResult,
    HandoffMode,
    HandoffPackage,
    InventoryItem,
    JobStatus,
    ModelRoute,
    SourceArtifact,
    TemplateProfile,
    utc_now,
)

MAX_SECTION_EVIDENCE_BLOCKS = 64


@dataclass(frozen=True, slots=True)
class _BoundedEvidence:
    blocks: tuple[ContentBlock, ...]
    omitted_count: int
    truncated_count: int


class SectionGenerator(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class CheckpointStore(Protocol):
    def save(self, job: GenerationJob) -> None: ...

    def load(self, job_id: str) -> GenerationJob: ...


class InMemoryCheckpointStore:
    """JSON-equivalent deep-copy checkpoints for deterministic tests and demos."""

    def __init__(self) -> None:
        self._jobs: dict[str, GenerationJob] = {}

    def save(self, job: GenerationJob) -> None:
        self._jobs[job.id] = job.model_copy(deep=True)

    def load(self, job_id: str) -> GenerationJob:
        try:
            return self._jobs[job_id].model_copy(deep=True)
        except KeyError as exc:
            raise KeyError(f"unknown generation job {job_id}") from exc


class GenerationJobRunner:
    def __init__(
        self,
        *,
        generator: SectionGenerator,
        checkpoint_store: CheckpointStore,
        composer: OfflineHandoffComposer,
        evidence_by_section: Mapping[int, Sequence[ContentBlock]],
        sources: Sequence[SourceArtifact] = (),
        max_retries: int = 1,
    ) -> None:
        self.generator = generator
        self.store = checkpoint_store
        self.composer = composer
        self.evidence_by_section = evidence_by_section
        self.sources = tuple(sources)
        self.max_retries = max(0, max_retries)

    def create_job(
        self,
        *,
        mode: HandoffMode,
        profile: TemplateProfile,
        route_matrix: Mapping[int, ModelRoute],
        inventory: Sequence[InventoryItem] = (),
    ) -> GenerationJob:
        if set(route_matrix) != set(range(1, 13)):
            raise ValueError("route matrix must contain Sections 1 through 12")
        job = GenerationJob(
            id=f"job-{uuid4().hex}",
            project_id=self.composer.project_id,
            mode=mode,
            profile=profile,
            route_matrix=dict(route_matrix),
            inventory=list(inventory),
        )
        self.store.save(job)
        return job

    def run(self, job_id: str) -> GenerationJob:
        job = self.store.load(job_id)
        if job.status is JobStatus.COMPLETE:
            self._checkpoint_inventory_scan(job, self._bounded_evidence(10))
            return self.store.load(job_id)
        if job.status is JobStatus.CANCEL_REQUESTED:
            job.status = JobStatus.CANCELLED
            job.updated_at = utc_now()
            self.store.save(job)
            return job
        job.status = JobStatus.RUNNING
        job.error = None
        self.store.save(job)
        completed = {section.id for section in job.completed_sections}
        if 10 in completed:
            selection = self._bounded_evidence(10)
            self._checkpoint_inventory_scan(job, selection)
        for section_id in range(1, 13):
            latest = self.store.load(job_id)
            if latest.status is JobStatus.CANCEL_REQUESTED:
                latest.status = JobStatus.CANCELLED
                latest.updated_at = utc_now()
                self.store.save(latest)
                return latest
            job = latest
            if section_id in completed:
                continue
            selection = self._bounded_evidence(section_id)
            if section_id == 10:
                self._checkpoint_inventory_scan(job, selection)
            evidence = list(selection.blocks)
            request = build_generation_request(
                section_id=section_id,
                evidence=evidence,
                route=job.route_matrix[section_id],
                omitted_source_count=selection.omitted_count,
                truncated_source_count=selection.truncated_count,
                inventory=job.inventory,
            )
            try:
                result = self._generate_with_retry(request)
            except Exception as exc:  # provider adapters expose heterogeneous failures
                job.status = JobStatus.FAILED
                job.error = _sanitize_error(str(exc))
                job.updated_at = utc_now()
                self.store.save(job)
                return job
            verified_ids, current_ids, revalidation_ids = explicit_verification_source_ids(
                request.evidence
            )
            section = self.composer.section_from_text(
                section_id=section_id,
                text=result.text,
                evidence=request.evidence,
                current_session_source_ids=current_ids,
                verified_source_ids=verified_ids,
                needs_revalidation_source_ids=revalidation_ids,
                sources=self.sources,
            )
            job.completed_sections.append(section)
            job.completed_sections.sort(key=lambda item: item.id)
            completed.add(section_id)
            job.updated_at = utc_now()
            self.store.save(job)
        selection = self._bounded_evidence(10)
        self._checkpoint_inventory_scan(job, selection)
        job.status = JobStatus.COMPLETE
        job.updated_at = utc_now()
        self.store.save(job)
        return job

    def resume(
        self,
        job_id: str,
        *,
        route_overrides: Mapping[int, ModelRoute] | None = None,
    ) -> GenerationJob:
        job = self.store.load(job_id)
        if route_overrides:
            for section_id, route in route_overrides.items():
                if not 1 <= section_id <= 12:
                    raise ValueError(f"invalid route override Section {section_id}")
                job.route_matrix[section_id] = route
        if job.status in {JobStatus.CANCELLED, JobStatus.COMPLETE}:
            return job
        job.status = JobStatus.PENDING
        job.error = None
        job.updated_at = utc_now()
        self.store.save(job)
        return self.run(job_id)

    def request_cancel(self, job_id: str) -> GenerationJob:
        job = self.store.load(job_id)
        if job.status not in {JobStatus.COMPLETE, JobStatus.CANCELLED}:
            job.status = JobStatus.CANCEL_REQUESTED
            job.updated_at = utc_now()
            self.store.save(job)
        return job

    def package(self, job_id: str) -> HandoffPackage:
        job = self.store.load(job_id)
        if job.status is not JobStatus.COMPLETE:
            raise HandoffValidationError("partial generation job cannot render a valid final MDC")
        selection = self._bounded_evidence(10)
        scan = self._inventory_scan(job.inventory, selection)
        package = self.composer.package_from_sections(
            mode=job.mode,
            profile=job.profile,
            sections=job.completed_sections,
            inventory=scan.items,
            sources=self.sources,
            routes=job.route_matrix,
        )
        marker = inventory_scan_marker(scan.evidence_scanned)
        return package.model_copy(
            update={"unverified_boundaries": [*package.unverified_boundaries, marker]},
            deep=True,
        )

    def _generate_with_retry(self, request: GenerationRequest) -> GenerationResult:
        last_error: Exception | None = None
        for _attempt in range(self.max_retries + 1):
            try:
                return self.generator.generate(request)
            except Exception as exc:  # provider adapters expose heterogeneous failures
                last_error = exc
        if last_error is None:
            raise RuntimeError("generation retry loop ended without a result or provider error")
        raise last_error

    def _bounded_evidence(self, section_id: int) -> _BoundedEvidence:
        return _select_bounded_evidence(
            self.evidence_by_section.get(section_id, ()),
            char_budget=SECTION_BY_ID[section_id].evidence_char_budget,
        )

    def _checkpoint_inventory_scan(
        self,
        job: GenerationJob,
        selection: _BoundedEvidence,
    ) -> InventoryScanResult:
        scan = self._inventory_scan(job.inventory, selection)
        job.inventory = list(scan.items)
        job.updated_at = utc_now()
        self.store.save(job)
        return scan

    def _inventory_scan(
        self,
        existing: Sequence[InventoryItem],
        selection: _BoundedEvidence,
    ) -> InventoryScanResult:
        selection_note = (
            f"Section 10 evidence selection: {len(selection.blocks)} selected; "
            f"{selection.omitted_count} omitted; {selection.truncated_count} truncated"
        )
        return scan_section_ten_inventory(
            selection.blocks,
            sources=self.sources,
            existing=existing,
            selection_notes=(selection_note,),
        )


def _select_bounded_evidence(
    evidence: Sequence[ContentBlock],
    *,
    char_budget: int,
    max_blocks: int = MAX_SECTION_EVIDENCE_BLOCKS,
) -> _BoundedEvidence:
    if char_budget < 1:
        raise ValueError("evidence character budget must be positive")
    if max_blocks < 1:
        raise ValueError("evidence block limit must be positive")
    ordered = sorted(evidence, key=lambda block: (block.order, block.artifact_id, block.id))
    selected: list[ContentBlock] = []
    used = 0
    omitted = 0
    truncated = 0
    for index, block in enumerate(ordered):
        if len(selected) >= max_blocks:
            omitted += len(ordered) - index
            break
        remaining = char_budget - used
        if remaining <= 0:
            omitted += len(ordered) - index
            break
        if len(block.text) <= remaining:
            selected.append(block)
            used += len(block.text)
            continue
        selected.append(block.model_copy(update={"text": _truncate_text(block.text, remaining)}))
        truncated += 1
        omitted += len(ordered) - index - 1
        break
    return _BoundedEvidence(tuple(selected), omitted, truncated)


def _truncate_text(text: str, char_budget: int) -> str:
    if char_budget == 1:
        return "…"
    prefix = text[: char_budget - 1].rstrip()
    return f"{prefix}…" if prefix else text[:char_budget]


_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret)\s*[=:]\s*\S+"),
)


def _sanitize_error(value: str) -> str:
    sanitized = value
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized[:1_000]
