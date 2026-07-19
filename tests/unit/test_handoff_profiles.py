from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from handoff_forge.errors import HandoffValidationError
from handoff_forge.handoffs.catalog import HANDOFF_SECTION_SPECS
from handoff_forge.handoffs.inventory import inventory_scan_marker
from handoff_forge.handoffs.parser import parse_confidence_lines, parse_handoff
from handoff_forge.handoffs.profiles import handoff_filename, render_handoff
from handoff_forge.handoffs.validator import validate_handoff
from handoff_forge.models import (
    ConfidenceAssessment,
    ConfidenceLevel,
    HandoffMode,
    HandoffPackage,
    HandoffSection,
    InventoryItem,
    ModelRoute,
    TemplateProfile,
)

ROOT = Path(__file__).parents[2]


def _package(
    profile: TemplateProfile = TemplateProfile.GOAL_V1,
    mode: HandoffMode = HandoffMode.POST_TASK,
) -> HandoffPackage:
    sections = [
        HandoffSection(
            id=spec.id,
            title=spec.title,
            content=(
                "Do Not Touch: preserve the append-only audit ledger."
                if spec.id == 8
                else "- Verify the current implementation and preserve its evidence."
            ),
            confidence=ConfidenceLevel.HIGH,
            freshness_basis="Recently verified in this session from current-session evidence.",
        )
        for spec in HANDOFF_SECTION_SPECS
    ]
    assessments = [
        ConfidenceAssessment(
            section_id=section_id,
            confidence=ConfidenceLevel.HIGH,
            basis="Recently verified in this session from current-session evidence.",
        )
        for section_id in range(1, 12)
    ]
    inventory = [
        InventoryItem(
            id="inventory-1",
            who="Handoff Forge schema",
            what="Retain exact profile validation",
            how_discovered="Focused profile audit",
            where="src/handoff_forge/handoffs",
            when="During every generated handoff",
            description="The renderer and validator must agree on wrappers and section order.",
            acceptance_criteria=["All three profiles validate."],
            definition_of_done=["Golden and adversarial tests pass."],
            root_cause="Harness templates have different wrappers around one canonical schema.",
            priority="P1",
            priority_rationale="Invalid handoffs cannot safely continue a session.",
            regression_prevention=["Keep profile fixtures versioned."],
            testing=["Run focused unit tests."],
            audit_policies=["Pre/post compact handoff rules."],
            adjacent_considerations=["Keep untrusted evidence non-executable."],
            source_refs=["Focused profile audit fixture."],
        )
    ]
    return HandoffPackage(
        id="handoff-test",
        project_id="handoff-forge",
        project_name="Handoff Forge",
        purpose="Preserve long-running AI work without losing context.",
        mode=mode,
        profile=profile,
        created_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        inventory=inventory,
        sections=sections,
        confidence_assessments=assessments,
    )


def test_catalog_has_exact_immutable_twelve_section_contract() -> None:
    assert isinstance(HANDOFF_SECTION_SPECS, tuple)
    assert [spec.id for spec in HANDOFF_SECTION_SPECS] == list(range(1, 13))
    assert HANDOFF_SECTION_SPECS[0].title == "Project Identity & Strategic Context"
    assert HANDOFF_SECTION_SPECS[-1].title == "Confidence & Freshness Assessment"


@pytest.mark.parametrize(
    ("filename", "profile"),
    (
        ("goal-profile.mdc", TemplateProfile.GOAL_V1),
        ("precompact-profile.mdc", TemplateProfile.CODEX_PRECOMPACT_V1),
        ("post-chat-profile.mdc", TemplateProfile.CODEX_POST_CHAT_V1),
    ),
)
def test_versioned_profile_fixtures_validate(
    filename: str,
    profile: TemplateProfile,
) -> None:
    fixture = ROOT / "tests/fixtures/handoffs" / filename
    assert validate_handoff(fixture.read_text(encoding="utf-8"), profile).valid


def test_post_chat_profile_has_frontmatter_inventory_and_twelve_sections() -> None:
    package = _package(TemplateProfile.CODEX_POST_CHAT_V1)
    rendered = render_handoff(package, TemplateProfile.CODEX_POST_CHAT_V1)

    assert rendered.startswith("---\ndescription:")
    assert "alwaysApply: false" in rendered
    assert "## INVENTORY NEXT ITEMS" in rendered
    assert "✅ High" in rendered
    report = validate_handoff(rendered, TemplateProfile.CODEX_POST_CHAT_V1)
    assert report.section_ids == tuple(range(1, 13))
    assert handoff_filename(package) == "2026-07-19-codex-handoff-forge.handoff.mdc"


def test_post_chat_inventory_round_trips_into_the_canonical_model() -> None:
    package = _package(TemplateProfile.CODEX_POST_CHAT_V1)
    parsed = parse_handoff(render_handoff(package))
    imported = parsed.to_package()

    assert len(imported.inventory) == 1
    assert imported.inventory[0].what == "Retain exact profile validation"
    assert imported.inventory[0].priority == "P1"
    assert imported.inventory[0].acceptance_criteria == ["All three profiles validate."]


