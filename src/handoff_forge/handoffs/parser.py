"""Tolerant Markdown/MDC parser for canonical handoff packages."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import yaml  # type: ignore[import-untyped]

from handoff_forge.handoffs.catalog import SECTION_BY_ID, section_id_for_title
from handoff_forge.models import (
    ConfidenceAssessment,
    ConfidenceLevel,
    HandoffMode,
    HandoffPackage,
    HandoffSection,
    InventoryItem,
    TemplateProfile,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NUMBERED_TITLE_RE = re.compile(r"^(?:(\d{1,2})[.)]\s*)?(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_CONFIDENCE_RE = re.compile(
    r"^\s*[-*]\s*Section\s+(\d{1,2})\s*[—:-]\s*"
    r"(?:(✅|⚠️|❓)\s*)?(High|Medium|Low)\b(?:\s*[—-]\s*(.*))?$",
    re.IGNORECASE,
)
_CONFIDENCE_ASSESSMENTS_HEADING = "### Section assessments"


@dataclass(frozen=True, slots=True)
class ParsedSection:
    id: int
    title: str
    content: str
    declared_id: int | None
    heading_line: int


@dataclass(frozen=True, slots=True)
class ParsedConfidenceLine:
    section_id: int
    confidence: ConfidenceLevel
    basis: str
    emoji: str | None


@dataclass(frozen=True, slots=True)
class ParsedHandoff:
    raw_text: str
    frontmatter: dict[str, Any]
    preamble: str
    inventory_text: str | None
    sections: tuple[ParsedSection, ...]
    unified_execution_plan: str | None
    inferred_profile: TemplateProfile

    def to_package(
        self,
        *,
        profile: TemplateProfile | None = None,
        mode: HandoffMode | None = None,
    ) -> HandoffPackage:
        """Normalize a valid parsed handoff into the provider-neutral package model."""

        selected_profile = profile or self.inferred_profile
        selected_mode = mode or _mode_for_profile(selected_profile)
        confidence_lines = {item.section_id: item for item in parse_confidence_lines(self)}
        sections: list[HandoffSection] = []
        for parsed in self.sections:
            assessment = confidence_lines.get(parsed.id)
            confidence = assessment.confidence if assessment else ConfidenceLevel.LOW
            basis = assessment.basis if assessment else "Evidence needs re-validation."
            if parsed.id == 12:
                confidence = ConfidenceLevel.LOW
                basis = "Derived assessment for Sections 1 through 11."
            sections.append(
                HandoffSection(
                    id=parsed.id,
                    title=SECTION_BY_ID[parsed.id].title,
                    content=parsed.content,
                    confidence=confidence,
                    freshness_basis=basis,
                )
            )
        assessments = [
            ConfidenceAssessment(
                section_id=section_id,
                confidence=(
                    confidence_lines[section_id].confidence
                    if section_id in confidence_lines
                    else ConfidenceLevel.LOW
                ),
                basis=(
                    confidence_lines[section_id].basis
                    if section_id in confidence_lines
                    else "Evidence needs re-validation."
                ),
            )
            for section_id in range(1, 12)
        ]
        project_name = _project_name(self)
        digest = hashlib.sha256(self.raw_text.encode("utf-8")).hexdigest()
        created_at = _created_at(self.frontmatter)
        return HandoffPackage(
            id=f"imported-{digest[:16]}",
            project_id=_slug(project_name),
            project_name=project_name,
            purpose=str(
                self.frontmatter.get(
                    "description",
                    "Imported handoff continuation package.",
                )
            ),
            mode=selected_mode,
            profile=selected_profile,
            created_at=created_at,
            inventory=_parse_inventory(self.inventory_text),
            sections=sections,
            confidence_assessments=assessments,
        )


def parse_handoff(text: str) -> ParsedHandoff:
    """Parse handoff structure while ignoring section-like content inside code fences."""

    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    frontmatter, body_start = _frontmatter(lines)
    sections: list[ParsedSection] = []
    preamble: list[str] = []
    inventory: list[str] | None = None
    unified: list[str] | None = None
    current_id: int | None = None
    current_title = ""
    current_declared: int | None = None
    current_line = 0
    current_content: list[str] = []
    fenced_with: str | None = None

    def finish_section() -> None:
        nonlocal current_id, current_title, current_declared, current_line, current_content
        if current_id is None:
            return
        sections.append(
            ParsedSection(
                id=current_id,
                title=current_title,
                content="\n".join(current_content).strip() or SECTION_BY_ID[current_id].empty_value,
                declared_id=current_declared,
                heading_line=current_line,
            )
        )
        current_id = None
        current_title = ""
        current_declared = None
        current_line = 0
        current_content = []

    for index, line in enumerate(lines[body_start:], start=body_start + 1):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if fenced_with is None:
                fenced_with = marker
            elif fenced_with == marker:
                fenced_with = None
        heading = _HEADING_RE.match(line) if fenced_with is None else None
        recognized_id: int | None = None
        declared_id: int | None = None
        title = ""
        special: str | None = None
        if heading:
            raw_title = heading.group(2).strip()
            normalized_title = re.sub(r"\s+", " ", raw_title).strip().casefold()
            if normalized_title == "inventory next items":
                special = "inventory"
            elif normalized_title == "unified execution plan":
                special = "unified"
            else:
                title_match = _NUMBERED_TITLE_RE.match(raw_title)
                if title_match:
                    declared_id = int(title_match.group(1)) if title_match.group(1) else None
                    title = title_match.group(2).strip()
                    inferred_id = section_id_for_title(title)
                    if declared_id is not None and 1 <= declared_id <= 12:
                        recognized_id = declared_id
                    elif declared_id is None:
                        recognized_id = inferred_id
        if special is not None:
            finish_section()
            if special == "inventory":
                inventory = []
                unified = None
            else:
                unified = []
                inventory = inventory
            continue
        if recognized_id is not None:
            finish_section()
            current_id = recognized_id
            current_title = title
            current_declared = declared_id
            current_line = index
            unified = None
            continue
        if current_id is not None:
            current_content.append(line)
        elif unified is not None:
            unified.append(line)
        elif inventory is not None:
            inventory.append(line)
        else:
            preamble.append(line)
    finish_section()

    profile = _infer_profile(normalized, frontmatter, inventory is not None)
    return ParsedHandoff(
        raw_text=normalized,
        frontmatter=frontmatter,
        preamble="\n".join(preamble).strip(),
        inventory_text="\n".join(inventory).strip() if inventory is not None else None,
        sections=tuple(sections),
        unified_execution_plan="\n".join(unified).strip() if unified is not None else None,
        inferred_profile=profile,
    )


def parse_handoff_file(path: Path) -> ParsedHandoff:
    return parse_handoff(path.read_text(encoding="utf-8-sig"))


def parse_confidence_lines(parsed: ParsedHandoff) -> tuple[ParsedConfidenceLine, ...]:
    section = next((item for item in parsed.sections if item.id == 12), None)
    if section is None:
        return ()
    lines = section.content.splitlines()
    assessment_starts = [
        index for index, line in enumerate(lines) if line.strip() == _CONFIDENCE_ASSESSMENTS_HEADING
    ]
    if not assessment_starts:
        return ()

    # Generated Section 12 prose is untrusted evidence and can itself contain
    # confidence-looking lines or copied assessment headings. The renderer owns
    # the final assessment subsection, so only parse content after its last
    # exact heading. This keeps retrieved handoffs from spoofing or duplicating
    # the package's conservative Section 1 through 11 assessments.
    found: list[ParsedConfidenceLine] = []
    for line in lines[assessment_starts[-1] + 1 :]:
        match = _CONFIDENCE_RE.match(line)
        if not match:
            continue
        found.append(
            ParsedConfidenceLine(
                section_id=int(match.group(1)),
                emoji=match.group(2),
                confidence=ConfidenceLevel(match.group(3).casefold()),
                basis=(match.group(4) or "Needs re-validation").strip(),
            )
        )
    return tuple(found)


def _frontmatter(lines: list[str]) -> tuple[dict[str, Any], int]:
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for index in range(1, len(lines)):
        if lines[index].strip() != "---":
            continue
        raw = "\n".join(lines[1:index])
        loaded = yaml.safe_load(raw) if raw.strip() else {}
        return (loaded if isinstance(loaded, dict) else {}), index + 1
    return {}, 0


def _infer_profile(
    text: str,
    frontmatter: dict[str, Any],
    has_inventory: bool,
) -> TemplateProfile:
    if has_inventory or "alwaysApply" in frontmatter:
        return TemplateProfile.CODEX_POST_CHAT_V1
    if "in-progress context snapshot" in text.casefold():
        return TemplateProfile.CODEX_PRECOMPACT_V1
    return TemplateProfile.GOAL_V1


def _mode_for_profile(profile: TemplateProfile) -> HandoffMode:
    if profile is TemplateProfile.CODEX_PRECOMPACT_V1:
        return HandoffMode.PRE_COMPACT
    return HandoffMode.POST_TASK


def _project_name(parsed: ParsedHandoff) -> str:
    configured = parsed.frontmatter.get("project") or parsed.frontmatter.get("projectName")
    if configured:
        return str(configured).strip()
    match = re.search(
        r"^#\s+(.+?)(?:\s+Handoff|\s+Continuation Package)?\s*$", parsed.preamble, re.M
    )
    return match.group(1).strip() if match else "Imported Handoff"


def _created_at(frontmatter: dict[str, Any]) -> datetime:
    raw = frontmatter.get("generatedAt") or frontmatter.get("createdAt")
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime(1970, 1, 1, tzinfo=UTC)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "imported-handoff"


def _parse_inventory(text: str | None) -> list[InventoryItem]:
    if not text or "no new items found" in text.casefold():
        return []
    heading_re = re.compile(r"^###\s+([^:\n]+):\s*(.+?)\s*$", re.MULTILINE)
    matches = list(heading_re.finditer(text))
    items: list[InventoryItem] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        scalars: dict[str, str] = {}
        lists: dict[str, list[str]] = {}
        active_list: str | None = None
        for line in body.splitlines():
            field = re.match(r"^- \*\*(.+?):\*\*(?:\s*(.*))?$", line.strip())
            if field:
                label = field.group(1).strip().casefold()
                value = (field.group(2) or "").strip()
                if value:
                    scalars[label] = value
                    active_list = None
                else:
                    lists[label] = []
                    active_list = label
                continue
            nested = re.match(r"^\s{2,}-\s+(.+)$", line)
            if nested and active_list:
                lists[active_list].append(nested.group(1).strip())
        priority_value = scalars.get("priority", "P2").upper()
        allowed_priorities = {"P0", "P1", "P2", "P3", "P4"}
        priority = cast(
            Literal["P0", "P1", "P2", "P3", "P4"],
            priority_value if priority_value in allowed_priorities else "P2",
        )

        items.append(
            InventoryItem(
                id=match.group(1).strip(),
                who=scalars.get("who", "Unknown"),
                what=scalars.get("what", match.group(2).strip()),
                how_discovered=scalars.get("how discovered", "Needs re-validation"),
                where=scalars.get("where", "Unknown"),
                when=scalars.get("when", "Unknown"),
                description=scalars.get("detailed description", "Needs re-validation"),
                acceptance_criteria=_inventory_values(lists, "acceptance criteria"),
                definition_of_done=_inventory_values(lists, "definition of done"),
                root_cause=scalars.get("root cause", "Needs re-validation"),
                priority=priority,
                priority_rationale=scalars.get("priority rationale", "Needs re-validation"),
                regression_prevention=_inventory_values(lists, "regression prevention"),
                testing=_inventory_values(lists, "testing"),
                audit_policies=_inventory_values(lists, "audit policies"),
                adjacent_considerations=_inventory_values(lists, "other considerations"),
                source_refs=[
                    value for value in lists.get("source references", []) if value != "Unknown"
                ],
            )
        )
    return items


def _inventory_values(values: dict[str, list[str]], label: str) -> list[str]:
    return values.get(label, []) or ["Needs re-validation"]
