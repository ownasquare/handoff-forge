"""Deterministic renderers for the three versioned handoff profiles."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable

from handoff_forge.errors import HandoffValidationError
from handoff_forge.handoffs.catalog import SECTION_BY_ID
from handoff_forge.handoffs.inventory import inventory_scan_evidence
from handoff_forge.models import (
    ConfidenceAssessment,
    ConfidenceLevel,
    HandoffMode,
    HandoffPackage,
    InventoryItem,
    ModelRoute,
    TemplateProfile,
)

_PLAIN_CONFIDENCE = {
    ConfidenceLevel.HIGH: "High",
    ConfidenceLevel.MEDIUM: "Medium",
    ConfidenceLevel.LOW: "Low",
}
_EMOJI_CONFIDENCE = {
    ConfidenceLevel.HIGH: "✅ High",
    ConfidenceLevel.MEDIUM: "⚠️ Medium",
    ConfidenceLevel.LOW: "❓ Low",
}


def render_handoff(
    package: HandoffPackage,
    profile: TemplateProfile | None = None,
) -> str:
    """Render a canonical package without dropping empty or unknown fields."""

    selected = profile or package.profile
    _validate_mode(package, selected)
    lines: list[str] = []
    if selected is TemplateProfile.CODEX_POST_CHAT_V1:
        lines.extend(
            [
                "---",
                f"description: {json.dumps(package.purpose, ensure_ascii=False)}",
                "alwaysApply: false",
                f"schemaVersion: {package.schema_version}",
                f"project: {json.dumps(package.project_name, ensure_ascii=False)}",
                f"mode: {package.mode.value}",
                f"generatedAt: {package.created_at.isoformat()}",
                "---",
                "",
            ]
        )
    lines.append(f"# {package.project_name} Handoff")
    lines.append("")
    if selected is TemplateProfile.CODEX_PRECOMPACT_V1:
        lines.extend(
            [
                "> **Pre-compact snapshot:** This is an in-progress context snapshot, "
                "not a completion claim.",
                f"> Expected filename: `{handoff_filename(package, selected)}`",
                "",
            ]
        )
    elif selected is TemplateProfile.CODEX_POST_CHAT_V1:
        lines.extend(
            [
                "## INVENTORY NEXT ITEMS",
                "",
                *_render_inventory(package),
                "",
            ]
        )

    assessments = {item.section_id: item for item in package.confidence_assessments}
    for section in package.sections:
        lines.append(f"## {section.id}. {SECTION_BY_ID[section.id].title}")
        lines.append("")
        route = package.routes.get(section.id)
        if route is not None:
            lines.extend([_render_route(route), ""])
        content = section.content.strip() or SECTION_BY_ID[section.id].empty_value
        if section.id == 10 and package.scheduled:
            next_mode = package.next_run_mode or "CONTINUATION_REQUIRED"
            content = f"Next run mode: {next_mode}\n\n{content}"
        lines.append(content)
        if section.id == 12:
            lines.extend(["", "### Section assessments", ""])
            for section_id in range(1, 12):
                assessment = assessments[section_id]
                label = _confidence_label(assessment, selected)
                separator = " — " if selected is TemplateProfile.CODEX_POST_CHAT_V1 else " - "
                lines.append(
                    f"- Section {section_id} — {label}{separator}{assessment.basis.strip()}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def handoff_filename(
    package: HandoffPackage,
    profile: TemplateProfile | None = None,
) -> str:
    selected = profile or package.profile
    date = package.created_at.date().isoformat()
    slug = _slug(package.project_name)
    if selected is TemplateProfile.CODEX_PRECOMPACT_V1:
        return f"{date}-codex-{slug}.precompact.handoff.mdc"
    if selected is TemplateProfile.CODEX_POST_CHAT_V1:
        return f"{date}-codex-{slug}.handoff.mdc"
    return f"{date}-goal-{slug}.handoff.mdc"


def _render_inventory(package: HandoffPackage) -> list[str]:
    scanned = inventory_scan_evidence(package.unverified_boundaries)
    if not package.inventory:
        if scanned:
            return [
                "- **No new items found.**",
                "- **Evidence scanned:**",
                *[f"  - {_safe_inline_text(value)}" for value in scanned],
            ]
        return [
            "- **Inventory scan incomplete.**",
            "- **Evidence scanned:**",
            "  - None recorded.",
        ]
    lines: list[str] = []
    if scanned:
        lines.extend(
            [
                "- **Inventory scan completed.**",
                "- **Evidence scanned:**",
                *[f"  - {_safe_inline_text(value)}" for value in scanned],
                "",
            ]
        )
    for item in package.inventory:
        lines.extend(_render_inventory_item(item))
    return lines


def _render_inventory_item(item: InventoryItem) -> list[str]:
    lines = [
        f"### {item.id}: {item.what}",
        "",
        f"- **Who:** {item.who}",
        f"- **What:** {item.what}",
        f"- **How discovered:** {item.how_discovered}",
        f"- **Where:** {item.where}",
        f"- **When:** {item.when}",
        f"- **Detailed description:** {item.description}",
        f"- **Root cause:** {item.root_cause}",
        f"- **Priority:** {item.priority}",
        f"- **Priority rationale:** {item.priority_rationale}",
    ]
    lines.extend(_render_list_field("Acceptance criteria", item.acceptance_criteria))
    lines.extend(_render_list_field("Definition of done", item.definition_of_done))
    lines.extend(_render_list_field("Regression prevention", item.regression_prevention))
    lines.extend(_render_list_field("Testing", item.testing))
    lines.extend(_render_list_field("Audit policies", item.audit_policies))
    lines.extend(_render_list_field("Other considerations", item.adjacent_considerations))
    lines.extend(_render_list_field("Source references", item.source_refs or ["Unknown"]))
    lines.append("")
    return lines


def _render_list_field(label: str, values: Iterable[str]) -> list[str]:
    normalized = [value.strip() for value in values if value.strip()] or ["Unknown"]
    return [f"- **{label}:**", *(f"  - {value}" for value in normalized)]


def _render_route(route: ModelRoute) -> str:
    provider = _safe_route_identifier(route.provider)
    model = _safe_route_identifier(route.model)
    consent = "allowed" if route.allow_cloud_upload else "not granted"
    visual_evidence = "operator-confirmed" if route.include_visual_evidence else "disabled"
    return (
        f"- **Generation route:** provider: {provider}; model: {model}; "
        f"cloud upload consent: {consent}; visual file inclusion: {visual_evidence}."
    )


_ROUTE_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret)\s*[=:]\s*\S+"),
)


def _safe_route_identifier(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    if any(pattern.search(normalized) for pattern in _ROUTE_SECRET_PATTERNS):
        return "[REDACTED]"
    normalized = normalized[:160].replace("`", "\N{MODIFIER LETTER GRAVE ACCENT}")
    return html.escape(normalized or "unknown", quote=True)


def _safe_inline_text(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    return html.escape(normalized, quote=True)


def _confidence_label(
    assessment: ConfidenceAssessment,
    profile: TemplateProfile,
) -> str:
    if profile is TemplateProfile.CODEX_POST_CHAT_V1:
        return _EMOJI_CONFIDENCE[assessment.confidence]
    return _PLAIN_CONFIDENCE[assessment.confidence]


def _validate_mode(package: HandoffPackage, profile: TemplateProfile) -> None:
    if (
        profile is TemplateProfile.CODEX_PRECOMPACT_V1
        and package.mode is not HandoffMode.PRE_COMPACT
    ):
        raise HandoffValidationError("precompact profile requires pre-compact mode")
    if profile is TemplateProfile.CODEX_POST_CHAT_V1 and package.mode is not HandoffMode.POST_TASK:
        raise HandoffValidationError("post-chat profile requires post-task mode")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "handoff"
