"""Immutable canonical catalog for the twelve handoff sections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class HandoffSectionSpec:
    """The stable identity and evidence contract for one handoff section."""

    id: int
    title: str
    required_topics: tuple[str, ...]
    evidence_queries: tuple[str, ...]
    empty_value: str
    evidence_char_budget: int = 8_000


HANDOFF_SECTION_SPECS: tuple[HandoffSectionSpec, ...] = (
    HandoffSectionSpec(
        1,
        "Project Identity & Strategic Context",
        (
            "project name and core purpose",
            "primary objectives and measurable success criteria",
            "constraints, non-functional requirements, and design philosophy",
            "target environment, stack, tools, and dependencies",
        ),
        ("project purpose objectives success criteria constraints stack dependencies",),
        "Unknown",
    ),
    HandoffSectionSpec(
        2,
        "Current System State & Architecture Map",
        (
            "implemented, in-progress, broken, blocked, and deferred state",
            "end-to-end system flow and architecture",
            "key data structures, files, components, and naming conventions",
        ),
        ("current state architecture implemented blocked deferred components files",),
        "Needs re-validation",
    ),
    HandoffSectionSpec(
        3,
        "Critical Decisions & Reasoning History",
        (
            "major decisions, rationale, and tradeoffs",
            "alternatives rejected",
            "evolution of thinking, assumptions, and mental models",
        ),
        ("decision rationale tradeoff alternative rejected assumption reasoning",),
        "None known",
    ),
    HandoffSectionSpec(
        4,
        "Recent Work & Iteration Log (High Priority)",
        (
            "recent accomplishments and changes",
            "new insights, corrections, reversals, and patterns",
            "open threads from recent exchanges",
        ),
        ("recent work change iteration insight correction reversal open thread",),
        "Needs re-validation",
    ),
    HandoffSectionSpec(
        5,
        "Testing, Validation & Quality Framework",
        (
            "test status and coverage",
            "validation methods, benchmarks, edge cases, and stress tests",
            "manual verification and testing gaps",
        ),
        ("test validation coverage benchmark edge case manual proof gap",),
        "Needs re-validation",
    ),
    HandoffSectionSpec(
        6,
        "Debugging History & Failure Modes",
        (
            "bugs, root causes, and resolutions",
            "recurring errors, anti-patterns, and subtle failure modes",
            "effective debugging techniques and misleading successes",
        ),
        ("bug error failure root cause resolution debugging regression",),
        "None known",
    ),
    HandoffSectionSpec(
        7,
        "Established Processes & Effective Patterns",
        (
            "implementation, review, test, and validation workflows",
            "checklists, standards, prompting, and communication patterns",
        ),
        ("workflow process checklist standard prompt communication validation",),
        "Unknown",
    ),
    HandoffSectionSpec(
        8,
        "Risks, Technical Debt & Strict Preservation Rules",
        (
            "risks, debt, shortcuts, and uncertainty",
            "explicit Do Not Touch constraints",
            "common mistakes and prevention guidance",
        ),
        ("risk debt uncertainty shortcut do not touch preserve security policy",),
        "None known",
    ),
    HandoffSectionSpec(
        9,
        "Key Artifacts & References",
        (
            "code, configuration, schemas, and test data",
            "critical outputs, errors, logs, screenshots, and references",
        ),
        ("artifact reference path code configuration schema log screenshot output",),
        "Unknown",
    ),
    HandoffSectionSpec(
        10,
        "Next Steps & Prioritized Backlog",
        (
            "immediate next task and recommended approach",
            "prioritized follow-up work, blockers, dependencies, and sequence",
        ),
        ("next task backlog priority blocker dependency follow-up sequence",),
        "Needs re-validation",
    ),
    HandoffSectionSpec(
        11,
        "Continuation & Working Style Instructions",
        (
            "next-session approach",
            "required critical thinking, validation, skepticism, and consistency",
        ),
        ("continuation instruction working style rigor skepticism consistency",),
        "Unknown",
    ),
    HandoffSectionSpec(
        12,
        "Confidence & Freshness Assessment",
        ("confidence and freshness tags for Sections 1 through 11 only",),
        ("confidence freshness verified current session re-validation",),
        "Needs re-validation",
    ),
)

SECTION_BY_ID = MappingProxyType({spec.id: spec for spec in HANDOFF_SECTION_SPECS})
SECTION_ID_BY_TITLE = MappingProxyType(
    {re.sub(r"\s+", " ", spec.title).strip().casefold(): spec.id for spec in HANDOFF_SECTION_SPECS}
)
EXPECTED_SECTION_IDS = tuple(range(1, 13))


def normalize_heading_title(value: str) -> str:
    """Normalize harmless heading variation without weakening title identity."""

    value = re.sub(r"^\s*\d{1,2}[.)]\s*", "", value)
    value = value.strip().rstrip(":").strip()
    return re.sub(r"\s+", " ", value).casefold()


def section_id_for_title(value: str) -> int | None:
    """Return the canonical section ID for an exact normalized title."""

    return SECTION_ID_BY_TITLE.get(normalize_heading_title(value))
