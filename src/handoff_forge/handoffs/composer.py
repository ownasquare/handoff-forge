"""Deterministic offline composition over canonical evidence blocks."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence, Set
from datetime import UTC, datetime

from handoff_forge.handoffs.catalog import HANDOFF_SECTION_SPECS, SECTION_BY_ID
from handoff_forge.handoffs.confidence import (
    EvidenceObservation,
    assess_section_confidence,
    explicit_verification_source_ids,
)
from handoff_forge.models import (
    ConfidenceAssessment,
    ConfidenceLevel,
    ContentBlock,
    EvidenceRef,
    HandoffMode,
    HandoffPackage,
    HandoffSection,
    InventoryItem,
    ModelRoute,
    SourceArtifact,
    TemplateProfile,
)


class OfflineHandoffComposer:
    """Compose all twelve sections without network or provider credentials."""

    def __init__(
        self,
        *,
        project_id: str,
        project_name: str,
        purpose: str,
        created_at: datetime | None = None,
    ) -> None:
        self.project_id = project_id
        self.project_name = project_name
        self.purpose = purpose
        self.created_at = created_at or datetime.now(UTC)

    def compose(
        self,
        *,
        mode: HandoffMode,
        profile: TemplateProfile,
        sources: Sequence[SourceArtifact] = (),
        evidence_by_section: Mapping[int, Sequence[ContentBlock]] | None = None,
        inventory: Sequence[InventoryItem] = (),
        routes: Mapping[int, ModelRoute] | None = None,
        current_session_source_ids: Set[str] | None = None,
        verified_source_ids: Set[str] | None = None,
        generated_content: Mapping[int, str] | None = None,
        scheduled: bool = False,
        next_run_mode: str | None = None,
    ) -> HandoffPackage:
        evidence_map = evidence_by_section or {}
        sections: list[HandoffSection] = []
        for spec in HANDOFF_SECTION_SPECS[:11]:
            selected, omitted = _select_evidence(
                evidence_map.get(spec.id, ()), spec.evidence_char_budget
            )
            metadata_verified, metadata_current, revalidation_ids = (
                explicit_verification_source_ids(selected)
            )
            current_ids = (
                current_session_source_ids
                if current_session_source_ids is not None
                else metadata_current
            )
            verified_ids = (
                verified_source_ids if verified_source_ids is not None else metadata_verified
            )
            content = (
                generated_content[spec.id].strip()
                if generated_content and spec.id in generated_content
                else _offline_section_content(spec.id, selected, omitted)
            )
            sections.append(
                self.section_from_text(
                    section_id=spec.id,
                    text=content,
                    evidence=selected,
                    current_session_source_ids=current_ids,
                    verified_source_ids=verified_ids,
                    needs_revalidation_source_ids=revalidation_ids,
                    sources=sources,
                )
            )
        assessments = [
            ConfidenceAssessment(
                section_id=section.id,
                confidence=section.confidence,
                basis=section.freshness_basis,
            )
            for section in sections
        ]
        section_twelve_text = (
            generated_content[12].strip()
            if generated_content and 12 in generated_content
            else "Assessment covers Sections 1 through 11 only."
        )
        sections.append(
            HandoffSection(
                id=12,
                title=SECTION_BY_ID[12].title,
                content=section_twelve_text,
                confidence=ConfidenceLevel.LOW,
                freshness_basis="Derived from the Section 1 through 11 evidence assessments.",
            )
        )
        package_id = _package_id(
            self.project_id,
            mode,
            profile,
            [section.content for section in sections],
        )
        return HandoffPackage(
            id=package_id,
            project_id=self.project_id,
            project_name=self.project_name,
            purpose=self.purpose,
            mode=mode,
            profile=profile,
            created_at=self.created_at,
            sources=list(sources),
            inventory=list(inventory),
            sections=sections,
            confidence_assessments=assessments,
            routes=dict(routes or {}),
            scheduled=scheduled,
            next_run_mode=next_run_mode,
        )

    def section_from_text(
        self,
        *,
        section_id: int,
        text: str,
        evidence: Sequence[ContentBlock],
        current_session_source_ids: Set[str],
        verified_source_ids: Set[str],
        needs_revalidation_source_ids: Set[str] | None = None,
        sources: Sequence[SourceArtifact] = (),
    ) -> HandoffSection:
        if section_id == 12:
            return HandoffSection(
                id=12,
                title=SECTION_BY_ID[12].title,
                content=text,
                confidence=ConfidenceLevel.LOW,
                freshness_basis="Derived from Sections 1 through 11.",
            )
        source_dates = {source.id: source.created_at for source in sources}
        observations = [
            EvidenceObservation(
                source_id=block.artifact_id,
                verified=block.artifact_id in verified_source_ids,
                current_session=block.artifact_id in current_session_source_ids,
                observed_at=source_dates.get(block.artifact_id),
                needs_revalidation=(block.artifact_id in (needs_revalidation_source_ids or set())),
            )
            for block in evidence
        ]
        assessment = assess_section_confidence(
            section_id,
            observations,
            now=self.created_at,
        )
        display_names = {source.id: source.display_name for source in sources}
        refs = [
            EvidenceRef(
                source_id=block.artifact_id,
                artifact_sha256=block.artifact_sha256,
                display_name=display_names.get(block.artifact_id, block.artifact_id),
                block_id=block.id,
                page_number=block.page_number,
                line_start=block.line_start,
                line_end=block.line_end,
                artifact_path=block.artifact_path,
            )
            for block in evidence
        ]
        return HandoffSection(
            id=section_id,
            title=SECTION_BY_ID[section_id].title,
            content=text.strip() or SECTION_BY_ID[section_id].empty_value,
            confidence=assessment.confidence,
            freshness_basis=assessment.basis,
            evidence=refs,
        )

    def package_from_sections(
        self,
        *,
        mode: HandoffMode,
        profile: TemplateProfile,
        sections: Sequence[HandoffSection],
        inventory: Sequence[InventoryItem] = (),
        sources: Sequence[SourceArtifact] = (),
        routes: Mapping[int, ModelRoute] | None = None,
    ) -> HandoffPackage:
        ordered = sorted(sections, key=lambda item: item.id)
        if [section.id for section in ordered] != list(range(1, 13)):
            raise ValueError("completed sections must contain IDs 1 through 12")
        assessments = [
            ConfidenceAssessment(
                section_id=section.id,
                confidence=section.confidence,
                basis=section.freshness_basis,
            )
            for section in ordered[:11]
        ]
        return HandoffPackage(
            id=_package_id(
                self.project_id,
                mode,
                profile,
                [section.content for section in ordered],
            ),
            project_id=self.project_id,
            project_name=self.project_name,
            purpose=self.purpose,
            mode=mode,
            profile=profile,
            created_at=self.created_at,
            sources=list(sources),
            inventory=list(inventory),
            sections=ordered,
            confidence_assessments=assessments,
            routes=dict(routes or {}),
        )


def _select_evidence(
    evidence: Sequence[ContentBlock],
    char_budget: int,
) -> tuple[list[ContentBlock], int]:
    ordered = sorted(evidence, key=lambda item: (item.order, item.artifact_id, item.id))
    selected: list[ContentBlock] = []
    used = 0
    for block in ordered:
        size = len(block.text)
        if selected and used + size > char_budget:
            continue
        selected.append(block)
        used += size
    return selected, len(ordered) - len(selected)


def _offline_section_content(
    section_id: int,
    evidence: Sequence[ContentBlock],
    omitted: int,
) -> str:
    if not evidence:
        return SECTION_BY_ID[section_id].empty_value
    lines = []
    for block in evidence:
        text = re.sub(r"\s+", " ", block.text).strip()
        lines.append(f"- {text} [{block.artifact_id}#{block.id}]")
    if omitted:
        lines.append(f"- {omitted} additional evidence block(s) omitted by the section budget.")
    return "\n".join(lines)


def _package_id(
    project_id: str,
    mode: HandoffMode,
    profile: TemplateProfile,
    contents: Sequence[str],
) -> str:
    payload = json.dumps(
        [project_id, mode.value, profile.value, list(contents)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"handoff-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