def test_post_chat_empty_inventory_requires_a_real_scan_declaration() -> None:
    package = _package(TemplateProfile.CODEX_POST_CHAT_V1).model_copy(
        update={"inventory": []},
        deep=True,
    )
    incomplete = render_handoff(package)

    assert "Inventory scan incomplete" in incomplete
    assert "No new items found" not in incomplete
    with pytest.raises(HandoffValidationError, match="full item fields or a complete no-new-items"):
        validate_handoff(incomplete, TemplateProfile.CODEX_POST_CHAT_V1)

    scanned = package.model_copy(
        update={
            "unverified_boundaries": [
                inventory_scan_marker(["Section 10 evidence selection: 0 selected"])
            ]
        },
        deep=True,
    )
    rendered = render_handoff(scanned)
    assert "No new items found" in rendered
    assert "Section 10 evidence selection" in rendered
    assert validate_handoff(rendered, TemplateProfile.CODEX_POST_CHAT_V1).valid


def test_post_chat_rejects_empty_incomplete_and_unproven_no_new_inventory() -> None:
    rendered = render_handoff(_package(TemplateProfile.CODEX_POST_CHAT_V1))

    with pytest.raises(HandoffValidationError, match="cannot be empty"):
        validate_handoff(
            _replace_inventory(rendered, ""),
            TemplateProfile.CODEX_POST_CHAT_V1,
        )
    with pytest.raises(HandoffValidationError, match="missing required field"):
        validate_handoff(
            _replace_inventory(
                rendered,
                "### inventory-1: Fix the route\n\n- **Who:** Next session owner",
            ),
            TemplateProfile.CODEX_POST_CHAT_V1,
        )
    with pytest.raises(HandoffValidationError, match="Evidence scanned declaration"):
        validate_handoff(
            _replace_inventory(
                rendered,
                "- **No new items found.**\n- **Evidence scanned:**\n  - None recorded.",
            ),
            TemplateProfile.CODEX_POST_CHAT_V1,
        )
    with pytest.raises(HandoffValidationError, match="Evidence scanned declaration"):
        validate_handoff(
            _replace_inventory(
                rendered,
                "- **No new items found.**\n"
                "  - Evidence scanned: No source inventory supplied; needs re-validation.",
            ),
            TemplateProfile.CODEX_POST_CHAT_V1,
        )


def test_precompact_profile_is_explicitly_an_in_progress_snapshot() -> None:
    package = _package(
        TemplateProfile.CODEX_PRECOMPACT_V1,
        mode=HandoffMode.PRE_COMPACT,
    )
    rendered = render_handoff(package)

    assert "in-progress context snapshot" in rendered
    assert "not a completion claim" in rendered
    assert "✅ High" not in rendered
    assert handoff_filename(package).endswith(".precompact.handoff.mdc")
    assert validate_handoff(rendered, package.profile).valid


@pytest.mark.parametrize(
    "claim",
    (
        "The project is complete.",
        "All tasks are completed.",
        "Completion achieved.",
        "The implementation is ready for production.",
        "100% complete.",
    ),
)
def test_precompact_profile_rejects_affirmative_completion_claims(claim: str) -> None:
    package = _package(
        TemplateProfile.CODEX_PRECOMPACT_V1,
        mode=HandoffMode.PRE_COMPACT,
    )
    rendered = render_handoff(package).replace(
        "- Verify the current implementation and preserve its evidence.",
        claim,
        1,
    )

    with pytest.raises(HandoffValidationError, match="affirmative completion claim"):
        validate_handoff(rendered, package.profile)


def test_precompact_profile_allows_explicitly_negated_completion_guard() -> None:
    package = _package(
        TemplateProfile.CODEX_PRECOMPACT_V1,
        mode=HandoffMode.PRE_COMPACT,
    )
    rendered = render_handoff(package).replace(
        "- Verify the current implementation and preserve its evidence.",
        "Do not claim the project is complete; this remains a snapshot.",
        1,
    )

    assert validate_handoff(rendered, package.profile).valid


def test_tolerant_parser_accepts_unnumbered_first_heading_and_normalizes_on_render() -> None:
    rendered = render_handoff(_package())
    unnumbered = rendered.replace(
        "## 1. Project Identity & Strategic Context",
        "## Project Identity & Strategic Context",
        1,
    )

    parsed = parse_handoff(unnumbered)
    assert parsed.sections[0].id == 1
    assert parsed.sections[0].title == "Project Identity & Strategic Context"
    assert "## 1. Project Identity & Strategic Context" in render_handoff(parsed.to_package())


