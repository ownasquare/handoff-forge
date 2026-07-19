"""Provider-neutral canonical data models.

Only these JSON-safe models are durable. Framework and provider SDK objects are
transient adapters so stored projects remain portable and rebuildable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class ArtifactKind(StrEnum):
    MARKDOWN = "markdown"
    MDC = "mdc"
    PDF = "pdf"
    IMAGE = "image"
    PAGE_RENDER = "page_render"
    TABLE = "table"
    OTHER = "other"


class BlockKind(StrEnum):
    TEXT = "text"
    HEADING = "heading"
    LIST = "list"
    CODE = "code"
    TABLE = "table"
    OCR = "ocr"
    IMAGE = "image"
    CHART = "chart"
    PAGE_RENDER = "page_render"


class HandoffMode(StrEnum):
    PRE_COMPACT = "pre-compact"
    POST_TASK = "post-task"


class TemplateProfile(StrEnum):
    GOAL_V1 = "goal-v1"
    CODEX_PRECOMPACT_V1 = "codex-precompact-v1"
    CODEX_POST_CHAT_V1 = "codex-post-chat-v1"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETE = "complete"


class ProjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    artifact_ids: list[str] = Field(default_factory=list)
    output_ids: list[str] = Field(default_factory=list)


class SourceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    display_name: str
    sha256: str
    media_type: str
    size_bytes: int
    kind: ArtifactKind
    stored_path: Path
    file_uri: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    page_number: int | None = None
    actionable: bool = True


class DocumentReference(BaseModel):
    """A Markdown/MDC asset or link reference without implicit network access."""

    model_config = ConfigDict(extra="forbid")

    reference: str
    kind: Literal["local", "remote", "missing", "anchor"]
    resolved_path: Path | None = None
    artifact_id: str | None = None


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    artifact_id: str
    artifact_sha256: str
    kind: BlockKind
    text: str
    order: int
    page_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    artifact_path: Path | None = None
    extraction_method: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def source_id(self) -> str:
        """Compatibility name for the canonical source artifact identifier."""

        return self.artifact_id

    @field_validator("text")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content block text cannot be empty")
        return normalized


class ParsedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: SourceArtifact
    blocks: list[ContentBlock]
    warnings: list[ParseWarning] = Field(default_factory=list)
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    references: list[DocumentReference] = Field(default_factory=list)
    parser_profile: str


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    artifact_sha256: str
    display_name: str
    block_id: str | None = None
    page_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    artifact_path: Path | None = None
    relevance: float = Field(default=1.0, ge=0.0, le=1.0)


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: bool = True
    image_input: bool = False
    native_pdf: bool = False
    document_search: bool = False
    structured_output: bool = False
    streaming: bool = False
    supported_mime_types: tuple[str, ...] = ()
    max_bytes: int | None = None
    max_pages: int | None = None
    context_tokens: int | None = None
    stability: Literal["stable", "beta", "local"] = "stable"


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(
        default="offline",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    model: str = Field(
        default="extractive-v1",
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]*$",
    )
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=2_000, ge=64, le=100_000)
    allow_cloud_upload: bool = False
    include_visual_evidence: bool = False

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        return str(value).strip().casefold()

    @field_validator("model", mode="before")
    @classmethod
    def _normalize_model(cls, value: object) -> str:
        return str(value).strip()


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: int = Field(ge=1, le=12)
    system_prompt: str
    user_prompt: str
    evidence: list[ContentBlock] = Field(default_factory=list)
    image_paths: list[Path] = Field(default_factory=list)
    route: ModelRoute


class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    provider: str
    model: str
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class InventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    who: str
    what: str
    how_discovered: str
    where: str
    when: str
    description: str
    acceptance_criteria: list[str]
    definition_of_done: list[str]
    root_cause: str
    priority: Literal["P0", "P1", "P2", "P3", "P4"]
    priority_rationale: str
    regression_prevention: list[str]
    testing: list[str]
    audit_policies: list[str]
    adjacent_considerations: list[str]
    source_refs: list[str] = Field(default_factory=list)


class HandoffSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1, le=12)
    title: str
    content: str
    confidence: ConfidenceLevel
    freshness_basis: str
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("content", "freshness_basis")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return value.strip() or "Needs re-validation"


class ConfidenceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: int = Field(ge=1, le=11)
    confidence: ConfidenceLevel
    basis: str


class HandoffPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    schema_version: str = "handoff-forge-v1"
    project_id: str
    project_name: str
    purpose: str
    mode: HandoffMode
    profile: TemplateProfile
    created_at: datetime = Field(default_factory=utc_now)
    sources: list[SourceArtifact] = Field(default_factory=list)
    inventory: list[InventoryItem] = Field(default_factory=list)
    sections: list[HandoffSection]
    confidence_assessments: list[ConfidenceAssessment]
    routes: dict[int, ModelRoute] = Field(default_factory=dict)
    scheduled: bool = False
    next_run_mode: Literal["CONTINUATION_REQUIRED", "INVENTORY_REFRESH_REQUIRED"] | None = None
    unverified_boundaries: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_section_contract(self) -> HandoffPackage:
        ids = [section.id for section in self.sections]
        if ids != list(range(1, 13)):
            raise ValueError("handoff sections must contain IDs 1 through 12 in order")
        assessment_ids = [item.section_id for item in self.confidence_assessments]
        if assessment_ids != list(range(1, 12)):
            raise ValueError("confidence assessments must contain Sections 1 through 11 in order")
        if self.scheduled and self.next_run_mode is None:
            raise ValueError("scheduled handoffs require next_run_mode")
        return self


class HandoffValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: TemplateProfile
    section_ids: tuple[int, ...]
    warnings: list[str] = Field(default_factory=list)
    valid: bool = True


class ConflictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    section_id: int
    summary: str
    variants: list[str]
    source_refs: list[str]
    resolution: str
    status: Literal["resolved", "review_required"]


class PlanTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    priority: Literal["P0", "P1", "P2", "P3", "P4"]
    status: Literal["pending", "blocked", "in_progress", "complete"] = "pending"
    description: str
    acceptance_criteria: list[str]
    dependencies: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class PreservedConstraint(BaseModel):
    """A security, policy, or Do Not Touch statement that merge may not discard."""

    model_config = ConfigDict(extra="forbid")

    text: str
    source_refs: list[str]
    reason: str


class MergedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_hashes: list[str]
    package: HandoffPackage
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    tasks: list[PlanTask] = Field(default_factory=list)
    preserved_constraints: list[PreservedConstraint] = Field(default_factory=list)
    content_hash: str


class GenerationJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    mode: HandoffMode
    profile: TemplateProfile
    status: JobStatus = JobStatus.PENDING
    route_matrix: dict[int, ModelRoute]
    completed_sections: list[HandoffSection] = Field(default_factory=list)
    inventory: list[InventoryItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    error: str | None = None
    output_path: Path | None = None

    @property
    def completed_section_ids(self) -> list[int]:
        return [section.id for section in self.completed_sections]
