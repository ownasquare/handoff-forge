"""Order-independent handoff merge with stable source citations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from handoff_forge.errors import MergeError
from handoff_forge.handoffs.catalog import HANDOFF_SECTION_SPECS, SECTION_BY_ID
from handoff_forge.handoffs.parser import parse_handoff_file
from handoff_forge.handoffs.validator import validate_handoff
from handoff_forge.merge.conflicts import (
    ProvenanceStatement,
    detect_conflicts,
    split_statements,
    statement_key,
)
from handoff_forge.merge.planner import derive_plan_tasks
from handoff_forge.models import (
    ArtifactKind,
    ConfidenceAssessment,
    ConfidenceLevel,
    HandoffMode,
    HandoffPackage,
    HandoffSection,
    InventoryItem,
    MergedPlan,
    PreservedConstraint,
    SourceArtifact,
    TemplateProfile,
)


@dataclass(frozen=True, slots=True)
class _Source:
    content_hash: str
    package: HandoffPackage
    label: str = ""


class MergeEngine:
    def merge_files(
        self,
        paths: Sequence[Path],
        *,
        target_profile: TemplateProfile = TemplateProfile.GOAL_V1,
        repair_mode: Literal["strict", "allow-unrelated"] = "strict",
    ) -> MergedPlan:
        packages = []
        for path in paths:
            raw_path = path.expanduser()
            if raw_path.is_symlink():
                raise MergeError(f"merge input cannot be a symlink: {raw_path.name}")
            resolved = raw_path.resolve(strict=True)
            if not resolved.is_file():
                raise MergeError(f"merge input is not a regular file: {resolved.name}")
            parsed = parse_handoff_file(resolved)
            validate_handoff(parsed.raw_text, parsed.inferred_profile)
            package = parsed.to_package()
            payload = resolved.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            kind = (
                ArtifactKind.MDC if resolved.suffix.casefold() == ".mdc" else ArtifactKind.MARKDOWN
            )
            merge_source = SourceArtifact(
                id=f"merge_{digest[:24]}",
                project_id=package.project_id,
                display_name=resolved.name,
                sha256=digest,
                media_type="text/markdown",
                size_bytes=len(payload),
                kind=kind,
                stored_path=resolved,
                file_uri=resolved.as_uri(),
                metadata={"role": "merge-input", "untrusted_evidence": True},
            )
            existing_sources = {(source.sha256, source.id): source for source in package.sources}
            existing_sources[(merge_source.sha256, merge_source.id)] = merge_source
            packages.append(
                package.model_copy(
                    update={"sources": [existing_sources[key] for key in sorted(existing_sources)]}
                )
            )
        return self.merge(
            packages,
            target_profile=target_profile,
            repair_mode=repair_mode,
        )

    def merge(
        self,
        packages: Sequence[HandoffPackage],
        *,
        target_profile: TemplateProfile = TemplateProfile.GOAL_V1,
        repair_mode: Literal["strict", "allow-unrelated"] = "strict",
    ) -> MergedPlan:
        if repair_mode not in {"strict", "allow-unrelated"}:
            raise MergeError(f"invalid repair mode: {repair_mode}")
        raw_sources = [
            _Source(content_hash=_semantic_hash(package), package=package) for package in packages
        ]
        unique_hashes = {source.content_hash for source in raw_sources}
        if len(unique_hashes) < 2:
            raise MergeError("merge requires at least two unique handoffs")
        if len(unique_hashes) != len(raw_sources):
            raise MergeError("merge requires two unique handoffs; duplicate content was supplied")
        project_ids = {source.package.project_id for source in raw_sources}
        if len(project_ids) > 1 and repair_mode == "strict":
            raise MergeError("strict merge rejected unrelated projects")
        ordered_sources = [
            _Source(source.content_hash, source.package, f"S{index}")
            for index, source in enumerate(
                sorted(raw_sources, key=lambda item: item.content_hash),
                start=1,
            )
        ]
        all_statements: list[ProvenanceStatement] = []
        by_section: dict[int, list[ProvenanceStatement]] = {
            section_id: [] for section_id in range(1, 13)
        }
        for source in ordered_sources:
            for section in source.package.sections:
                statements = split_statements(
                    section.content,
                    section_id=section.id,
                    source_ref=f"{source.label}#{section.id}",
                    confidence=section.confidence,
                )
                all_statements.extend(statements)
                by_section[section.id].extend(statements)
        conflicts = detect_conflicts(all_statements)
        constraints = _preserved_constraints(by_section[8])
        sections: list[HandoffSection] = []
        assessments: list[ConfidenceAssessment] = []
        for spec in HANDOFF_SECTION_SPECS[:11]:
            section_conflicts = [item for item in conflicts if item.section_id == spec.id]
            content = _merge_section(by_section[spec.id])
            if spec.id == 8 and constraints:
                content += "\n\n### Preserved constraints\n" + "\n".join(
                    f"- {item.text} [{' '.join(item.source_refs)}]" for item in constraints
                )
            confidence, basis = _merged_confidence(by_section[spec.id], section_conflicts)
            sections.append(
                HandoffSection(
                    id=spec.id,
                    title=spec.title,
                    content=content or spec.empty_value,
                    confidence=confidence,
                    freshness_basis=basis,
                )
            )
            assessments.append(
                ConfidenceAssessment(
                    section_id=spec.id,
                    confidence=confidence,
                    basis=basis,
                )
            )
        sections.append(
            HandoffSection(
                id=12,
                title=SECTION_BY_ID[12].title,
                content="Merged confidence is assessed conservatively across all source handoffs.",
                confidence=ConfidenceLevel.LOW,
                freshness_basis="Derived from merged Sections 1 through 11.",
            )
        )
        inventory = _merge_inventory(ordered_sources)
        project_name = ordered_sources[0].package.project_name
        project_id = (
            ordered_sources[0].package.project_id
            if len(project_ids) == 1
            else f"merged-{hashlib.sha256('|'.join(sorted(project_ids)).encode()).hexdigest()[:12]}"
        )
        source_hashes = [source.content_hash for source in ordered_sources]
        mode = (
            HandoffMode.PRE_COMPACT
            if target_profile is TemplateProfile.CODEX_PRECOMPACT_V1
            else HandoffMode.POST_TASK
        )
        created_at = max(
            (source.package.created_at for source in ordered_sources), default=datetime.now(UTC)
        )
        merged_package = HandoffPackage(
            id=f"merged-{'-'.join(digest[:6] for digest in source_hashes)}",
            project_id=project_id,
            project_name=project_name,
            purpose=f"Unified continuation plan merged from {len(ordered_sources)} handoffs.",
            mode=mode,
            profile=target_profile,
            created_at=created_at,
            sources=_merge_artifacts(ordered_sources),
            inventory=inventory,
            sections=sections,
            confidence_assessments=assessments,
            scheduled=any(source.package.scheduled for source in ordered_sources),
            next_run_mode=(
                "CONTINUATION_REQUIRED"
                if any(source.package.scheduled for source in ordered_sources)
                else None
            ),
            unverified_boundaries=sorted(
                {
                    boundary
                    for source in ordered_sources
                    for boundary in source.package.unverified_boundaries
                }
                | (
                    {"Unresolved merge conflicts require review."}
                    if any(item.status == "review_required" for item in conflicts)
                    else set()
                )
            ),
        )
        section_ten_items = [(statement.text, statement.source_ref) for statement in by_section[10]]
        inventory_items = [
            (item, source_ref)
            for source in ordered_sources
            for item in source.package.inventory
            for source_ref in [source.label]
        ]
        tasks = derive_plan_tasks(section_ten_items, inventory_items)
        payload = {
            "source_hashes": source_hashes,
            "package": merged_package.model_dump(mode="json"),
            "conflicts": [item.model_dump(mode="json") for item in conflicts],
            "tasks": [item.model_dump(mode="json") for item in tasks],
            "constraints": [item.model_dump(mode="json") for item in constraints],
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return MergedPlan(
            id=f"plan-{content_hash[:16]}",
            source_hashes=source_hashes,
            package=merged_package,
            conflicts=conflicts,
            tasks=tasks,
            preserved_constraints=constraints,
            content_hash=content_hash,
        )


def _semantic_hash(package: HandoffPackage) -> str:
    payload = {
        "project_id": package.project_id,
        "project_name": package.project_name,
        "purpose": package.purpose,
        "mode": package.mode.value,
        "inventory": [item.model_dump(mode="json") for item in package.inventory],
        "sections": [
            {
                "id": section.id,
                "title": section.title,
                "content": section.content,
                "confidence": section.confidence.value,
                "freshness_basis": section.freshness_basis,
            }
            for section in package.sections
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _merge_section(statements: Sequence[ProvenanceStatement]) -> str:
    by_key: dict[str, tuple[str, set[str]]] = {}
    for statement in statements:
        key = statement_key(statement.text)
        if key not in by_key:
            by_key[key] = (statement.text, {statement.source_ref})
        else:
            text, refs = by_key[key]
            refs.add(statement.source_ref)
            by_key[key] = (text, refs)
    rows = [
        f"- {text} " + " ".join(f"[{ref}]" for ref in sorted(refs))
        for _key, (text, refs) in sorted(by_key.items())
    ]
    return "\n".join(rows)


def _preserved_constraints(statements: Sequence[ProvenanceStatement]) -> list[PreservedConstraint]:
    markers = ("do not touch", "must not", "never ", "preserve", "security", "policy")
    grouped: dict[str, tuple[str, set[str]]] = {}
    for statement in statements:
        if not any(marker in statement.text.casefold() for marker in markers):
            continue
        key = statement_key(statement.text)
        text, refs = grouped.get(key, (statement.text, set()))
        refs.add(statement.source_ref)
        grouped[key] = (text, refs)
    return [
        PreservedConstraint(
            text=text,
            source_refs=sorted(refs),
            reason=(
                "Security, policy, or explicit preservation constraints survive freshness ranking."
            ),
        )
        for _key, (text, refs) in sorted(grouped.items())
    ]


def _merged_confidence(
    statements: Sequence[ProvenanceStatement],
    conflicts: Sequence[object],
) -> tuple[ConfidenceLevel, str]:
    if not statements:
        return ConfidenceLevel.LOW, "Merged evidence is missing and needs re-validation."
    if any(getattr(item, "status", None) == "review_required" for item in conflicts):
        return ConfidenceLevel.LOW, "Equal-confidence source conflict needs re-validation."
    levels = {statement.confidence for statement in statements}
    if levels == {ConfidenceLevel.HIGH}:
        return (
            ConfidenceLevel.HIGH,
            "All contributing statements were recently verified in the current session.",
        )
    if ConfidenceLevel.LOW not in levels:
        return ConfidenceLevel.MEDIUM, "Verified source evidence is solid but older or mixed."
    return ConfidenceLevel.LOW, "At least one contributing source needs re-validation."


def _merge_inventory(sources: Sequence[_Source]) -> list[InventoryItem]:
    items: dict[str, InventoryItem] = {}
    for source in sources:
        for item in source.package.inventory:
            key = re.sub(r"[^a-z0-9]+", " ", item.what.casefold()).strip()
            existing = items.get(key)
            refs = sorted(set(item.source_refs) | {source.label})
            if existing is None:
                items[key] = item.model_copy(update={"source_refs": refs})
            else:
                items[key] = existing.model_copy(
                    update={"source_refs": sorted(set(existing.source_refs) | set(refs))}
                )
    return [items[key] for key in sorted(items)]


def _merge_artifacts(sources: Sequence[_Source]) -> list[SourceArtifact]:
    artifacts: dict[tuple[str, str], SourceArtifact] = {}
    for source in sources:
        for artifact in source.package.sources:
            artifacts[(artifact.sha256, artifact.id)] = artifact
    return [artifacts[key] for key in sorted(artifacts)]