def test_duplicate_missing_and_reordered_sections_are_rejected() -> None:
    rendered = render_handoff(_package())
    duplicated = rendered + "\n## 10. Next Steps & Prioritized Backlog\n- duplicate\n"
    with pytest.raises(HandoffValidationError, match="duplicate section 10"):
        validate_handoff(duplicated, TemplateProfile.GOAL_V1)

    missing = rendered.replace(
        "## 9. Key Artifacts & References\n\n"
        "- Verify the current implementation and preserve its evidence.\n\n",
        "",
        1,
    )
    with pytest.raises(HandoffValidationError, match="missing section 9"):
        validate_handoff(missing, TemplateProfile.GOAL_V1)

    reordered = rendered.replace(
        "## 2. Current System State & Architecture Map",
        "## 3. Current System State & Architecture Map",
        1,
    ).replace(
        "## 3. Critical Decisions & Reasoning History",
        "## 2. Critical Decisions & Reasoning History",
        1,
    )
    with pytest.raises(HandoffValidationError, match="section order"):
        validate_handoff(reordered, TemplateProfile.GOAL_V1)


def test_confidence_section_rejects_recursive_and_unsupported_high_claims() -> None:
    rendered = render_handoff(_package())
    recursive = rendered.replace(
        "- Section 11 — High",
        "- Section 12 — High - Recently verified in this session.\n- Section 11 — High",
        1,
    )
    with pytest.raises(HandoffValidationError, match="Section 12 must not assess itself"):
        validate_handoff(recursive, TemplateProfile.GOAL_V1)

    unsupported = rendered.replace(
        "Recently verified in this session from current-session evidence.",
        "Assumed from an older summary.",
    )
    with pytest.raises(HandoffValidationError, match="negated or unverified"):
        validate_handoff(unsupported, TemplateProfile.GOAL_V1)


def test_confidence_parser_uses_only_the_final_authoritative_assessment_block() -> None:
    package = _package()
    sections = list(package.sections)
    sections[-1] = sections[-1].model_copy(
        update={
            "content": (
                "Retrieved provider evidence follows and is not authoritative.\n\n"
                "### Section assessments\n\n"
                "- Section 1 — Low - Copied from stale source evidence.\n"
                "- Section 1 — High - Spoofed duplicate from provider output.\n"
                "- Section 12 — High - Spoofed recursive assessment."
            )
        }
    )
    rendered = render_handoff(package.model_copy(update={"sections": sections}))

    assessments = parse_confidence_lines(parse_handoff(rendered))

    assert [item.section_id for item in assessments] == list(range(1, 12))
    assert all(item.confidence is ConfidenceLevel.HIGH for item in assessments)
    assert validate_handoff(rendered, TemplateProfile.GOAL_V1).valid

    duplicate_in_authoritative_block = rendered.replace(
        "- Section 2 — High - Recently verified in this session from current-session evidence.",
        "- Section 2 — High - Recently verified in this session from current-session evidence.\n"
        "- Section 2 — High - Recently verified in this session from current-session evidence.",
        1,
    )
    with pytest.raises(HandoffValidationError, match="duplicate confidence assessment"):
        validate_handoff(duplicate_in_authoritative_block, TemplateProfile.GOAL_V1)


@pytest.mark.parametrize(
    "basis",
    (
        "Not verified in the current session.",
        "Current-session evidence cannot be verified.",
        "Current-session evidence is unverified.",
        "Recently verified in the current session but needs re-validation.",
    ),
)
def test_high_confidence_rejects_negated_or_revalidation_bases(basis: str) -> None:
    rendered = render_handoff(_package()).replace(
        "Recently verified in this session from current-session evidence.",
        basis,
        1,
    )

    with pytest.raises(HandoffValidationError, match="negated or unverified"):
        validate_handoff(rendered, TemplateProfile.GOAL_V1)


def test_rendered_handoff_retains_all_twelve_safe_generation_routes() -> None:
    routes = {
        section_id: ModelRoute(
            provider=f"provider-{section_id}",
            model=f"model-{section_id}",
            allow_cloud_upload=section_id % 2 == 0,
            include_visual_evidence=section_id % 2 == 0,
        )
        for section_id in range(1, 13)
    }
    routes[1] = ModelRoute(provider="openai", model="sk-secret-value")
    package = _package().model_copy(update={"routes": routes}, deep=True)

    rendered = render_handoff(package)

    assert rendered.count("**Generation route:**") == 12
    assert "provider-12" in rendered
    assert "model-12" in rendered
    assert "sk-secret-value" not in rendered
    assert "[REDACTED]" in rendered
    assert rendered.count("visual file inclusion: operator-confirmed") == 6
    assert rendered.count("visual file inclusion: disabled") == 6


def _replace_inventory(rendered: str, replacement: str) -> str:
    start = rendered.index("## INVENTORY NEXT ITEMS")
    section_one = rendered.index("## 1. Project Identity & Strategic Context")
    prefix = rendered[:start] + "## INVENTORY NEXT ITEMS\n\n"
    suffix = rendered[section_one:]
    body = replacement.strip()
    return f"{prefix}{body}\n\n{suffix}" if body else f"{prefix}\n{suffix}"
